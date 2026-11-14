"""GitHub API 访问封装。

本模块只负责与 GitHub HTTP API 和仓库 URL 解析相关的低层能力：
解析仓库地址、读取访问令牌、创建 Pull Request、发布 PR 评论、复用重复 PR。

上层 LangChain/DeepAgents 工具定义在 `github_tools.py` 中。
这样的分层可以让 API 调用逻辑脱离模型工具协议，便于单元测试、复用和错误处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from agent.env_utils import get_env


@dataclass(frozen=True)
class GitHubRepo:
    """标准化后的 GitHub 仓库信息。

    owner 和 repo 用于调用 GitHub API；clone_url 用于 Git clone/push 等本地命令。
    使用 frozen dataclass 可以避免解析后的仓库信息在调用链中被意外修改。
    """

    owner: str
    repo: str
    clone_url: str


def parse_github_repo_url(repo_url: str) -> GitHubRepo:
    """解析 GitHub 仓库 URL。

    Args:
        repo_url: 用户输入或任务配置中的 GitHub 仓库地址，支持带 `.git` 后缀的 HTTPS 地址。

    Returns:
        标准化后的 `GitHubRepo`。

    Raises:
        ValueError: URL 不是 github.com 域名，或路径中无法解析出 owner/repo。
    """

    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"github.com", "www.github.com"}:
        raise ValueError("当前仅支持 github.com 仓库地址")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"无法解析 GitHub 仓库地址: {repo_url}")
    owner = parts[0]
    # 统一去掉 `.git` 后缀，API 路径使用纯 repo 名，clone_url 再补回标准后缀。
    repo = re.sub(r"\.git$", "", parts[1])
    return GitHubRepo(owner=owner, repo=repo, clone_url=f"https://github.com/{owner}/{repo}.git")


def normalize_github_repo_url(repo_url: str) -> str:
    """把 GitHub 仓库地址规范化为不含 token 的标准 HTTPS clone URL。"""

    return parse_github_repo_url(repo_url).clone_url


def get_github_token() -> str:
    """读取 GitHub 个人访问令牌（PAT）。

    令牌只用于 API 请求头或 Git askpass 环境，不应拼接到日志或命令文本中。
    """

    token = get_env("GITHUB_TOKEN").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: GITHUB_TOKEN")
    return token


def mask_token(text: str) -> str:
    """对文本中的 GitHub Token 做脱敏。

    API 错误、Git 输出和异常信息可能包含访问令牌。
    所有写日志或返回给模型的外部错误文本都应经过该函数处理。
    """

    masked = text
    token = get_env("GITHUB_TOKEN").strip()
    if token:
        masked = masked.replace(token, "***")
    return masked


def _api_base() -> str:
    """读取 GitHub API base url，默认官方 API 地址。"""

    return get_env("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")


def _headers(token: str) -> dict[str, str]:
    """构造 GitHub REST API 请求头。

    GitHub 使用 `Authorization: Bearer <token>` 认证，并建议携带
    `X-GitHub-Api-Version` 以固定 API 版本行为。
    """

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_get(path: str, *, params: dict | None = None) -> dict | list:
    """执行 GitHub GET 请求，集中处理认证和错误抛出。"""

    url = f"{_api_base()}{path}"
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_headers(get_github_token()), params=params)
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub API 读取失败: {response.status_code} {response.text}")
    return response.json()


def list_open_pull_requests(*, owner: str, repo: str, head: str) -> list:
    """列出某个 head 分支的 open Pull Request。

    GitHub 对相同 head/base 的 PR 创建不是幂等的，会返回 422。为了复用已有 PR，
    这里先按 head 查询 open PR，命中则复用。
    """

    data = _github_get(f"/repos/{owner}/{repo}/pulls", params={"state": "open", "head": head})
    return data if isinstance(data, list) else [data]


def create_pull_request(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> dict:
    """调用 GitHub API 创建 Pull Request。

    Args:
        owner: GitHub 仓库 owner。
        repo: GitHub 仓库名称。
        head: 源分支名称。
        base: 目标分支名称。
        title: PR 标题。
        body: PR 描述。

    Returns:
        GitHub API 返回的 PR JSON；如果检测到重复 PR，则返回带 `reused=True` 的兼容结构。

    Raises:
        RuntimeError: API 返回失败且无法识别为可复用 PR。
    """

    head_ref = head if ":" in head else f"{owner}:{head}"

    # 相同 head/base 的 PR 已存在时复用，保证工具具备幂等语义。
    try:
        for pr in list_open_pull_requests(owner=owner, repo=repo, head=head_ref):
            if isinstance(pr, dict) and (pr.get("base") or {}).get("ref") == base:
                url = pr.get("html_url") or pr.get("url") or ""
                return {
                    **pr,
                    "html_url": url,
                    "url": url,
                    "reused": True,
                    "message": "已复用相同源分支和目标分支的现有 Pull Request",
                }
    except RuntimeError:
        # 查询失败不应阻断创建流程，退化为直接创建；若确有重复，下方会收到 422 并抛出。
        pass

    url = f"{_api_base()}/repos/{owner}/{repo}/pulls"
    payload = {"title": title, "head": head_ref, "base": base, "body": body}
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_headers(get_github_token()), json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub 创建 PR 失败: {response.status_code} {response.text}")
    return response.json()


def get_pull_request(*, owner: str, repo: str, number: int) -> dict:
    """读取 GitHub Pull Request 详情。"""

    data = _github_get(f"/repos/{owner}/{repo}/pulls/{number}")
    return data if isinstance(data, dict) else {"items": data}


def list_pull_request_commits(*, owner: str, repo: str, number: int) -> list:
    """读取 GitHub Pull Request 的提交列表。"""

    data = _github_get(f"/repos/{owner}/{repo}/pulls/{number}/commits")
    return data if isinstance(data, list) else [data]


def list_pull_request_files(*, owner: str, repo: str, number: int) -> list:
    """读取 GitHub Pull Request 的文件变更列表。"""

    data = _github_get(f"/repos/{owner}/{repo}/pulls/{number}/files")
    return data if isinstance(data, list) else [data]


def list_pull_request_comments(*, owner: str, repo: str, number: int) -> list:
    """读取 GitHub Pull Request 的普通评论列表（issue comments）。"""

    data = _github_get(f"/repos/{owner}/{repo}/issues/{number}/comments")
    return data if isinstance(data, list) else [data]


def post_pr_comment(*, owner: str, repo: str, number: int, body: str) -> dict:
    """调用 GitHub API 向 Pull Request 发布普通评论。

    GitHub 中 PR 本质也是 issue，因此普通评论走 `/issues/{number}/comments`。
    """

    url = f"{_api_base()}/repos/{owner}/{repo}/issues/{number}/comments"
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_headers(get_github_token()), json={"body": body})
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub 发布 PR 评论失败: {response.status_code} {response.text}")
    return response.json()
