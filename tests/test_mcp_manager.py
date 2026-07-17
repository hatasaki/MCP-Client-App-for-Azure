from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.mcp_manager import MCPManager, OAuthCallbackBroker, _callback_id


async def first_text(manager: MCPManager, qualified_id: str, arguments: dict) -> str:
    contents = await manager.call_tool(qualified_id, arguments)
    return contents[0].text


@pytest.mark.asyncio
async def test_stdio_registry_qualifies_same_named_tools_and_invokes(tmp_path: Path):
    script = Path(__file__).parent / "fixtures" / "mcp_test_server.py"
    manager = await MCPManager([]).__aenter__()
    try:
        first = await manager.add_server({
            "id": "server-a",
            "name": "Server A",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(script)],
            "cwd": str(tmp_path),
            "env": {"TEST_SERVER_LABEL": "A"},
        })
        second = await manager.add_server({
            "id": "server-b",
            "name": "Server B",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(script)],
            "env": {"TEST_SERVER_LABEL": "B"},
        })

        assert {item["qualifiedId"] for item in first} == {
            "server-a:echo",
            "server-a:current_directory",
        }
        assert {item["qualifiedId"] for item in second} == {
            "server-b:echo",
            "server-b:current_directory",
        }
        model_names = [item["name"] for item in manager.function_defs]
        assert len(model_names) == len(set(model_names))
        assert await first_text(manager, "server-a:echo", {"value": "hello"}) == "A:hello"
        assert await first_text(manager, "server-b:echo", {"value": "hello"}) == "B:hello"
        assert await first_text(manager, "server-a:current_directory", {}) == str(tmp_path)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_replace_and_remove_server(tmp_path: Path, monkeypatch):
    script = Path(__file__).parent / "fixtures" / "mcp_test_server.py"
    manager = await MCPManager([]).__aenter__()
    config = {
        "id": "replace-me",
        "name": "Replace Me",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script)],
        "env": {"TEST_SERVER_LABEL": "old"},
    }
    try:
        await manager.add_server(config)
        assert await first_text(manager, "replace-me:echo", {"value": "x"}) == "old:x"

        rebuild = manager._rebuild_function_indexes
        failed = False

        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("index failure")
            rebuild()

        monkeypatch.setattr(manager, "_rebuild_function_indexes", fail_once)
        with pytest.raises(RuntimeError, match="index failure"):
            await manager.add_server({**config, "env": {"TEST_SERVER_LABEL": "discarded"}})
        assert await first_text(manager, "replace-me:echo", {"value": "x"}) == "old:x"

        monkeypatch.setattr(manager, "_rebuild_function_indexes", rebuild)
        await manager.add_server({**config, "env": {"TEST_SERVER_LABEL": "new"}})
        assert await first_text(manager, "replace-me:echo", {"value": "x"}) == "new:x"
        await manager.remove_server("replace-me")
        assert manager.function_defs == []
        with pytest.raises(KeyError):
            manager.get_function("replace-me:echo")
    finally:
        await manager.close()


def test_sse_is_streamable_http_alias(monkeypatch):
    # Constructor behavior is covered without making a network connection.
    manager = MCPManager([])
    from agent_framework import MCPStreamableHTTPTool

    created = []

    async def fake_connect(self):
        created.append(self)

    async def fake_close(self):
        return None

    monkeypatch.setattr(MCPStreamableHTTPTool, "connect", fake_connect)
    monkeypatch.setattr(MCPStreamableHTTPTool, "close", fake_close)

    async def exercise():
        try:
            await manager.add_server({
                "id": "legacy-label",
                "name": "Legacy label",
                "transport": "sse",
                "url": "https://example.test/mcp",
            })
            assert created
            assert isinstance(created[0], MCPStreamableHTTPTool)
            assert manager.server_configs()[0]["transport"] == "sse"
        finally:
            await manager.close()

    import asyncio

    asyncio.run(exercise())


@pytest.mark.asyncio
async def test_callback_broker_routes_by_server():
    broker = OAuthCallbackBroker()
    first = __import__("asyncio").create_task(broker.wait("a"))
    second = __import__("asyncio").create_task(broker.wait("b"))
    await __import__("asyncio").sleep(0)

    assert broker.resolve("b", "code-b", "state-b")
    assert broker.resolve("a", "code-a", "state-a")
    assert await first == ("code-a", "state-a")
    assert await second == ("code-b", "state-b")


def test_callback_id_is_path_safe_and_collision_resistant():
    first = _callback_id("team/server with spaces")
    second = _callback_id("team-server with spaces")

    assert all(character.isalnum() or character == "_" for character in first)
    assert first != second
