"""web_search 工具的单元测试。

覆盖：
1. `_format_search_results` 的引用格式（title/url/date）与正文回退（summary > snippet > content）。
2. `web_search` 的入参/返回契约：返回 str、空 query、失败降级文本。
3. `_deepseek_search` 的请求体（query/count/need_content）、Bearer 头与响应解析。
"""

from __future__ import annotations

from unittest import mock

import pytest

import importlib

ws = importlib.import_module("agent.tools.web_search")


def test_format_citation_and_summary():
    results = [
        {
            "title": "DeepSeek V4 发布",
            "url": "https://example.com/v4",
            "summary": "V4 是最新一代模型。",
            "snippet": "V4 已上线。",
            "content": None,
            "date": "2026-11-01",
        }
    ]
    out = ws._format_search_results(results)
    assert isinstance(out, str)
    assert "DeepSeek V4 发布" in out
    assert "https://example.com/v4" in out
    assert "V4 是最新一代模型。" in out  # 优先 summary
    assert "来源:" in out


def test_format_falls_back_summary_then_snippet_then_content():
    # summary 缺失 → 用 snippet
    a = ws._format_search_results(
        [{"title": "T", "url": "u", "summary": "", "snippet": "SNIP", "content": "FULL"}]
    )
    assert "SNIP" in a and "FULL" not in a
    # summary/snippet 都缺失 → 用 content
    b = ws._format_search_results(
        [{"title": "T", "url": "u", "summary": "", "snippet": "", "content": "FULL"}]
    )
    assert "FULL" in b


def test_format_empty_results():
    assert ws._format_search_results([]) == "没有搜索到任何内容。"


def test_format_skips_items_without_body():
    out = ws._format_search_results(
        [{"title": "T", "url": "u", "summary": "", "snippet": "", "content": None}]
    )
    assert out == "搜索结果为空。"


def test_web_search_empty_query():
    assert ws.web_search.func("   ") == "搜索失败：query 不能为空"


def test_web_search_success_returns_str(monkeypatch):
    monkeypatch.setattr(ws, "get_runtime_thread_id", lambda: None)
    monkeypatch.setattr(
        ws,
        "_deepseek_search",
        lambda query, count=3: [{"title": "T", "url": "https://x", "summary": "S", "content": None}],
    )
    out = ws.web_search.func("hello")
    assert isinstance(out, str)
    assert "https://x" in out
    assert "S" in out


def test_web_search_error_degrades_to_text(monkeypatch):
    def boom(query, count=3):
        raise RuntimeError("boom")

    monkeypatch.setattr(ws, "get_runtime_thread_id", lambda: None)
    monkeypatch.setattr(ws, "_deepseek_search", boom)
    out = ws.web_search.func("hello")
    assert out.startswith("搜索失败:")


def test_deepseek_search_payload_and_headers(monkeypatch):
    monkeypatch.setattr(ws, "_deepseek_search_base", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(
        ws,
        "_deepseek_headers",
        lambda: {"Authorization": "Bearer sk-test", "Content-Type": "application/json"},
    )

    fake_resp = mock.MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = ""
    fake_resp.json.return_value = {
        "results": [{"title": "T", "url": "u", "summary": "s", "content": None}]
    }

    fake_client = mock.MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_resp
    monkeypatch.setattr(ws.httpx, "Client", lambda *a, **k: fake_client)

    results = ws._deepseek_search("hello world")
    assert results == [{"title": "T", "url": "u", "summary": "s", "content": None}]

    posted = fake_client.post.call_args
    assert posted.kwargs["json"] == {"query": "hello world", "count": 3, "need_content": False}
    assert posted.kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_deepseek_search_http_error(monkeypatch):
    monkeypatch.setattr(ws, "_deepseek_search_base", lambda: "https://api.deepseek.com")
    monkeypatch.setattr(ws, "_deepseek_headers", lambda: {"Authorization": "Bearer sk-test"})

    fake_resp = mock.MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "server error"
    fake_client = mock.MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_resp
    monkeypatch.setattr(ws.httpx, "Client", lambda *a, **k: fake_client)

    with pytest.raises(RuntimeError):
        ws._deepseek_search("hello")
