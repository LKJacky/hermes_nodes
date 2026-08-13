"""hermes-node-hub plugin — dynamic device registry for Hermes.

Install: copy this directory to ~/.hermes/plugins/hermes-node-hub/ (or run
`hermes plugins install <git-url>` once the repo has a remote) and restart
Hermes. The server starts lazily on the first tool call.

Env config (see config.py): HERMES_NODE_HUB_HOST / _PORT / _TOKEN / ...
"""
from __future__ import annotations

import logging

from tools.registry import tool_error, tool_result

from . import config
from .registry import get_registry
from .server import call_device, start_server

logger = logging.getLogger("hermes_node_hub")

_NODES_LIST_SCHEMA = {
    "name": "nodes_list",
    "description": "List all hermes-node devices registered with the hub: name, node_id, platform, capabilities, online status. Optionally filter by device name.",
    "parameters": {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Optional device name filter (exact match on name or node_id).",
            },
        },
        "required": [],
    },
}

_NODE_CALL_SCHEMA = {
    "name": "node_call",
    "description": "Call a tool on a specific registered node device (e.g. exec_command, read_file, write_file, sys_info). The device must be online.",
    "parameters": {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Device name or node_id (see nodes_list).",
            },
            "tool": {
                "type": "string",
                "description": "Tool to invoke on the node.",
            },
            "args": {
                "type": "object",
                "description": "Arguments passed to the tool (tool-specific).",
            },
            "timeout": {
                "type": "integer",
                "description": "Per-call timeout in seconds (default 120).",
            },
        },
        "required": ["device", "tool"],
    },
}

_NODES_RUN_SCHEMA = {
    "name": "nodes_run",
    "description": "Run a shell command on a registered device (convenience wrapper around node_call -> exec_command).",
    "parameters": {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Device name or node_id (see nodes_list).",
            },
            "command": {
                "type": "string",
                "description": "Shell command to execute on the device.",
            },
            "timeout": {
                "type": "integer",
                "description": "Per-call timeout in seconds (default 120).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory on the device (default ~).",
            },
        },
        "required": ["device", "command"],
    },
}

_NODES_FANOUT_SCHEMA = {
    "name": "nodes_fanout",
    "description": "Call the same tool with the same args on every online device in parallel and collect all results.",
    "parameters": {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Tool to invoke on every online node.",
            },
            "args": {
                "type": "object",
                "description": "Arguments passed to the tool on each node.",
            },
            "timeout": {
                "type": "integer",
                "description": "Per-call timeout in seconds (default 120).",
            },
        },
        "required": ["tool"],
    },
}


def _resolve_or_error(device: str):
    node_id = get_registry().resolve(device)
    if node_id is None:
        return None, tool_error(
            f"device {device!r} is not registered. Run nodes_list to see registered devices."
        )
    if not get_registry().is_online(node_id):
        return None, tool_error(f"device {device!r} is offline (no heartbeat).")
    return node_id, None


def _handle_nodes_list(args: dict) -> str:
    start_server()
    devices = get_registry().list_public()
    name = (args or {}).get("device")
    if name:
        devices = [d for d in devices if d["name"] == name or d["node_id"] == name]
    return tool_result(devices=devices, count=len(devices))


def _handle_node_call(args: dict) -> str:
    start_server()
    device = (args or {}).get("device")
    tool = (args or {}).get("tool")
    if not device or not tool:
        return tool_error("both 'device' and 'tool' are required")
    node_id, err = _resolve_or_error(device)
    if err:
        return err
    result = call_device(node_id, tool, (args or {}).get("args") or {}, (args or {}).get("timeout"))
    if not result.get("ok"):
        return tool_error(result.get("error", "call failed"))
    return tool_result(**result)


def _handle_nodes_run(args: dict) -> str:
    start_server()
    device = (args or {}).get("device")
    command = (args or {}).get("command")
    if not device or not command:
        return tool_error("both 'device' and 'command' are required")
    node_id, err = _resolve_or_error(device)
    if err:
        return err
    timeout = (args or {}).get("timeout")
    result = call_device(
        node_id,
        "exec_command",
        {
            "cmd": command,
            "timeout": timeout or 120,
            "cwd": (args or {}).get("cwd", "~"),
        },
        timeout,
    )
    if not result.get("ok"):
        return tool_error(result.get("error", "call failed"))
    return tool_result(**result)


def _handle_nodes_fanout(args: dict) -> str:
    start_server()
    tool = (args or {}).get("tool")
    if not tool:
        return tool_error("'tool' is required")
    tool_args = (args or {}).get("args") or {}
    timeout = (args or {}).get("timeout")
    results = {}
    failures = 0
    for dev in get_registry().list_public():
        if not dev["online"]:
            continue
        res = call_device(dev["node_id"], tool, tool_args, timeout)
        if res.get("ok"):
            results[dev["name"]] = res
        else:
            results[dev["name"]] = {"ok": False, "error": res.get("error")}
            failures += 1
    return tool_result(results=results, devices=len(results), failures=failures)


_TOOLS = (
    ("nodes_list", _NODES_LIST_SCHEMA, _handle_nodes_list, "📡"),
    ("node_call", _NODE_CALL_SCHEMA, _handle_node_call, "🖥️"),
    ("nodes_run", _NODES_RUN_SCHEMA, _handle_nodes_run, "⚡"),
    ("nodes_fanout", _NODES_FANOUT_SCHEMA, _handle_nodes_fanout, "🌐"),
)


def register(ctx) -> None:
    """Called once by the Hermes plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="nodes",
            schema=schema,
            handler=handler,
            description=schema.get("description", ""),
            emoji=emoji,
        )
    # Eager start: devices should be able to register at any time, not only
    # after the agent first calls a nodes_* tool. start_server() is idempotent
    # and skips if another process already holds the port.
    start_server()
    logger.info(
        "hermes-node-hub plugin ready. Hub URL: http://%s:%s (token set: %s)",
        config.hub_host(),
        config.hub_port(),
        bool(config.hub_token()),
    )
