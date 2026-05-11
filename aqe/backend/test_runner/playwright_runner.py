"""Playwright browser controller for the Computer Use agent pipeline.

The UI agent calls `take_screenshot()` to get the current browser state,
then Claude (via the Computer Use beta) responds with actions
(click, type, scroll, key). This module executes those actions and returns
a fresh screenshot for the next iteration.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from core.settings import settings

log = get_logger("PlaywrightRunner")

_page = None
_browser = None
_playwright = None
_cdp_session = None
_screencast_on_frame = None  # callback(frame_b64: str) -> awaitable
_screencast_task = None


async def start_browser() -> None:
    global _page, _browser, _playwright
    if _page is not None:
        return
    from playwright.async_api import async_playwright
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    _page = await _browser.new_page(viewport={"width": 1280, "height": 800})
    log.info("playwright.browser_started")


async def stop_browser() -> None:
    global _page, _browser, _playwright
    # Stop screencast first so frame events don't fire after page close
    try:
        await stop_screencast()
    except Exception:
        pass
    if _page:
        await _page.close()
        _page = None
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    log.info("playwright.browser_stopped")


async def navigate(url: str) -> str:
    """Navigate to URL; return base64 screenshot."""
    await start_browser()
    assert _page
    await _page.goto(url, wait_until="networkidle", timeout=15000)
    return await take_screenshot()


async def take_screenshot() -> str:
    """Capture current page as base64 PNG (for Claude Computer Use)."""
    await start_browser()
    assert _page
    png_bytes = await _page.screenshot(type="png", full_page=False)
    return base64.b64encode(png_bytes).decode("utf-8")


async def execute_action(action: dict[str, Any]) -> str:
    """Execute a single Computer Use action block from Claude's response."""
    await start_browser()
    assert _page

    atype = action.get("type", "")

    if atype == "screenshot":
        pass  # just take screenshot below

    elif atype == "left_click":
        x, y = action["coordinate"]
        await _page.mouse.click(x, y)

    elif atype == "double_click":
        x, y = action["coordinate"]
        await _page.mouse.dblclick(x, y)

    elif atype == "right_click":
        x, y = action["coordinate"]
        await _page.mouse.click(x, y, button="right")

    elif atype == "type":
        await _page.keyboard.type(action["text"])

    elif atype == "key":
        key = action["text"].replace("+", "")
        await _page.keyboard.press(key)

    elif atype == "scroll":
        x, y = action["coordinate"]
        delta = action.get("scroll_direction", "down")
        amount = action.get("scroll_distance", 3) * 100
        dy = amount if delta == "down" else -amount
        await _page.mouse.wheel(0, dy)

    elif atype == "mouse_move":
        x, y = action["coordinate"]
        await _page.mouse.move(x, y)

    else:
        log.warning("playwright.unknown_action", context={"action_type": atype})

    await asyncio.sleep(0.3)  # brief settle after action
    return await take_screenshot()


async def login_if_required(username: str, password: str) -> None:
    """Fill and submit the login form if the current page is a login screen.

    Uses direct Playwright DOM interaction — faster and more reliable than
    routing this through Claude Computer Use. Safe to call even when already
    logged in; it no-ops if the login page is not detected.
    """
    await start_browser()
    assert _page
    if not username:
        return
    current_url = _page.url
    if "login" not in current_url.lower():
        return
    log.info("playwright.login_required", context={"url": current_url})
    await _page.fill("#uid", username)
    await _page.fill("#pwd", password)
    await _page.click(".login-btn")
    await _page.wait_for_load_state("networkidle", timeout=10000)
    log.info("playwright.login_completed", context={"landed_at": _page.url})


async def start_screencast(on_frame) -> None:
    """Begin a CDP Page.startScreencast stream on the current page.

    `on_frame` is an async callable taking a single base64 JPEG string.
    Frames arrive at ~Chromium's chosen rate; we ack each one so Chrome keeps sending.
    Safe to call multiple times — second call is a no-op if a screencast is already running.
    """
    global _cdp_session, _screencast_on_frame
    await start_browser()
    assert _page
    if _cdp_session is not None:
        log.info("playwright.screencast_already_running")
        return

    _screencast_on_frame = on_frame
    try:
        _cdp_session = await _page.context.new_cdp_session(_page)
    except Exception as exc:
        log.warning("playwright.screencast_cdp_failed", context={"error": str(exc)})
        _cdp_session = None
        _screencast_on_frame = None
        return

    async def _on_frame_event(params: dict) -> None:
        frame_b64 = params.get("data", "")
        session_id = params.get("sessionId")
        # Push the frame to the consumer (e.g., event bus)
        if _screencast_on_frame and frame_b64:
            try:
                await _screencast_on_frame(frame_b64)
            except Exception as exc:
                log.warning("playwright.screencast_callback_failed", context={"error": str(exc)})
        # Ack so Chrome keeps streaming
        if _cdp_session and session_id is not None:
            try:
                await _cdp_session.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

    # CDP events deliver sync callbacks; wrap to fire-and-forget asyncio tasks
    def _frame_listener(params):
        asyncio.create_task(_on_frame_event(params))

    _cdp_session.on("Page.screencastFrame", _frame_listener)

    await _cdp_session.send("Page.startScreencast", {
        "format": "jpeg",
        "quality": 60,
        "maxWidth": 1024,
        "maxHeight": 640,
        "everyNthFrame": 3,
    })
    log.info("playwright.screencast_started")


async def stop_screencast() -> None:
    """Stop the CDP screencast (if running). Safe to call when not started."""
    global _cdp_session, _screencast_on_frame
    if _cdp_session is None:
        return
    try:
        await _cdp_session.send("Page.stopScreencast")
    except Exception as exc:
        log.warning("playwright.screencast_stop_failed", context={"error": str(exc)})
    try:
        await _cdp_session.detach()
    except Exception:
        pass
    _cdp_session = None
    _screencast_on_frame = None
    log.info("playwright.screencast_stopped")


async def get_page_url() -> str:
    """Return the current page URL."""
    await start_browser()
    assert _page
    return _page.url


async def get_page_title() -> str:
    await start_browser()
    assert _page
    return await _page.title()
