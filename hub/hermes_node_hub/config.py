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

# Candidates for the Hermes .env file. Different Hermes surfaces (CLI,
# gateway, WebUI agent workers) may run with different $HOME values, so try
# the known real paths. Process env vars always win over the file.
_ENV_CANDIDATES = (
    Path("/home/hermeswebui/.hermes/.env"),
    Path(os.environ.get("HERMES_HOME", "")) / ".env",
    Path.home() / ".hermes" / ".env",
)


def _read_env_file(name: str) -> str:
    """Read a key from the Hermes .env file (KEY=VALUE lines, # comments)."""
    for path in _ENV_CANDIDATES:
        try:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    return _read_env_file(name) or default


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


def offline_ttl() -> int:
    """Seconds a disconnected device stays in the registry before auto-prune.

    Hot-plug semantics: nodes connect on demand and drop off when idle, so
    stale offline entries are pruned automatically (on nodes_list / nodes_prune)
    instead of lingering forever.
    """
    return int(_env("HERMES_NODE_HUB_OFFLINE_TTL", "300"))


def call_timeout() -> int:
    return int(_env("HERMES_NODE_HUB_CALL_TIMEOUT", "120"))


def send_file_max_bytes() -> int:
    """Cap on how large a local file the hub will read for send_file's
    path_replace flow. Files beyond this should be pulled on the node via exec_command (e.g. curl)."""
    return int(_env("HERMES_NODE_HUB_SEND_FILE_MAX_BYTES", str(64 * 1024 * 1024)))


def hub_url() -> str:
    return f"http://{hub_host()}:{hub_port()}"
