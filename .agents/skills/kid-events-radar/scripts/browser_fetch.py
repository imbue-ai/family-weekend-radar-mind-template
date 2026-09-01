#!/usr/bin/env python3
"""Fetch one URL's rendered HTML via the Fortress stealth browser.

This helper is deliberately NOT a PEP 723 script: it is invoked as
``uv run python browser_fetch.py <url>`` from the repo root so it resolves the
workspace project venv (which has Playwright installed), not an isolated env.
It prints the page HTML to stdout and exits non-zero on failure, so the radar's
fetch layer can call it as a subprocess for sources that bot-block plain HTTP.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

_FORTRESS_EXECUTABLE_PATH = "/opt/fortress/tilion-fortress/tilion"
_PAGE_LOAD_TIMEOUT_MS = 45000


def _fetch_rendered_html(url: str) -> str:
    with sync_playwright() as playwright:
        # Try a normal launch first; retry without the sandbox only if the
        # runtime rejects the sandbox (gVisor is fine, bare runtimes are not).
        try:
            browser = playwright.chromium.launch(
                executable_path=_FORTRESS_EXECUTABLE_PATH
            )
        except Exception as launch_error:
            if "sandbox" not in str(launch_error).lower():
                raise
            browser = playwright.chromium.launch(
                executable_path=_FORTRESS_EXECUTABLE_PATH, args=["--no-sandbox"]
            )
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT_MS)
            html = page.content()
        finally:
            browser.close()
    return html


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: browser_fetch.py <url>", file=sys.stderr)
        raise SystemExit(2)
    url = sys.argv[1]
    try:
        html = _fetch_rendered_html(url)
    except Exception as fetch_error:
        print(f"browser fetch failed for {url}: {fetch_error}", file=sys.stderr)
        raise SystemExit(1) from fetch_error
    sys.stdout.write(html)


if __name__ == "__main__":
    main()
