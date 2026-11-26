"""Shared lightweight utilities used across processing, scenarios, definitions, and UI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def dedupe_preserve(items: Sequence[str]) -> list[str]:
    """Return unique string items while preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def target_label(target: Mapping[str, Any], *, default: str = "element") -> str:
    """Return the most human-readable label available for a recorded DOM target."""
    for key in (
        "text",
        "aria_label",
        "label",
        "placeholder",
        "test_id",
        "name",
        "id",
        "css_path",
        "tag",
    ):
        val = target.get(key)
        if val is not None:
            cleaned = str(val).strip()
            if cleaned:
                return cleaned
    return default
