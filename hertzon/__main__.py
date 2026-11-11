import sys

if len(sys.argv) > 1 and sys.argv[1] in ("version", "describe", "solve", "--version"):
    # python -m hertzon solve ...: the command line interface without the window (hertzon/cli.py)
    from .cli import main as cli_main
    raise SystemExit(cli_main(["version"] if sys.argv[1] == "--version" else sys.argv[1:]))

from .gui.app import main

raise SystemExit(main())
