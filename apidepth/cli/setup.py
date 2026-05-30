"""Implements ``python -m apidepth setup``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import List, Optional

from apidepth.cli.framework_detector import detect

DASHBOARD_KEYS_URL = "https://apidepth.io/dashboard/api-keys"


def run(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="apidepth setup",
        description="Configure the Apidepth SDK for your project.",
    )
    parser.add_argument("--api-key", metavar="KEY", help="API key (skips browser OAuth)")
    parser.add_argument("--collector-url", metavar="URL", help="Override collector URL")
    parser.add_argument(
        "--ignored-hosts",
        metavar="HOSTS",
        help="Comma-separated ignored host patterns",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Non-interactive mode; output to stdout only",
    )
    parser.add_argument(
        "--framework",
        metavar="NAME",
        help="Override framework detection (django|fastapi|nextjs|express|generic)",
    )
    args = parser.parse_args(argv)

    api_key: Optional[str] = args.api_key
    collector_url: Optional[str] = args.collector_url
    ignored_hosts: List[str] = (
        [h.strip() for h in args.ignored_hosts.split(",") if h.strip()]
        if args.ignored_hosts
        else []
    )
    no_prompt: bool = args.no_prompt

    # Interactive: open dashboard and prompt for key
    if not api_key and not no_prompt:
        print("\nApidepth SDK Setup")
        print("─" * 40)
        print("\nOpening your API keys page...")
        _open_browser(DASHBOARD_KEYS_URL)
        api_key = input("\nPaste your API key: ").strip()
        if not api_key:
            print("No API key provided. Aborting.", file=sys.stderr)
            sys.exit(1)

    # Interactive: prompt for ignored hosts
    if not no_prompt:
        print("\nDefault ignored hosts (always skipped):")
        for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            print(f"  • {h}")
        print(f"  • {collector_url or 'collector.apidepth.io'}")
        print("\nAny internal API patterns to ignore? (comma-separated, wildcards ok)")
        print("  Examples: *.internal, *.local, *.svc.cluster.local, *.railway.internal")
        raw = input("> ").strip()
        if raw:
            ignored_hosts += [h.strip() for h in raw.split(",") if h.strip()]

    result = detect(
        directory=os.getcwd(),
        api_key=api_key,
        ignored_hosts=ignored_hosts,
        collector_url=collector_url,
    )
    if args.framework:
        from apidepth.cli.framework_detector import _build_result
        result = _build_result(
            args.framework,
            api_key=api_key,
            ignored_hosts=ignored_hosts,
            collector_url=collector_url,
        )

    _print_result(result, no_prompt=no_prompt)


def _open_browser(url: str) -> None:
    import platform
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", url], check=False)
        elif system == "Linux":
            subprocess.run(["xdg-open", url], check=False)
        else:
            print(f"Visit: {url}")
    except Exception:
        print(f"Visit: {url}")


def _print_result(result, no_prompt: bool) -> None:
    if not no_prompt:
        print(f"\nDetected: {result.name.capitalize()}")

    if result.initializer_path and not no_prompt:
        print(f"\nAdd the following to {result.initializer_path}:\n")
        print(result.initializer_snippet)
        answer = input(f"Write to {result.initializer_path}? [y/N] ").strip().lower()
        if answer == "y":
            import os
            os.makedirs(os.path.dirname(result.initializer_path) or ".", exist_ok=True)
            with open(result.initializer_path, "w") as f:
                f.write(result.initializer_snippet)
            print(f"Written to {result.initializer_path}")
        else:
            print("(Not written — copy the snippet above into your codebase)")
    else:
        print(result.initializer_snippet)

    if not no_prompt:
        print("\nRun `python -m apidepth test` to confirm events are reaching the collector.")
