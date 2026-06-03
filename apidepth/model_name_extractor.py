"""Extracts the AI model name from vendor JSON response bodies.

Only activates for a hard-coded set of AI vendor hostnames and only when the
response Content-Type is application/json.  All other calls return None
immediately with zero overhead.

Body safety: for requests and httpx non-streaming responses the body is already
fully buffered by the time the instrumentation wrapper calls this function.
Streaming responses carry Content-Type: text/event-stream so the JSON guard
exits before any body access.
"""

from __future__ import annotations

import json
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

_MAX_BODY_BYTES = 8_192


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

        text = body_bytes[:_MAX_BODY_BYTES].decode("utf-8", errors="ignore")
        data = json.loads(text)
        model = data.get("model")
        return model if isinstance(model, str) and model else None
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
