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


async def _heartbeat_loop(ws) -> None:
    while True:
        await asyncio.sleep(15)
        await ws.send(json.dumps({"type": "heartbeat", "ts": time.time()}))


async def _receive_loop(ws) -> None:
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") != "call":
            continue
        resp = await _run_tool(str(msg.get("tool")), msg.get("args") or {})
        resp["type"] = "result"
        resp["id"] = msg.get("id")
        await ws.send(json.dumps(resp, ensure_ascii=False))


async def run(hub_url: str, token: str, device: str, *, once: bool = False) -> None:
    uri = _ws_uri(hub_url, token)
    logger.info(
        "hermes-node v%s connecting to %s as device %r",
        __version__, uri.split("?", 1)[0], device,
    )
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
                await asyncio.gather(_heartbeat_loop(ws), _receive_loop(ws))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("connection lost: %s", exc)
        if once:
            return
        await asyncio.sleep(5)
