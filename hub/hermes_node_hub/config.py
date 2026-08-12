"""Hub configuration — env-driven with sane defaults.

All settings are optional; the hub works out of the box on loopback.

Env vars:
    HERMES_NODE_HUB_HOST              bind address (default 127.0.0.1)
    HERMES_NODE_HUB_PORT              bind port (default 9721)
    HERMES_NODE_HUB_TOKEN             shared secret nodes must present (default "")
    HERMES_NODE_HUB_REGISTRY_FILE     JSON registry path (default ~/.hermes/node-hub-registry.json)
    HERMES_NODE_HUB_HEARTBEAT_TIMEOUT seconds before a silent node is marked offline (default 45)
    HERMES_NODE_HUB_CALL_TIMEOUT      default per-call timeout in seconds (default 120)
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def hub_host() -> str:
    return _env("HERMES_NODE_HUB_HOST", "127.0.0.1")


def hub_port() -> int:
    return int(_env("HERMES_NODE_HUB_PORT", "9721"))


def hub_token() -> str:
    return _env("HERMES_NODE_HUB_TOKEN", "")


def registry_file() -> Path:
    return Path(
        _env(
            "HERMES_NODE_HUB_REGISTRY_FILE",
            str(Path.home() / ".hermes" / "node-hub-registry.json"),
        )
    )


def heartbeat_timeout() -> int:
    return int(_env("HERMES_NODE_HUB_HEARTBEAT_TIMEOUT", "45"))


def call_timeout() -> int:
    return int(_env("HERMES_NODE_HUB_CALL_TIMEOUT", "120"))


def hub_url() -> str:
    return f"http://{hub_host()}:{hub_port()}"
