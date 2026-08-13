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
    __slots__ = ("node_id", "name", "platform", "capabilities", "addr", "last_seen", "disconnected_at")

    def __init__(
        self,
        node_id: str,
        name: str,
        platform: str,
        capabilities: List[str],
        addr: str,
        last_seen: float,
        disconnected_at: Optional[float] = None,
    ) -> None:
        self.node_id = node_id
        self.name = name
        self.platform = platform
        self.capabilities = capabilities
        self.addr = addr
        self.last_seen = last_seen
        self.disconnected_at = disconnected_at


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
                        disconnected_at=d.get("disconnected_at"),
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
                    "disconnected_at": d.disconnected_at,
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
                    d.disconnected_at = None  # re-connected
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
                dev.disconnected_at = None  # alive again

    def mark_disconnected(self, node_id: str) -> None:
        """Called on WS disconnect: the device goes offline immediately.

        Hot-plug semantics: a node that dropped its connection is offline
        right away, no waiting for the heartbeat timeout.
        """
        with self._lock:
            dev = self._devices.get(node_id)
            if dev and dev.disconnected_at is None:
                dev.disconnected_at = time.time()
                self._save()

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
            if not dev or dev.disconnected_at is not None:
                return False
            return (now - dev.last_seen) < config.heartbeat_timeout()

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
                        "online": (
                            d.disconnected_at is None
                            and (now - d.last_seen) < config.heartbeat_timeout()
                        ),
                        "last_seen": d.last_seen,
                        "disconnected_at": d.disconnected_at,
                    }
                )
            return out

    def prune_offline(self, ttl: Optional[int] = None) -> int:
        """Remove devices that have been offline longer than *ttl* seconds.

        Hot-plug friendly: nodes connect on demand and vanish when idle, so
        stale entries are dropped (and the registry file rewritten) instead of
        lingering forever. Offline age uses disconnected_at when known (WS
        disconnect) and falls back to last heartbeat age otherwise.
        Returns the number of pruned devices.
        """
        ttl = ttl if ttl is not None else config.offline_ttl()
        now = time.time()
        with self._lock:
            dead = []
            for nid, d in self._devices.items():
                if d.disconnected_at is not None:
                    offline_for = now - d.disconnected_at
                else:
                    offline_for = now - d.last_seen - config.heartbeat_timeout()
                if offline_for > ttl:
                    dead.append(nid)
            for nid in dead:
                del self._devices[nid]
            if dead:
                self._save()
        return len(dead)


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
