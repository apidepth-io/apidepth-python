"""Framework detection for the apidepth setup subcommand.

Detects the web framework in the current directory by inspecting well-known
files. Returns a DetectedFramework with the recommended initializer path and
a copy-paste-ready snippet. Used by ``apidepth setup`` to avoid generic output.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DetectedFramework:
    name: str
    initializer_path: Optional[str]
    initializer_snippet: str


def detect(
    directory: str = ".",
    api_key: Optional[str] = None,
    ignored_hosts: Optional[List[str]] = None,
    collector_url: Optional[str] = None,
) -> DetectedFramework:
    """Detect the framework in *directory* and return a configured snippet."""
    framework = _detect_framework(directory)
    return _build_result(
        framework,
        api_key=api_key,
        ignored_hosts=ignored_hosts or [],
        collector_url=collector_url,
    )


def _detect_framework(directory: str) -> str:
    def exists(*parts: str) -> bool:
        return os.path.exists(os.path.join(directory, *parts))

    if exists("manage.py"):
        return "django"

    # FastAPI: app.py present and fastapi appears in requirements
    if exists("app.py") and _has_requirement(directory, "fastapi"):
        return "fastapi"

    # Next.js check before Express (Next supersedes Express in most setups)
    if exists("next.config.js") or exists("next.config.ts"):
        return "nextjs"

    if exists("package.json") and _has_npm_dep(directory, "express"):
        return "express"

    return "generic"


def _has_requirement(directory: str, package: str) -> bool:
    for fname in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        path = os.path.join(directory, fname)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    if package.lower() in f.read().lower():
                        return True
            except OSError:
                pass
    return False


def _has_npm_dep(directory: str, package: str) -> bool:
    import json

    path = os.path.join(directory, "package.json")
    try:
        with open(path) as f:
            data = json.load(f)
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        return package in deps
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _build_result(
    framework: str,
    api_key: Optional[str],
    ignored_hosts: List[str],
    collector_url: Optional[str],
) -> DetectedFramework:
    key_val = api_key or "YOUR_API_KEY"
    url_val = collector_url or "https://collector.apidepth.io"
    hosts_repr = repr(ignored_hosts)

    if framework == "django":
        snippet = f"""\
# In your Django settings.py (or a dedicated apidepth.py imported from settings)
APIDEPTH = {{
    "api_key": {key_val!r},
    "collector_url": {url_val!r},
    "ignored_hosts": {hosts_repr},
}}
"""
        return DetectedFramework(
            name="django",
            initializer_path="settings.py",
            initializer_snippet=snippet,
        )

    if framework == "fastapi":
        snippet = f"""\
# In your app.py lifespan handler
import apidepth

apidepth.configure(
    api_key={key_val!r},
    collector_url={url_val!r},
    ignored_hosts={hosts_repr},
)
apidepth.instrument()
"""
        return DetectedFramework(
            name="fastapi",
            initializer_path="app.py",
            initializer_snippet=snippet,
        )

    if framework == "nextjs":
        snippet = f"""\
// instrumentation.ts (Next.js 13.4+)
import apidepth from "apidepth";

export async function register() {{
  apidepth.configure({{
    apiKey: {key_val!r},
    collectorUrl: {url_val!r},
    ignoredHosts: {hosts_repr},
  }});
  apidepth.instrument();
}}
"""
        return DetectedFramework(
            name="nextjs",
            initializer_path="instrumentation.ts",
            initializer_snippet=snippet,
        )

    if framework == "express":
        snippet = f"""\
// Near the top of your main app file, before any routes
import apidepth from "apidepth";

apidepth.configure({{
  apiKey: {key_val!r},
  collectorUrl: {url_val!r},
  ignoredHosts: {hosts_repr},
}});
apidepth.instrument();
"""
        return DetectedFramework(
            name="express",
            initializer_path=None,
            initializer_snippet=snippet,
        )

    snippet = f"""\
import apidepth

apidepth.configure(
    api_key={key_val!r},
    collector_url={url_val!r},
    ignored_hosts={hosts_repr},
)
apidepth.instrument()
"""
    return DetectedFramework(
        name="generic",
        initializer_path=None,
        initializer_snippet=snippet,
    )
