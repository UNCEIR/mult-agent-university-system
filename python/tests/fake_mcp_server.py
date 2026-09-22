# -*- coding: utf-8 -*-
"""假 MCP 服务器 — 供 mcp_client 单测（不真连外部服务）。

以工具注入方式模拟三个服务器（tavily/jimeng/e2b）的工具集，
绕过真实 streamable_http 连接，验证注册/发现/调用/熔断/降级链。
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def search(query: str, max_results: int = 5) -> str:
    """假搜索工具。"""
    return f"results for {query} ({max_results})"


@tool
def generate_image(prompt: str) -> str:
    """假生图工具。"""
    return '{"url": "https://fake.invalid/img.png"}'


@tool
def execute_code(code: str, language: str = "python", timeout: int = 30) -> str:
    """假执行工具。"""
    return "ok"


def install_fake_tools(client, *, fail_on_call: bool = False):
    """向 mcp client 注入假服务器配置与工具（等价于 connect 成功）。"""
    client.register_server("tavily", {"transport": "streamable_http", "url": "https://fake.invalid/tavily", "api_key_env": "", "namespace": "search"})
    client.register_server("jimeng", {"transport": "streamable_http", "url": "https://fake.invalid/jimeng", "api_key_env": "", "namespace": "image"})
    client.register_server("e2b", {"transport": "streamable_http", "url": "https://fake.invalid/e2b", "api_key_env": "", "namespace": "code"})
    client._connections["tavily"] = object()
    client._connections["jimeng"] = object()
    client._connections["e2b"] = object()

    search_tool = search

    if fail_on_call:

        async def _boom(*a, **k):
            """fake server down."""
            raise RuntimeError("fake server down")

        _boom.__name__ = "search"
        search_tool = tool(_boom)

    client._tools["tavily"] = [search_tool]
    client._tools["jimeng"] = [generate_image]
    client._tools["e2b"] = [execute_code]
    return client
