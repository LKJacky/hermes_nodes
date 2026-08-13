"""CLI entrypoint for hermes-node."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys

from . import __version__
from .client import run


def _default_device() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "hermes-node"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes-node",
        description="Device-side node: expose local tools to a hermes-node-hub.",
    )
    parser.add_argument(
        "--hub",
        default=os.environ.get("HERMES_NODE_HUB", "http://127.0.0.1:9721"),
        help="Hub URL, e.g. http://hub-host:9721 (default: env HERMES_NODE_HUB or http://127.0.0.1:9721)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HERMES_NODE_TOKEN", ""),
        help="Shared hub token (default: env HERMES_NODE_TOKEN)",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("HERMES_NODE_DEVICE", _default_device()),
        help="Device name shown in nodes_list (default: hostname)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exit after the connection drops instead of reconnecting forever.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=float(os.environ.get("HERMES_NODE_IDLE_TIMEOUT", "0")),
        help="Disconnect and exit after N seconds without any tool call "
             "(hot-plug: connect on demand, serve, drop off). 0 = stay connected "
             "indefinitely (default: env HERMES_NODE_IDLE_TIMEOUT or 0).",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(
            run(
                args.hub,
                args.token,
                args.device,
                once=args.once,
                idle_timeout=args.idle_timeout,
            )
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
