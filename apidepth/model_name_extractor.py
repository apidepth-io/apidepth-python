"""Extracts the AI model name from vendor JSON response bodies.

Only activates for a hard-coded set of AI vendor hostnames and only when the
response Content-Type is application/json.  All other calls return None
immediately with zero overhead.

Body safety: for requests and httpx non-streaming responses the body is already
fully buffered by the time the instrumentation wrapper calls this function.
Streaming responses carry Content-Type: text/event-stream so the JSON guard
exits before any body access.

Extraction strategy (PY-018): scan for the JSON ``"model": "<value>"`` field
with a linear regex over the raw bytes rather than ``json.loads`` on a truncated
body.  Embeddings and batch responses place ``model`` AFTER a large ``data``
array, so the old parse-after-8KB-truncate approach produced invalid JSON and
silently dropped the model.  The regex finds the first structural model field
wherever it sits, up to a generous scan bound.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_AI_VENDOR_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "api.mistral.ai",
        "api.cohere.com",
    }
)

#: Upper bound on how far into the body we scan for the model field. 256 KB
#: comfortably covers realistic embeddings/batch responses (a few-input OpenAI
#: embeddings body is ~23 KB) while bounding work on pathologically large bodies.
_MODEL_SCAN_MAX_BYTES = 262_144

#: Matches a structural JSON "model": "<value>" pair in the raw bytes. Escaped
#: quotes inside string values appear as \" so this never matches a "model"
#: mentioned inside another JSON string. First match wins (top-level model).
_MODEL_RE = re.compile(rb'"model"\s*:\s*"([^"]+)"')


def extract(host: str, response: Any) -> Optional[str]:
    """Return the model name from *response*, or None if not extractable.

    Args:
        host: Bare hostname of the request (e.g. ``"api.openai.com"``).
        response: A requests.Response or httpx.Response object.

    Returns:
        Model name string (e.g. ``"gpt-4-turbo"``), or ``None``.
    """
    try:
        import apidepth

        if not apidepth.get_configuration().capture_model_names:
            return None
    except Exception:
        return None

    if host not in _AI_VENDOR_HOSTS:
        return None

    try:
        ct = ""
        if hasattr(response, "headers"):
            ct = (
                response.headers.get("content-type", "")
                if callable(response.headers.get)
                else str(response.headers.get("content-type", ""))
            )
        if "application/json" not in ct:
            return None

        body_bytes = _safe_body(response)
        if not body_bytes:
            return None

        match = _MODEL_RE.search(body_bytes[:_MODEL_SCAN_MAX_BYTES])
        if not match:
            return None
        model = match.group(1).decode("utf-8", errors="ignore")
        return model or None
    except Exception:
        return None


def _safe_body(response: Any) -> Optional[bytes]:
    """Return buffered body bytes without triggering a streaming read.

    For requests: ``_content`` is ``False`` when not yet buffered (streaming).
    For httpx: ``content`` raises ``ResponseNotRead`` when streaming.
    """
    # requests.Response: _content is bytes when buffered, False otherwise
    content = getattr(response, "_content", None)
    if content is False:
        return None
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)

    # httpx.Response: .content raises ResponseNotRead for streaming responses
    try:
        body = response.content
        if isinstance(body, (bytes, bytearray)):
            return bytes(body)
    except Exception:
        pass

    return None
