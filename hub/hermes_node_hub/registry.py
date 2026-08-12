"""Thread-safe device registry with JSON persistence.

Devices stay in the registry after they disconnect (marked offline) so
`nodes_list` can show the fleet even when some machines are asleep.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger("hermes_node_hub.registry")


class Device:
    __slots__ = ("node_id", "name", "platform", "capabilities", "addr", "last_seen")

    def __init__(
        self,
        node_id: str,
        name: str,
        platform: str,
        capabilities: List[str],
        addr: str,
        last_seen: float,
    ) -> None:
        self.node_id = node_id
        self.name = name
        self.platform = platform
        self.capabilities = capabilities
        self.addr = addr
        self.last_seen = last_seen


class Registry:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._path = path or config.registry_file()
        self._devices: Dict[str, Device] = {}
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for node_id, d in raw.items():
                    self._devices[node_id] = Device(
                        node_id=node_id,
                        name=d.get("name", node_id),
                        platform=d.get("platform", "unknown"),
                        capabilities=list(d.get("capabilities", [])),
                        addr=d.get("addr", ""),
                        last_seen=float(d.get("last_seen", 0.0)),
                    )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load registry %s: %s", self._path, exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                node_id: {
                    "name": d.name,
                    "platform": d.platform,
                    "capabilities": d.capabilities,
                    "addr": d.addr,
                    "last_seen": d.last_seen,
                }
                for node_id, d in self._devices.items()
            }
            self._path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to save registry: %s", exc)

    # ------------------------------------------------------------- mutate
    def register(self, name: str, platform: str, capabilities: List[str], addr: str) -> str:
        """Register (or re-register) a device; returns its stable node_id."""
        now = time.time()
        with self._lock:
            for node_id, d in self._devices.items():
                if d.name == name:
                    d.platform = platform
                    d.capabilities = capabilities
                    d.addr = addr
                    d.last_seen = now
                    self._save()
                    return node_id
            node_id = uuid.uuid4().hex[:8]
            self._devices[node_id] = Device(
                node_id=node_id,
                name=name,
                platform=platform,
                capabilities=capabilities,
                addr=addr,
                last_seen=now,
            )
            self._save()
            return node_id

    def heartbeat(self, node_id: str) -> None:
        with self._lock:
            dev = self._devices.get(node_id)
            if dev:
                dev.last_seen = time.time()

    # -------------------------------------------------------------- query
    def resolve(self, name_or_id: str) -> Optional[str]:
        """Resolve a device name or node_id to a stable node_id."""
        with self._lock:
            if name_or_id in self._devices:
                return name_or_id
            for node_id, d in self._devices.items():
                if d.name == name_or_id:
                    return node_id
        return None

    def is_online(self, node_id: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        with self._lock:
            dev = self._devices.get(node_id)
            return bool(dev) and (now - dev.last_seen) < config.heartbeat_timeout()

    def list_public(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            out = []
            for node_id, d in sorted(self._devices.items(), key=lambda kv: kv[1].name):
                out.append(
                    {
                        "node_id": node_id,
                        "name": d.name,
                        "platform": d.platform,
                        "capabilities": d.capabilities,
                        "addr": d.addr,
                        "online": (now - d.last_seen) < config.heartbeat_timeout(),
                        "last_seen": d.last_seen,
                    }
                )
            return out


_registry: Optional[Registry] = None
_registry_lock = threading.Lock()


def get_registry() -> Registry:
    """Module-level singleton so server.py and the plugin share one registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = Registry()
    return _registry
