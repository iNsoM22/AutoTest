"""Design tokens, vector icons, and UX helpers for the AutoTest desktop UI.

Implements a three-layer token architecture (primitive -> semantic -> component)
tailored to AutoTest's dark precision-instrumentation aesthetic: cool obsidian
graphite surfaces paired with a calibrated laboratory teal accent and crisp
semantic status signals.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QPushButton, QWidget


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Semantic color, typography, and spacing tokens for AutoTest."""

    # Surfaces (Obsidian Graphite scale)
    canvas: str = "#0F1115"
    surface: str = "#161920"
    surface_alt: str = "#191D26"
    surface_elevated: str = "#1D222C"
    surface_hover: str = "#252B37"
    surface_pressed: str = "#2C3342"
    surface_code: str = "#11141A"

    # Borders
    border_subtle: str = "#232834"
    border_default: str = "#2E3544"
    border_strong: str = "#3E475B"

    # Ink / Typography
    text_primary: str = "#ECEFF4"
    text_secondary: str = "#9CA6B8"
    text_muted: str = "#687387"
    text_disabled: str = "#525C6E"

    # Brand Accent (Calibrated Laboratory Teal)
    accent: str = "#0EA5A4"
    accent_hover: str = "#14B8B6"
    accent_pressed: str = "#0B8786"
    accent_subtle: str = "#132E33"
    accent_border: str = "#1B5B5E"
    accent_fg: str = "#F0FDFA"

    # Semantic Signals
    success: str = "#22C55E"
    success_bg: str = "#122B1E"
    success_border: str = "#1E5638"
    success_fg: str = "#86EFAC"

    warning: str = "#EAB308"
    warning_bg: str = "#2C230E"
    warning_border: str = "#5C4816"
    warning_fg: str = "#FDE047"

    danger: str = "#DC2626"
    danger_hover: str = "#EF4444"
    danger_pressed: str = "#B91C1C"
    danger_bg: str = "#321619"
    danger_border: str = "#68282E"
    danger_fg: str = "#FCA5A5"

    info: str = "#38BDF8"
    info_bg: str = "#112838"
    info_border: str = "#1E4D6B"
    info_fg: str = "#7DD3FC"

    # Typography families
    font_sans: str = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
    font_mono: str = '"Cascadia Code", "JetBrains Mono", "Consolas", monospace'


TOKENS = ThemeTokens()


