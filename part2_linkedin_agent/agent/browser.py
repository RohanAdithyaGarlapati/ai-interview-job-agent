"""One shared headless browser, instead of one per call.

Every Playwright call site here used to open its own `sync_playwright()` context
and launch its own Chromium, then tear both down again. Measured, that costs
~6.5s per call, against ~0.19s to open a page on a browser that is already
running - and resolving a single LinkedIn URL makes several such calls (render
the careers page, retry the ATS lookup, then up to four search-fallback
queries). The launch cost dominated the actual work by a wide margin.

So the browser is started once, lazily, and reused. Playwright's sync API is not
thread-safe and its objects cannot cross threads, so the instance is held in
thread-local storage: single-threaded callers get one browser, and a thread pool
gets one per worker thread, which is what makes `test_batch.py --workers` safe.
"""
from __future__ import annotations

import atexit
import threading
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from .linkedin import USER_AGENT

_local = threading.local()
_all_lock = threading.Lock()
_all_instances: list[tuple] = []  # (playwright, browser) for shutdown


def _get_browser():
    browser = getattr(_local, "browser", None)
    if browser is not None:
        return browser

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        args=[
            # This process never shows a window and never plays media; skipping
            # that machinery measurably shortens startup.
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--mute-audio",
            "--disable-extensions",
            "--disable-background-networking",
        ]
    )
    _local.pw, _local.browser = pw, browser
    with _all_lock:
        _all_instances.append((pw, browser))
    return browser


@contextmanager
def page(timeout_ms: int = 20000):
    """A fresh page on the shared browser, always closed afterwards.

    Each page comes from its own browser context, so cookies and storage do not
    leak between the sites being crawled - the isolation a separate browser gave
    us, without paying to start one.
    """
    browser = _get_browser()
    ctx = browser.new_context(user_agent=USER_AGENT)
    ctx.set_default_timeout(timeout_ms)
    pg = ctx.new_page()
    try:
        yield pg
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def shutdown() -> None:
    """Close every browser this process started. Registered with atexit."""
    with _all_lock:
        instances, _all_instances[:] = list(_all_instances), []
    for pw, browser in instances:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


atexit.register(shutdown)
