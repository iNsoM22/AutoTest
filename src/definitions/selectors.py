"""Deterministic selector extraction and stability ranking from recorded element metadata."""

from __future__ import annotations

import re
from typing import Any

from src.definitions.models import SelectorCandidate, SelectorStrategy

_DYNAMIC_ID_RE = re.compile(
    r"(?:"
    r"^[0-9a-f]{8,}$"
    r"|^[0-9]+$"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|^ember\d+$"
    r"|^react-[a-z0-9-]+$"
    r"|^:r[0-9a-z]+:$"
    r"|^mui-\d+$"
    r"|^radix-[a-z0-9:-]+$"
    r"|^headlessui-[a-z0-9:-]+$"
    r"|^mat-(?:input|select|option|tab|mdc)-\d+$"
    r"|^cdk-[a-z0-9-]+-\d+$"
    r"|[_-][0-9a-f]{8,}$"
    r"|[_-]\d{6,}$"
    r"|[:]\w+[:]"
    r")",
    re.IGNORECASE,
)

_SIMPLE_CSS_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

_TAG_TO_ROLE: dict[str, str] = {
    "button": "button",
    "a": "link",
    "select": "combobox",
    "textarea": "textbox",
}


def _css_attr_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _css_id_selector(element_id: str) -> str:
    """Return `#id` for simple identifiers or `[id="..."]` when special chars are present."""
    if _SIMPLE_CSS_IDENT_RE.match(element_id):
        return f"#{element_id}"
    return f'[id="{_css_attr_escape(element_id)}"]'


def _css_path_confidence(css_path: str) -> float:
    """Estimate stability of a fallback CSS path based on depth and nth-child fragility."""
    depth = css_path.count(">") + 1
    nth_count = css_path.lower().count(":nth-")
    score = 0.60 - (0.04 * max(0, depth - 2)) - (0.08 * nth_count)
    return round(max(0.20, min(0.60, score)), 2)


def _looks_dynamic_id(element_id: str) -> bool:
    return bool(_DYNAMIC_ID_RE.search(element_id.strip()))


def _infer_role(target: dict[str, Any]) -> str:
    explicit = str(target.get("role", "")).strip().lower()
    if explicit:
        return explicit

    tag = str(target.get("tag", "")).strip().lower()
    if tag == "input":
        input_type = str(target.get("input_type", "")).strip().lower()
        if input_type in ("submit", "button", "reset"):
            return "button"
        if input_type == "checkbox":
            return "checkbox"
        if input_type == "radio":
            return "radio"
        if input_type == "search":
            return "searchbox"
        if input_type == "number":
            return "spinbutton"
        if input_type in ("", "text", "email", "tel", "url"):
            return "textbox"
        return ""
    return _TAG_TO_ROLE.get(tag, "")


def build_selector_candidates(
    target: dict[str, Any],
) -> tuple[SelectorCandidate | None, list[SelectorCandidate]]:
    """Rank recorded DOM target attributes into a primary selector and fallbacks.

    Preference order (highest stability first):
    1. `test_id` (`data-testid` / `data-test` / `data-cy`)
    2. Accessible `role` + accessible `name` (`aria_label` or short `text`)
    3. `aria_label` (`get_by_label`)
    4. `placeholder` (`get_by_placeholder`)
    5. Stable `id` (`#id`)
    6. `name` attribute (`[name="..."]`)
    7. Short visible `text` (`get_by_text`)
    8. `css_path` fallback
    """
    if not target:
        return None, []

    candidates: list[SelectorCandidate] = []
    seen: set[tuple[SelectorStrategy, str, str, str]] = set()

    def _add(candidate: SelectorCandidate) -> None:
        key = (candidate.strategy, candidate.value, candidate.role, candidate.name)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    test_id = str(target.get("test_id", "")).strip()
    test_id_attr = str(target.get("test_id_attr", "")).strip().lower()
    if test_id:
        if test_id_attr in ("data-test-id", "data-cy"):
            _add(
                SelectorCandidate(
                    strategy=SelectorStrategy.CSS,
                    value=f'[{test_id_attr}="{_css_attr_escape(test_id)}"]',
                    confidence=0.98,
                )
            )
        else:
            _add(
                SelectorCandidate(
                    strategy=SelectorStrategy.TEST_ID,
                    value=test_id,
                    confidence=0.98,
                )
            )

    aria_label = str(target.get("aria_label", "")).strip()
    raw_text = " ".join(str(target.get("text", "")).split())
    short_text = raw_text if 0 < len(raw_text) <= 60 else ""
    role = _infer_role(target)
    accessible_name = aria_label or short_text

    if role and accessible_name:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.ROLE,
                value=f"{role}:{accessible_name}",
                role=role,
                name=accessible_name,
                confidence=0.92,
            )
        )

    if aria_label:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.LABEL,
                value=aria_label,
                confidence=0.88,
            )
        )

    placeholder = str(target.get("placeholder", "")).strip()
    if placeholder:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.PLACEHOLDER,
                value=placeholder,
                confidence=0.84,
            )
        )

    element_id = str(target.get("id", "")).strip()
    if element_id and not _looks_dynamic_id(element_id):
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.CSS,
                value=_css_id_selector(element_id),
                confidence=0.80,
            )
        )

    name_attr = str(target.get("name", "")).strip()
    if name_attr:
        tag = str(target.get("tag", "")).strip().lower()
        prefix = tag if tag in ("input", "select", "textarea", "button", "form") else ""
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.CSS,
                value=f'{prefix}[name="{_css_attr_escape(name_attr)}"]',
                confidence=0.76,
            )
        )

    if short_text:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.TEXT,
                value=short_text,
                confidence=0.72,
            )
        )

    alt_attr = str(target.get("alt", "")).strip()
    if alt_attr:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.CSS,
                value=f'img[alt="{_css_attr_escape(alt_attr)}"]',
                confidence=0.68,
            )
        )

    title_attr = str(target.get("title", "")).strip()
    if title_attr:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.CSS,
                value=f'[title="{_css_attr_escape(title_attr)}"]',
                confidence=0.66,
            )
        )

    css_path = str(target.get("css_path", "")).strip()
    if css_path:
        _add(
            SelectorCandidate(
                strategy=SelectorStrategy.CSS,
                value=css_path,
                confidence=_css_path_confidence(css_path),
            )
        )

    if not candidates:
        return None, []
    return candidates[0], candidates[1:]
