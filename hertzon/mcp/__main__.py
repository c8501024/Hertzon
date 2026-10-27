"""python -m hertzon.mcp            run the MCP server on stdio (started by the AI assistant)
python -m hertzon.mcp --install claude|desktop|codex|vscode|all    register it with an assistant
python -m hertzon.mcp --uninstall claude|desktop|codex|vscode|all
python -m hertzon.mcp --config     print the configuration for manual setup"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--install", "--uninstall", "--config", "-h", "--help"):
        from . import clients
        if argv[0] in ("-h", "--help"):
            print(__doc__)
            return 0
        if argv[0] == "--config":
            print(clients.snippets())
            return 0
        which = argv[1] if len(argv) > 1 else "all"
        targets = clients.CLIENTS if which == "all" else [which]
        rc = 0
        for c in targets:
            if c not in clients.CLIENTS:
                print(f"unknown client {c!r}; use {', '.join(clients.CLIENTS)} or all", file=sys.stderr)
                return 2
            try:
                print((clients.install if argv[0] == "--install" else clients.remove)(c))
            except Exception as e:  # noqa: BLE001
                print(f"{clients.TITLES[c]}: {e}", file=sys.stderr)
                rc = 1
        return rc
    for stream in (sys.stderr,):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    from .protocol import Server
    from .tools import TOOLS, INSTRUCTIONS
    Server("hertzon", _version(), INSTRUCTIONS, TOOLS).serve()
    return 0


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("hertzon")
    except Exception:  # noqa: BLE001
        return "0.2.5"


if __name__ == "__main__":
    sys.exit(main())
