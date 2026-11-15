"""联网搜索工具。

该工具用于在本地仓库上下文不足时，补充公开网络资料，例如框架文档、第三方库变更说明、
错误信息背景和公开技术资料。工具本身只返回搜索摘要，不替代本地代码分析。

外部搜索依赖 DeepSeek Search API（POST {DEEPSEEK_BASE_URL}/v1/tools/search）。
工具内直接使用 httpx 发起 HTTP 调用，返回结构化 results（title/url/snippet/summary/
content/date），再格式化为带引用的文本摘要交给模型。

对外契约保持不变：`web_search(query: str) -> str`，返回带引用的搜索结果文本；
失败时返回明确错误信息。底层供应商从智谱平替为 DeepSeek，调用方无感知。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from langchain_core.tools import tool

from agent.core.events import record_event
from agent.env_utils import get_env, require_env
from agent.tools.github_api import mask_token
from agent.tools.runtime_context import get_runtime_thread_id

logger = logging.getLogger("agent.run.web_search")

# 搜索结果默认条数，控制外部结果数量和模型上下文体积。
_SEARCH_COUNT = 3


def _deepseek_search_base() -> str:
    """解析 DeepSeek 服务地址，去掉尾部斜杠，复用模型接入同一套 DEEPSEEK_BASE_URL。"""

    return get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")


def _deepseek_headers() -> dict[str, str]:
    """构造 DeepSeek Search API 请求头。

    DeepSeek 使用 `Authorization: Bearer <api_key>` 认证，与模型接入共用同一个
    DEEPSEEK_API_KEY。Key 从环境变量读取，绝不硬编码。
    """

    return {
        "Authorization": f"Bearer {require_env('DEEPSEEK_API_KEY')}",
        "Content-Type": "application/json",
    }


def _deepseek_search(query: str, *, count: int = _SEARCH_COUNT) -> list[dict[str, Any]]:
    """调用 DeepSeek Search API，返回结构化 results 列表。

    智谱 → DeepSeek 参数映射：
    - search_query               → query（字段名不同，语义相同）
    - count                      → count
    - search_engine="search_pro" → 忽略：DeepSeek 只有自家搜索，无引擎选择参数
    - search_recency_filter      → 忽略：DeepSeek 未提供时间/新鲜度过滤参数
    - （新增）need_content=False → content 字段置空，不拉取可能存在的完整正文；
      snippet（短摘要）已足够补充背景，控制上下文体积

    Args:
        query: 搜索关键词或问题。
        count: 期望返回的结果条数上限。

    Returns:
        结构化结果列表，每个元素包含 title/url/snippet/summary/content/date/rerank_score。

    Raises:
        RuntimeError: 请求失败、返回非 2xx、或响应结构不符合契约。
    """

    url = f"{_deepseek_search_base()}/v1/tools/search"
    payload = {"query": query, "count": count, "need_content": False}
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=_deepseek_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"DeepSeek 搜索请求失败: {mask_token(str(exc))}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"DeepSeek 搜索失败: {response.status_code} {mask_token(response.text)}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("DeepSeek 搜索返回非 JSON 响应") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise RuntimeError(f"DeepSeek 搜索返回结构异常: {mask_token(str(data)[:200])}")
    return results


def _format_search_results(results: list[dict[str, Any]]) -> str:
    """把结构化结果格式化为带引用的文本摘要。

    每条结果按「标题 + 摘要 + 来源 URL」输出，方便模型给出处、
    也能二次 fetch_url 深读。正文优先级 snippet > summary > content：
    snippet 是短摘要、summary 是较完整正文，优先 snippet 控制上下文体积；
    二者都缺失时才回退 content。
    """

    if not results:
        return "没有搜索到任何内容。"

    blocks: list[str] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        body = (
            str(item.get("snippet") or "").strip()
            or str(item.get("summary") or "").strip()
            or str(item.get("content") or "").strip()
        )
        url = str(item.get("url") or "").strip()
        date = str(item.get("date") or "").strip()[:10]
        if not body:
            continue
        parts = [f"{index}. {title}" if title else f"{index}. 搜索结果"]
        parts.append(body)
        source = url
        if date:
            source = f"{url}（{date}）" if url else f"日期: {date}"
        if source:
            parts.append(f"来源: {source}")
        blocks.append("\n".join(parts))
    if not blocks:
        return "搜索结果为空。"
    return "\n\n".join(blocks)


@tool("web_search", parse_docstring=True)
def web_search(query: str) -> str:
    """使用 DeepSeek Search API 进行联网搜索。

    适用于需要外部资料支撑的任务，例如查询最新框架文档、第三方库用法、
    API 变更说明、行业资料或错误信息背景。不要搜索密钥、token、私有仓库内容。

    Args:
        query: 需要搜索的关键词或问题；超过 128 个字符会被截断。

    Returns:
        带引用的搜索结果摘要文本；失败时返回明确错误信息。
    """

    thread_id = get_runtime_thread_id()
    # 压缩空白，避免模型生成多行 query 或带大量空格影响搜索质量。
    normalized_query = " ".join((query or "").split())
    if not normalized_query:
        return "搜索失败：query 不能为空"
    # DeepSeek Search API 要求 query 为 1~128 个 Unicode 字符，超出会被官方拒绝（400）。
    # 这里截断到 128，避免模型偶发超长 query 导致搜索整体失败。
    normalized_query = normalized_query[:128]

    if thread_id:
        # 写入工具调用事件，前端可以展示“正在联网搜索”的运行过程。
        record_event(
            thread_id,
            f"web_search:{normalized_query[:80]}",
            "联网搜索资料",
            kind="fetch",
            status="in_progress",
            detail=json.dumps({"query": normalized_query}, ensure_ascii=False),
        )
    try:
        results = _deepseek_search(normalized_query)
        output = _format_search_results(results)
        if thread_id:
            # 事件详情只记录预览片段，避免把大量搜索结果重复写入事件表。
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="completed",
                detail=json.dumps(
                    {"query": normalized_query, "result_preview": output[:1200]},
                    ensure_ascii=False,
                ),
            )
        return output
    except Exception as exc:
        # 外部 API 错误、网络异常、SDK 异常都返回可读文本，让模型可以继续降级处理。
        error = mask_token(str(exc))
        logger.warning("联网搜索失败：query=%s error=%s", normalized_query, error)
        if thread_id:
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="error",
                detail=json.dumps({"query": normalized_query, "error": error}, ensure_ascii=False),
            )
        return f"搜索失败: {error}"
