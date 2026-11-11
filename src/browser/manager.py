"""Dedicated Chromium lifecycle and page/tab tracking via Playwright."""

from __future__ import annotations

import logging
import uuid
from contextlib import suppress

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Frame,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

from src.browser.models import BrowserCallbacks, BrowserLaunchConfig, BrowserState, PageInfo

logger = logging.getLogger(__name__)


class BrowserManagerError(Exception):
    """Raised when the browser manager encounters a lifecycle or Playwright error."""


class BrowserManager:
    """Controls a dedicated Chromium instance and tracks its pages/tabs.

    Playwright's sync API is bound to the thread that calls `launch()`. In the
    desktop UI, run `BrowserManager` inside a dedicated worker thread.
    """

    def __init__(self, callbacks: BrowserCallbacks | None = None) -> None:
        self._callbacks = callbacks or BrowserCallbacks()
        self._state: BrowserState = BrowserState.IDLE
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: dict[str, Page] = {}
        self._page_info: dict[str, PageInfo] = {}
        self._next_tab_index: int = 0
        self._disconnected: bool = False

    @property
    def state(self) -> BrowserState:
        """Return the current browser lifecycle state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Return True if Chromium is launched, connected, and has not been closed."""
        return (
            self._state == BrowserState.RUNNING
            and not self._disconnected
            and self._browser is not None
            and self._browser.is_connected()
        )

    def list_pages(self) -> list[PageInfo]:
        """Return snapshots of all tracked pages ordered by tab index."""
        return sorted(self._page_info.values(), key=lambda info: info.tab_index)

    def get_page_id(self, page: Page) -> str | None:
        """Return the tracked `page_id` for a Playwright `Page`, or None if unknown."""
        for page_id, tracked in self._pages.items():
            if tracked == page:
                return page_id
        return None

    def launch(self, config: BrowserLaunchConfig) -> PageInfo:
        """Launch Chromium, create a context, open the initial page, and navigate."""
        if self._state in {BrowserState.LAUNCHING, BrowserState.RUNNING}:
            raise BrowserManagerError("BrowserManager is already running.")

        self._state = BrowserState.LAUNCHING
        self._disconnected = False
        self._pages.clear()
        self._page_info.clear()
        self._next_tab_index = 0

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=config.headless,
                slow_mo=config.slow_mo_ms,
            )
            self._browser.on("disconnected", self._handle_browser_disconnected)

            context_kwargs: dict[str, object] = {
                "viewport": {
                    "width": config.viewport_width,
                    "height": config.viewport_height,
                }
            }
            if config.record_video_dir.strip():
                context_kwargs["record_video_dir"] = config.record_video_dir
                context_kwargs["record_video_size"] = {
                    "width": config.viewport_width,
                    "height": config.viewport_height,
                }
            self._context = self._browser.new_context(**context_kwargs)  # type: ignore[arg-type]
            self._context.set_default_timeout(config.default_timeout_ms)
            if self._callbacks.on_context_created is not None:
                self._callbacks.on_context_created(self._context)
            self._context.on("page", self._handle_context_page)

            initial_page = self._context.new_page()
            info = self._register_page(initial_page)
            self._state = BrowserState.RUNNING

            target_url = config.start_url.strip()
            if target_url and target_url != "about:blank":
                initial_page.goto(target_url, wait_until="domcontentloaded")
                info = self._sync_page_info(info.page_id, initial_page, notify=True)

            logger.info(
                "Launched dedicated Chromium (headless=%s, start_url=%s)",
                config.headless,
                info.url or target_url,
            )
            return info
        except Exception as exc:
            self._state = BrowserState.ERROR
            logger.exception("Failed to launch Chromium")
            self.close()
            raise BrowserManagerError(f"Failed to launch Chromium: {exc}") from exc

    def open_page(self, url: str = "about:blank") -> PageInfo:
        """Open a new tab in the active browser context."""
        if not self.is_running or self._context is None:
            raise BrowserManagerError("Cannot open a page when the browser is not running.")

        page = self._context.new_page()
        info = self._register_page(page)
        target_url = url.strip()
        if target_url and target_url != "about:blank":
            page.goto(target_url, wait_until="domcontentloaded")
            info = self._sync_page_info(info.page_id, page, notify=True)
        return info

    def poll_events(self, timeout_ms: int = 100) -> bool:
        """Pump Playwright's sync event loop briefly so browser/page events dispatch.

        Returns True while the browser remains running with at least one open page.
        """
        if not self.is_running:
            return False

        open_page = self._first_open_page()
        if open_page is None:
            return False

        with suppress(PlaywrightError):
            # Expected if the user closed the tab or browser window during the wait.
            open_page.wait_for_timeout(timeout_ms)
        return self.is_running and self._first_open_page() is not None

    def close(self) -> None:
        """Close the browser context, Chromium process, and Playwright driver."""
        if self._state == BrowserState.CLOSED and self._playwright is None:
            return

        previous_state = self._state
        self._state = BrowserState.STOPPING

        if self._context is not None:
            try:
                self._context.close()
            except PlaywrightError:
                logger.debug("Ignored error closing browser context", exc_info=True)
            self._context = None

        if self._browser is not None:
            try:
                self._browser.close()
            except PlaywrightError:
                logger.debug("Ignored error closing browser", exc_info=True)
            self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                logger.debug("Ignored error stopping Playwright", exc_info=True)
            self._playwright = None

        self._pages.clear()
        if previous_state == BrowserState.ERROR:
            self._state = BrowserState.ERROR
        else:
            self._state = BrowserState.CLOSED
        logger.info("Closed dedicated Chromium session")

    def _first_open_page(self) -> Page | None:
        for page_id, page in list(self._pages.items()):
            info = self._page_info.get(page_id)
            if info is not None and not info.is_closed and not page.is_closed():
                return page
        return None

    def _register_page(self, page: Page) -> PageInfo:
        for existing_id, existing_page in self._pages.items():
            if existing_page == page:
                return self._page_info[existing_id]

        page_id = uuid.uuid4().hex
        tab_index = self._next_tab_index
        self._next_tab_index += 1

        info = PageInfo(
            page_id=page_id,
            tab_index=tab_index,
            url=page.url or "",
            title="",
            is_closed=page.is_closed(),
        )
        # Store before attaching listeners or calling Playwright RPCs to avoid re-entrancy.
        self._pages[page_id] = page
        self._page_info[page_id] = info

        page.on("framenavigated", lambda frame: self._handle_frame_navigated(page_id, page, frame))
        page.on("load", lambda _p: self._handle_page_load(page_id, page))
        page.on("close", lambda _p: self._handle_page_closed(page_id))

        if self._callbacks.on_page_opened is not None:
            self._callbacks.on_page_opened(info)
        if self._callbacks.on_page_attached is not None:
            self._callbacks.on_page_attached(page_id, page)
        return info

    def _handle_context_page(self, page: Page) -> None:
        self._register_page(page)

    def _handle_frame_navigated(self, page_id: str, page: Page, frame: Frame) -> None:
        if frame != page.main_frame:
            return
        self._sync_page_info(page_id, page, notify=True, force_notify=True)

    def _handle_page_load(self, page_id: str, page: Page) -> None:
        self._sync_page_info(page_id, page, notify=True, force_notify=False)

    def _sync_page_info(
        self,
        page_id: str,
        page: Page,
        *,
        notify: bool,
        force_notify: bool = False,
    ) -> PageInfo:
        current = self._page_info[page_id]
        new_url = page.url or current.url
        new_title = self._safe_title(page) or current.title
        url_changed = new_url != current.url
        is_reload = force_notify and bool(current.url) and not url_changed
        if not force_notify and not url_changed and new_title == current.title:
            return current

        nav_type = "reload" if is_reload else "navigate"
        updated = current.model_copy(
            update={"url": new_url, "title": new_title, "navigation_type": nav_type}
        )
        self._page_info[page_id] = updated
        if (
            notify
            and (force_notify or url_changed)
            and self._callbacks.on_page_navigated is not None
        ):
            self._callbacks.on_page_navigated(updated)
        return updated

    def _handle_page_closed(self, page_id: str) -> None:
        current = self._page_info.get(page_id)
        if current is None or current.is_closed:
            return
        updated = current.model_copy(update={"is_closed": True})
        self._page_info[page_id] = updated
        if self._callbacks.on_page_closed is not None:
            self._callbacks.on_page_closed(updated)

    def _handle_browser_disconnected(self, _browser: Browser) -> None:
        self._disconnected = True
        if self._state == BrowserState.RUNNING:
            self._state = BrowserState.CLOSED
        if self._callbacks.on_disconnected is not None:
            self._callbacks.on_disconnected()

    @staticmethod
    def _safe_title(page: Page) -> str:
        if page.is_closed():
            return ""
        try:
            return page.title()
        except PlaywrightError:
            return ""
