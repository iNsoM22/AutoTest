"""Sensitive-data redaction for recorded events, form inputs, and URLs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED_VALUE = "[REDACTED]"

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|auth|authorization|cookie|"
    r"session[_-]?id|credit[_-]?card|card[_-]?number|cvv|cvc|ssn|pin|otp|private[_-]?key)",
    re.IGNORECASE,
)

_SENSITIVE_INPUT_TYPES = frozenset({"password"})

_BEARER_PATTERN = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def is_sensitive_key(name: str) -> bool:
    """Return True if a field, header, or query parameter name likely holds a secret."""
    return bool(name and _SENSITIVE_KEY_PATTERN.search(name))


def redact_url(url: str) -> str:
    """Redact credentials in userinfo and sensitive query parameters from a URL."""
    if not url or url.startswith(("about:", "data:", "javascript:")):
        return url

    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        _, host_part = netloc.rsplit("@", 1)
        netloc = f"{REDACTED_VALUE}@{host_part}"

    if not parts.query:
        return urlunsplit((parts.scheme, netloc, parts.path, "", parts.fragment))

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = [
        (key, REDACTED_VALUE if is_sensitive_key(key) else value) for key, value in pairs
    ]
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            parts.path,
            urlencode(redacted_pairs, doseq=True),
            parts.fragment,
        )
    )


def redact_string(value: str) -> str:
    """Mask inline Authorization-style tokens in free-form strings."""
    return _BEARER_PATTERN.sub(rf"\1 {REDACTED_VALUE}", value)


def redact_target_and_payload(
    target: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return sanitized copies of event `target` and `payload` dictionaries."""
    clean_target = _sanitize_mapping(target)
    clean_payload = _sanitize_mapping(payload)

    input_type = str(clean_target.get("input_type", "")).lower()
    target_name = str(clean_target.get("name", ""))
    target_id = str(clean_target.get("id", ""))
    autocomplete = str(clean_target.get("autocomplete", "")).lower()

    is_sensitive_target = (
        input_type in _SENSITIVE_INPUT_TYPES
        or is_sensitive_key(target_name)
        or is_sensitive_key(target_id)
        or "password" in autocomplete
        or "cc-" in autocomplete
    )

    if is_sensitive_target:
        for field in ("value", "text", "key"):
            if field in clean_payload and clean_payload[field]:
                clean_payload[field] = REDACTED_VALUE
        clean_payload["redacted"] = True

    return clean_target, clean_payload


def _sanitize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"url", "href", "action", "location_url"} and isinstance(value, str):
            sanitized[key] = redact_url(value)
        elif is_sensitive_key(key):
            sanitized[key] = REDACTED_VALUE if value else ""
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_mapping(value)
        elif isinstance(value, list):
            sanitized[key] = [
                _sanitize_mapping(item)
                if isinstance(item, dict)
                else (redact_string(item) if isinstance(item, str) else item)
                for item in value
            ]
        elif isinstance(value, str):
            sanitized[key] = redact_string(value)
        else:
            sanitized[key] = value
    return sanitized
