"""Entrypoint: python -m ui — parse --port/PORT and run uvicorn."""
from __future__ import annotations

import argparse
import os

DEFAULT_PORT = 8765


def parse_args(argv: list[str] | None = None) -> tuple[int, argparse.Namespace]:
    parser = argparse.ArgumentParser(prog="ui")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind (default 127.0.0.1)",
    )
    ns = parser.parse_args(argv)
    # Precedence: --port flag > PORT env > 8765
    if ns.port is not None:
        port = ns.port
    elif os.environ.get("PORT"):
        try:
            port = int(os.environ["PORT"])
        except ValueError:
            port = DEFAULT_PORT
    else:
        port = DEFAULT_PORT
    return port, ns


def main(argv: list[str] | None = None) -> None:
    port, ns = parse_args(argv)
    host = ns.host
    import uvicorn

    uvicorn.run("ui.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