# Crisp 16x16 stroke-based SVG icon templates (currentColor replaced at render time).
_ICON_SVGS: dict[str, str] = {
    "brand": (
        '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="2" y="2" width="20" height="20" rx="5" '
        'fill="#132E33" stroke="#0EA5A4" stroke-width="1.8"/>'
        '<circle cx="9" cy="12" r="3" fill="#0EA5A4"/>'
        '<path d="M14.2 9.2L17.5 12L14.2 14.8" stroke="#ECEFF4" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    ),
    "plus": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round"><path d="M8 3.2v9.6M3.2 8h9.6"/></svg>'
    ),
    "edit": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M11.3 2.5a1.6 1.6 0 0 1 2.2 2.2L5.6 12.6 2.5 13.5l.9-3.1 7.9-7.9Z"/></svg>'
    ),
    "trash": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2.5 4.2h11M6 4.2V2.8h4v1.4M4.2 4.2l.7 9h6.2l.7-9"/></svg>'
    ),
    "record": (
        '<svg viewBox="0 0 16 16" fill="none">'
        '<circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.5"/>'
        '<circle cx="8" cy="8" r="2.7" fill="currentColor"/></svg>'
    ),
    "stop": (
        '<svg viewBox="0 0 16 16" fill="none">'
        '<rect x="3.5" y="3.5" width="9" height="9" rx="1.6" fill="currentColor"/></svg>'
    ),
    "play": (
        '<svg viewBox="0 0 16 16" fill="none">'
        '<path d="M4.2 3.1L12.6 8 4.2 12.9V3.1Z" fill="currentColor"/></svg>'
    ),
    "replay": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 8a5 5 0 1 0 1.4-3.5L3 6"/><path d="M3 2.8V6h3.2"/></svg>'
    ),
    "video": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="1.8" y="3.5" width="8.8" height="9" rx="1.6"/>'
        '<path d="M10.6 6.8l3.6-2.1v6.6l-3.6-2.1"/></svg>'
    ),
    "spark": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M8 2v2.4M8 11.6V14M2 8h2.4M11.6 8H14M4 4l1.6 1.6M10.4 10.4L12 12'
        'M12 4l-1.6 1.6M5.6 10.4L4 12"/></svg>'
    ),
    "import": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M8 2.5v7.5M5.2 7.4L8 10.2l2.8-2.8M2.5 12.8h11"/></svg>'
    ),
    "export": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M8 10V2.5M5.2 5.2L8 2.4l2.8 2.8M2.5 12.8h11"/></svg>'
    ),
    "copy": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="5.5" y="5.5" width="8" height="8" rx="1.4"/>'
        '<path d="M10.5 5.5V3.7A1.2 1.2 0 0 0 9.3 2.5H3.7A1.2 1.2 0 0 0 2.5 3.7v5.6'
        'a1.2 1.2 0 0 0 1.2 1.2h1.8"/></svg>'
    ),
    "folder": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 4.2A1.2 1.2 0 0 1 3.2 3h3l1.3 1.4h5.3A1.2 1.2 0 0 1 14 5.6v6.2'
        'a1.2 1.2 0 0 1-1.2 1.2H3.2A1.2 1.2 0 0 1 2 11.8V4.2Z"/></svg>'
    ),
    "sliders": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round">'
        '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>'
        '<circle cx="5.5" cy="4.5" r="1.4" fill="#1D222C"/>'
        '<circle cx="10.5" cy="8" r="1.4" fill="#1D222C"/>'
        '<circle cx="6.5" cy="11.5" r="1.4" fill="#1D222C"/></svg>'
    ),
    "key": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="5.5" cy="8" r="2.8"/><path d="M8.3 8h5.2M11.5 8v2M13.5 8v1.5"/></svg>'
    ),
    "refresh": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M13 8a5 5 0 1 1-1.4-3.5L13 6"/><path d="M13 2.8V6H9.8"/></svg>'
    ),
    "up": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round"><path d="M4 10l4-4 4 4"/></svg>'
    ),
    "down": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>'
    ),
    "merge": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 3v3.5L8 9.5l4-3V3M8 9.5V13.5"/></svg>'
    ),
    "split": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M8 2.5v4L4 9.5V13.5M8 6.5l4 3V13.5"/></svg>'
    ),
    "trace": (
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 8h2.6l1.7-4 2.6 8 1.8-4H14"/></svg>'
    ),
}

_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def make_icon(name: str, color: str = TOKENS.text_primary, size: int = 16) -> QIcon:
    """Render a crisp vector SVG icon tinted to `color` (cached per name/color/size)."""
    key = (name, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    svg = _ICON_SVGS.get(name)
    if svg is None:
        return QIcon()

    tinted = svg.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(tinted.encode("utf-8")))
    pixmap = QPixmap(QSize(size * 2, size * 2))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)

    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon


def set_button_state(
    button: QPushButton,
    enabled: bool,
    *,
    enabled_tip: str,
    disabled_tip: str,
) -> None:
    """Enable/disable a button and update its tooltip to explain why."""
    button.setEnabled(enabled)
    button.setToolTip(enabled_tip if enabled else disabled_tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)


def make_vseparator(parent: QWidget | None = None) -> QFrame:
    """Return a subtle vertical toolbar divider line."""
    sep = QFrame(parent)
    sep.setObjectName("toolbarSeparator")
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    sep.setFixedHeight(20)
    return sep


def make_hseparator(parent: QWidget | None = None) -> QFrame:
    """Return a subtle horizontal hairline divider."""
    sep = QFrame(parent)
    sep.setObjectName("hairlineDivider")
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFixedHeight(1)
    return sep


def status_color(status_text: str) -> QColor:
    """Return a semantic foreground color for common execution/session statuses."""
    normalized = status_text.strip().lower()
    if normalized in ("passed", "active", "open", "ok", "included", "yes"):
        return QColor(TOKENS.success_fg)
    if normalized in ("failed", "error", "cancelled"):
        return QColor(TOKENS.danger_fg)
    if normalized in ("running", "launching", "stopping", "journey"):
        return QColor(TOKENS.info_fg)
    if normalized in ("skipped", "review", "partial", "warning"):
        return QColor(TOKENS.warning_fg)
    return QColor(TOKENS.text_secondary)
