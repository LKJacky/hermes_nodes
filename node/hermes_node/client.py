"""WebSocket client: register with the hub, stay connected, serve calls.

The client:
  1. connects to <hub>/ws?token=...
  2. sends a hello frame (device name, platform, capabilities)
  3. heartbeats every 15s
  4. executes {"type":"call"} frames against the local tools and replies

Reconnects forever with a 5s backoff, so it survives hub restarts and
network blips (launchd/systemd keep the process itself alive).
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import time

import websockets

from . import CAPABILITIES, __version__
from .tools import ALL_TOOLS

logger = logging.getLogger("hermes_node")


def _ws_uri(hub_url: str, token: str) -> str:
    uri = hub_url.strip().rstrip("/")
    if uri.startswith("http://"):
        uri = "ws://" + uri[len("http://"):]
    elif uri.startswith("https://"):
        uri = "wss://" + uri[len("https://"):]
    elif not uri.startswith("ws"):
        uri = "ws://" + uri
    sep = "&" if "?" in uri else "?"
    return f"{uri}/ws{sep}token={token}"


async def _run_tool(tool: str, args: dict) -> dict:
    fn = ALL_TOOLS.get(tool)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {tool!r} (have: {', '.join(ALL_TOOLS)})"}
    try:
        started = time.time()
        result = await asyncio.to_thread(fn, **args)
        if isinstance(result, dict):
            result = dict(result)
            result["duration_ms"] = int((time.time() - started) * 1000)
        return {"ok": True, "output": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _truncate(value, limit: int = 200) -> str:
    """JSON-serialize *value* and truncate to *limit* chars for log lines."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(+{len(text) - limit} chars)"


async def _heartbeat_loop(ws) -> None:
    while True:
        await asyncio.sleep(15)
        await ws.send(json.dumps({"type": "heartbeat", "ts": time.time()}))


async def _idle_watchdog(ws, last_call: list, idle_timeout: float) -> None:
    """Disconnect (and thus exit) after *idle_timeout* seconds without a call.

    Hot-plug semantics: a node usually connects on demand, serves a few
    calls, then drops off. The hub prunes the stale entry automatically.
    """
    while True:
        await asyncio.sleep(5)
        if time.time() - last_call[0] > idle_timeout:
            logger.info("idle for %.0fs (limit %.0fs) — disconnecting", idle_timeout, idle_timeout)
            await ws.close()
            return


async def _receive_loop(ws, last_call: list) -> None:
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") != "call":
            continue
        last_call[0] = time.time()
        call_id = msg.get("id")
        tool = str(msg.get("tool"))
        tool_args = msg.get("args") or {}
        logger.info("▶ call #%s tool=%s args=%s", call_id, tool, _truncate(tool_args))
        resp = await _run_tool(tool, tool_args)
        ok = resp.get("ok")
        summary = resp.get("output") if ok else resp.get("error")
        logger.info(
            "✔ call #%s done ok=%s result=%s",
            call_id, ok, _truncate(summary, 300),
        )
        resp["type"] = "result"
        resp["id"] = call_id
        await ws.send(json.dumps(resp, ensure_ascii=False))


async def run(
    hub_url: str,
    token: str,
    device: str,
    *,
    once: bool = False,
    idle_timeout: float = 0.0,
) -> None:
    uri = _ws_uri(hub_url, token)
    logger.info(
        "hermes-node v%s connecting to %s as device %r",
        __version__, uri.split("?", 1)[0], device,
    )
    last_call = [time.time()]
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "device": device,
                            "platform": platform.platform(),
                            "capabilities": list(CAPABILITIES),
                        }
                    )
                )
                logger.info("connected to hub as %r", device)
                coros = [_heartbeat_loop(ws), _receive_loop(ws, last_call)]
                if idle_timeout and idle_timeout > 0:
                    coros.append(_idle_watchdog(ws, last_call, idle_timeout))
                # FIRST_COMPLETED so the idle watchdog can tear the connection
                # down without waiting for the heartbeat/receive loops to end.
                tasks = [asyncio.ensure_future(c) for c in coros]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in pending:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("connection lost: %s", exc)
        if once:
            return
        await asyncio.sleep(5)
