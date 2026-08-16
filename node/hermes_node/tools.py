"""Local tools exposed by a hermes-node device.

Each function returns a plain dict so results serialize cleanly over the
WebSocket. These are also the exact functions a FastMCP server could wrap
if you want the device's own Hermes to use them locally.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def exec_command(cmd: str, timeout: int = 120, cwd: str = "~") -> dict:
    """Execute a shell command on the device."""
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_expand(cwd),
        )
        return {
            "code": r.returncode,
            "stdout": r.stdout[-6000:],
            "stderr": r.stderr[-2000:],
            "duration_ms": 0,
        }
    except subprocess.TimeoutExpired:
        return {"code": -1, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"code": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def _decode_prefix(data: bytes):
    """Return (payload, is_binary) for a bytes chunk.

    Strict UTF-8 wins -> (str, False). Retries after dropping up to 3 tail
    bytes in case a multi-byte char was cut by the max_bytes cap. Anything
    else -> (base64 str, True) so binary round-trips losslessly.
    """
    import base64

    for cut in range(0, 4):
        chunk = data if cut == 0 else data[:-cut]
        try:
            return chunk.decode("utf-8"), False
        except UnicodeDecodeError:
            continue
    return base64.b64encode(data).decode("ascii"), True


def read_file(path: str, max_bytes: int = 200_000) -> dict:
    """Read a file from the device (capped at max_bytes).

    Text files come back as UTF-8 text; binary files come back base64-encoded
    with binary=True so any format round-trips losslessly.
    """
    try:
        p = Path(_expand(path))
        if not p.exists():
            return {"error": f"no such file: {path}"}
        data = p.read_bytes()[:max_bytes]
        payload, is_binary = _decode_prefix(data)
        return {
            "path": str(p),
            "bytes": len(data),
            "content": payload,
            "binary": is_binary,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def write_file(path: str, content: str, binary: bool = False) -> dict:
    """Write a file on the device (creates parent directories).

    binary=False (default): content is UTF-8 text.
    binary=True: content is base64-encoded bytes (any format).
    """
    try:
        p = Path(_expand(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            import base64

            raw = base64.b64decode(content)
            p.write_bytes(raw)
            return {"ok": True, "path": str(p), "bytes": len(raw), "binary": True}
        p.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "path": str(p),
            "bytes": len(content.encode("utf-8")),
            "binary": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def send_file(path: str, content: str, binary: bool = False) -> dict:
    """Alias of write_file - kept as a distinct tool name so the hub can
    route path_replaced payloads to it without ambiguity."""
    return write_file(path, content, binary=binary)


def sys_info() -> dict:
    """CPU / memory / disk / load snapshot for fleet health checks."""
    info = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
        "load_avg": None,
        "mem_total_gb": None,
        "mem_available_gb": None,
        "disk_free_gb": None,
        "gpu": None,
        "ts": time.time(),
    }
    try:
        if os.path.exists("/proc/loadavg"):
            info["load_avg"] = open("/proc/loadavg").read().split()[:3]
    except Exception:
        pass
    try:
        if os.path.exists("/proc/meminfo"):
            mem = {}
            for line in open("/proc/meminfo"):
                k, _, v = line.partition(":")
                mem[k] = int(v.strip().split()[0]) / 1024 / 1024  # GB
            info["mem_total_gb"] = round(mem.get("MemTotal", 0), 1)
            info["mem_available_gb"] = round(mem.get("MemAvailable", 0), 1)
    except Exception:
        pass
    try:
        total, used, free = shutil.disk_usage("/")
        info["disk_free_gb"] = round(free / 1024**3, 1)
    except Exception:
        pass
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            r = subprocess.run(
                [nvidia, "--query-gpu=name,memory.total,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                info["gpu"] = [g.strip() for g in r.stdout.strip().splitlines()]
        except Exception:
            pass
    return info


ALL_TOOLS = {
    "exec_command": exec_command,
    "read_file": read_file,
    "write_file": write_file,
    "sys_info": sys_info,
    "send_file": send_file,
}
