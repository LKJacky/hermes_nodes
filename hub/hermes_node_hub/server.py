"""HTTP + WebSocket registration server for hermes-node-hub.

Runs in a background daemon thread inside the Hermes process.

Endpoints
    POST /register            optional pre-flight: validates token, returns node_id
    GET  /status              registered devices (debugging)
    WS   /ws?token=...        persistent node connection: hello / heartbeat / call / result

Wire format (see docs/protocol.md):
    node -> hub  {"type":"hello","device":"devbox","platform":"...","capabilities":[...]}
    node -> hub  {"type":"heartbeat","ts":123.4}
    hub  -> node {"type":"call","id":"<hex>","tool":"exec_command","args":{...}}
    node -> hub  {"type":"result","id":"<hex>","ok":true,"output":{...}}
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import secrets
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import config
from .registry import get_registry

logger = logging.getLogger("hermes_node_hub.server")

app = FastAPI(title="hermes-node-hub", version="0.1.0")

# call_id -> threading.Future (set from the asyncio loop on result frames)
_pending: Dict[str, concurrent.futures.Future] = {}
# node_id -> (websocket, event loop) for cross-thread sends
_conns: Dict[str, Tuple[WebSocket, asyncio.AbstractEventLoop]] = {}
_conns_lock = threading.Lock()


def _token_ok(token: Optional[str]) -> bool:
    expected = config.hub_token()
    if not expected:  # no token configured -> loopback-only deployment
        return True
    return secrets.compare_digest(token or "", expected)


@app.get("/status")
async def status() -> dict:
    return {"ok": True, "devices": get_registry().list_public()}


@app.post("/register")
async def register(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not _token_ok(body.get("token")):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    client = req.client.host if req.client else "?"
    node_id = get_registry().register(
        name=str(body.get("device") or "unnamed"),
        platform=str(body.get("platform") or "unknown"),
        capabilities=list(body.get("capabilities") or []),
        addr=client,
    )
    return JSONResponse({"ok": True, "node_id": node_id, "hub_version": "0.1.0"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not _token_ok(ws.query_params.get("token")):
        await ws.close(code=4401)
        return
    await ws.accept()
    node_id: Optional[str] = None
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=15)
        if first.get("type") != "hello" or not first.get("device"):
            await ws.close(code=4400)
            return
        node_id = get_registry().register(
            name=str(first["device"]),
            platform=str(first.get("platform") or "unknown"),
            capabilities=list(first.get("capabilities") or []),
            addr=ws.client.host if ws.client else "?",
        )
        loop = asyncio.get_running_loop()
        with _conns_lock:
            _conns[node_id] = (ws, loop)
        logger.info("node connected: %s (%s)", first["device"], node_id)

        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "heartbeat":
                get_registry().heartbeat(node_id)
            elif mtype == "result":
                call_id = str(msg.get("id"))
                fut = _pending.pop(call_id, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            # unknown frame types are ignored (forward compatible)
    except (WebSocketDisconnect, asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
        logger.info("node disconnected: %s (%s)", node_id, type(exc).__name__)
    finally:
        if node_id is not None:
            with _conns_lock:
                _conns.pop(node_id, None)


def _ws_send(node_id: str, payload: dict) -> bool:
    """Send a frame to a node from any thread. Returns False if not connected."""
    with _conns_lock:
        conn = _conns.get(node_id)
        if conn is None:
            return False
        ws, loop = conn
    try:
        asyncio.run_coroutine_threadsafe(
            ws.send_text(json.dumps(payload, ensure_ascii=False)), loop
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def call_device(node_id: str, tool: str, args: dict, timeout: Optional[int] = None) -> dict:
    """Blocking remote call, safe to invoke from sync tool-handler threads.

    Returns the node's result frame: {"ok": True, "output": ...} or
    {"ok": False, "error": ...}.
    """
    timeout = timeout or config.call_timeout()
    call_id = uuid.uuid4().hex
    fut: concurrent.futures.Future = concurrent.futures.Future()
    _pending[call_id] = fut
    if not _ws_send(node_id, {"type": "call", "id": call_id, "tool": tool, "args": args or {}, "timeout": timeout}):
        _pending.pop(call_id, None)
        return {"ok": False, "error": f"device {node_id!r} is not connected"}
    try:
        frame = fut.result(timeout=timeout)
        return frame if isinstance(frame, dict) else {"ok": False, "error": "malformed result"}
    except concurrent.futures.TimeoutError:
        _pending.pop(call_id, None)
        return {"ok": False, "error": f"device {node_id!r} did not respond within {timeout}s"}


_server_thread: Optional[threading.Thread] = None
_start_lock = threading.Lock()


def _port_open(host: str, port: int) -> bool:
    """True if something already listens on host:port (another Hermes process runs the hub)."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_server() -> bool:
    """Start the uvicorn server in a daemon thread. Idempotent.

    Safe to call from plugin load (eager) and from tool handlers (lazy):
    if another Hermes process already bound the port, we detect it and skip
    instead of crashing on a double bind.
    """
    global _server_thread
    with _start_lock:
        if _server_thread is not None and _server_thread.is_alive():
            return True
        if _port_open(config.hub_host(), config.hub_port()):
            logger.info(
                "hermes-node-hub already listening on %s:%s — not starting a second server",
                config.hub_host(),
                config.hub_port(),
            )
            return True

        def _run() -> None:
            try:
                uvicorn.run(
                    app,
                    host=config.hub_host(),
                    port=config.hub_port(),
                    log_level="warning",
                    access_log=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("hermes-node-hub server exited: %s", exc)

        _server_thread = threading.Thread(
            target=_run,
            name="hermes-node-hub",
            daemon=True,
        )
        _server_thread.start()
        return True
