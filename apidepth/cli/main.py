"""CLI entry point for the Apidepth Python SDK.

Invoked via:
  python -m apidepth setup
  python -m apidepth test

Registered as a console_scripts entry point in pyproject.toml:
  apidepth = "apidepth.cli.main:main"
"""

from __future__ import annotations

import sys


def main(argv=None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    subcommand = args[0] if args else None
    rest = args[1:] if args else []

    if subcommand == "setup":
        from apidepth.cli.setup import run

        run(rest)
    elif subcommand == "test":
        from apidepth.cli.test_cmd import run

        run(rest)
    elif subcommand in (None, "--help", "-h"):
        print("Usage: apidepth <subcommand> [options]")
        print("")
        print("Subcommands:")
        print("  setup   Configure the SDK and write your initializer")
        print("  test    Send a test event to confirm the pipeline works")
        print("")
        print("Run `apidepth <subcommand> --help` for subcommand options.")
    else:
        print(f"Unknown subcommand: {subcommand!r}", file=sys.stderr)
        print("Run `apidepth --help` for usage.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
