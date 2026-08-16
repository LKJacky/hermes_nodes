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
    "description": "List all hermes-node devices registered with the hub: name, node_id, platform, capabilities, online status. Stale offline devices (no heartbeat for HERMES_NODE_HUB_OFFLINE_TTL, default 300s) are pruned automatically. Optionally filter by device name.",
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

_NODES_PRUNE_SCHEMA = {
    "name": "nodes_prune",
    "description": "Manually prune offline devices from the registry: remove devices whose last heartbeat is older than ttl seconds (default HERMES_NODE_HUB_OFFLINE_TTL = 300). Returns how many were removed. Useful for hot-plugged nodes that disconnected and will not come back.",
    "parameters": {
        "type": "object",
        "properties": {
            "ttl": {
                "type": "integer",
                "description": "Prune devices idle for more than this many seconds (default 300). Pass 0 to remove every offline device immediately.",
            },
        },
        "required": [],
    },
}

_NODE_CALL_SCHEMA = {
    "name": "node_call",
    "description": "Call a tool on a specific registered node device. Available tools: exec_command(cmd, timeout, cwd) run a shell command; read_file(path, max_bytes) read a text file; write_file(path, content) write a text file; sys_info() system snapshot; send_file(path, content) write a text file (content is a local path the hub reads by default). The device must be online.",
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
    "description": "Call the same tool with the same args on every online device in parallel and collect all results. Tools per device: exec_command, read_file, write_file, sys_info, send_file.",
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

_SEND_FILE_SCHEMA = {
    "name": "send_file",
    "description": (
        "Write a local file (on this hub/agent side) to a remote node. "
        "content is treated as a LOCAL FILE PATH by default: the hub reads "
        "that file and writes its contents to path on the node. Pass "
        "extra={'path_replace': []} to write content as inline text instead. "
        "Use for docs/scripts/configs/text data; for binary or very large "
        "files prefer exec_command on the node (e.g. curl) to pull them directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "device": {
                "type": "string",
                "description": "Device name or node_id (optional; defaults to the first online device).",
            },
            "path": {
                "type": "string",
                "description": "Destination path on the node (parent dirs are created).",
            },
            "content": {
                "type": "string",
                "description": "Local file path to read (default), or inline text when extra.path_replace is [].",
            },
            "extra": {
                "type": "object",
                "description": 'Optional config. {"path_replace": ["content"]} (default) means content is a local path to read; {"path_replace": []} means content is inline text.',
                "properties": {
                    "path_replace": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Field names whose string values are local file paths to read and replace with file contents.",
                    },
                },
                "default": {"path_replace": ["content"]},
            },
            "timeout": {
                "type": "integer",
                "description": "Per-call timeout in seconds (default 120).",
            },
        },
        "required": ["path", "content"],
    },
}


def _read_local_text(path_str: str, max_bytes: int) -> str:
    """Read a hub-side local text file, enforcing the size cap."""
    from pathlib import Path as _P

    p = _P(path_str).expanduser()
    if not p.is_file():
        raise ValueError(
            f"content {path_str!r} is not a readable local file. "
            "send_file treats content as a LOCAL PATH by default; pass "
            "extra={'path_replace': []} to send inline text instead."
        )
    size = p.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"local file {path_str!r} is {size} bytes, exceeding the "
            f"{max_bytes} byte cap (HERMES_NODE_HUB_SEND_FILE_MAX_BYTES). "
            "Use exec_command with curl on the node for large files."
        )
    data = p.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            f"local file {path_str!r} is not valid UTF-8 text. send_file "
            "writes text files; binary content should be pulled on the node via exec_command (e.g. curl)."
        ) from None


def _first_online_node_id():
    for dev in get_registry().list_public():
        if dev["online"]:
            return dev["node_id"]
    return None


def _handle_send_file(args: dict, **kwargs) -> str:
    start_server()
    args = args or {}
    path = args.get("path")
    content = args.get("content")
    if not path or content is None:
        return tool_error("both 'path' and 'content' are required")
    extra = args.get("extra") or {}
    path_replace = extra.get("path_replace", ["content"])
    if not isinstance(path_replace, list) or not all(
        isinstance(f, str) for f in path_replace
    ):
        return tool_error("extra.path_replace must be a list of field names")
    if "content" in path_replace:
        try:
            content = _read_local_text(content, config.send_file_max_bytes())
        except ValueError as exc:
            return tool_error(str(exc))
    device = args.get("device")
    if device:
        node_id, err = _resolve_or_error(device)
        if err:
            return err
    else:
        node_id = _first_online_node_id()
        if node_id is None:
            return tool_error(
                "no online device to send the file to; pass device=... or start a node"
            )
    result = call_device(
        node_id, "send_file", {"path": path, "content": content}, args.get("timeout")
    )
    if not result.get("ok"):
        return tool_error(result.get("error", "call failed"))
    return tool_result(**result)


def _resolve_or_error(device: str):
    node_id = get_registry().resolve(device)
    if node_id is None:
        return None, tool_error(
            f"device {device!r} is not registered. Run nodes_list to see registered devices."
        )
    if not get_registry().is_online(node_id):
        return None, tool_error(f"device {device!r} is offline (no heartbeat).")
    return node_id, None


def _handle_nodes_list(args: dict, **kwargs) -> str:
    start_server()
    get_registry().prune_offline()  # hot-plug: drop stale offline entries first
    devices = get_registry().list_public()
    name = (args or {}).get("device")
    if name:
        devices = [d for d in devices if d["name"] == name or d["node_id"] == name]
    return tool_result(devices=devices, count=len(devices))


def _handle_nodes_prune(args: dict, **kwargs) -> str:
    start_server()
    ttl = (args or {}).get("ttl")
    if ttl is not None and ttl < 0:
        return tool_error("ttl must be >= 0")
    removed = get_registry().prune_offline(ttl=ttl)
    return tool_result(removed=removed, remaining=len(get_registry().list_public()))


def _handle_node_call(args: dict, **kwargs) -> str:
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


def _handle_nodes_run(args: dict, **kwargs) -> str:
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


def _handle_nodes_fanout(args: dict, **kwargs) -> str:
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
    ("nodes_prune", _NODES_PRUNE_SCHEMA, _handle_nodes_prune, "🧹"),
    ("node_call", _NODE_CALL_SCHEMA, _handle_node_call, "🖥️"),
    ("nodes_run", _NODES_RUN_SCHEMA, _handle_nodes_run, "⚡"),
    ("nodes_fanout", _NODES_FANOUT_SCHEMA, _handle_nodes_fanout, "🌐"),
    ("send_file", _SEND_FILE_SCHEMA, _handle_send_file, "📤"),
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
