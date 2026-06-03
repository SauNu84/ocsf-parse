"""Capture UI screenshots used in README.md and the GitHub Pages landing page.

Drives a headless Chromium against a running ``ocsf-mapper serve`` instance
(default http://127.0.0.1:8002) and saves PNGs to ``docs/screenshots/``.

Usage:

    # In one terminal:
    python3 -m ocsf_mapper.cli serve --port 8002

    # In another:
    python3 scripts/capture_screenshots.py

Re-running is safe — PNGs are overwritten. Each capture has a short
``await_idle`` wait so HTMX swaps and Monaco's async load finish before the
shutter snaps.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
OUT_DIR    = REPO_ROOT / "docs" / "screenshots"
VIEWPORT   = {"width": 1400, "height": 900}
WAIT_LOAD  = 800   # ms after page navigation
WAIT_TAB   = 600   # ms after a tab click (HTMX swap)
WAIT_MONACO = 2200  # ms after Mapping tab click — Monaco loads from CDN


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("error: install with `pip install playwright && playwright install chromium`",
              file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = ctx.new_page()

        # Realism: seed the audit log with one OK + one REJECTED save so the
        # /audit screenshot isn't an empty-state page. Re-saves the existing
        # cloudtrail config (lints clean → OK row), then posts broken JSON
        # (parse fails → REJECTED row).
        print("seeding audit log…")
        api = ctx.request
        with open(REPO_ROOT / "mappings" / "cloudtrail.json", encoding="utf-8") as f:
            current = f.read()
        api.post(f"{args.base_url}/sources/cloudtrail/save",
                 form={"content": current})
        api.post(f"{args.base_url}/sources/cloudtrail/save",
                 form={"content": "{not valid json"})

        def shot(name: str, msg: str, *, clip: dict | None = None) -> None:
            path = OUT_DIR / f"{name}.png"
            kwargs = {"path": str(path), "full_page": False}
            if clip is not None:
                kwargs["clip"] = clip
            page.screenshot(**kwargs)
            print(f"  ✓ {path.relative_to(REPO_ROOT)}  ({msg})")

        # 1. Homepage
        print("\nhomepage…")
        page.goto(args.base_url)
        page.wait_for_timeout(WAIT_LOAD)
        # Expand IAM in the rail so the screenshot shows the tree drilled in.
        iam = page.locator('.tree-node[data-cat-slug="identity-access-management"] .tree-toggle')
        if iam.count() > 0:
            iam.first.click()
            page.wait_for_timeout(300)
        shot("homepage", "two-pane catalog: rail + KPIs + cards")

        # 2. Source page → Sample tab (default)
        print("\nsource page (Sample tab)…")
        page.goto(f"{args.base_url}/sources/cloudtrail")
        page.wait_for_timeout(WAIT_LOAD)
        shot("source-sample", "Sample tab — raw cloudtrail.jsonl")

        # 3. Snippets tab — click, wait for HTMX swap.
        print("\nsource page (Snippets tab)…")
        page.get_by_role("button", name="Snippets").first.click()
        page.wait_for_timeout(WAIT_TAB)
        shot("source-snippets", "Snippets tab — CLI / Python / PySpark / Pandas")

        # 4. Mapping tab — the interesting UI is the toolbar (with the new
        # "Lint against OCSF" dropdown), not the Monaco body. Clip the
        # screenshot to the toolbar area so we don't wait on the jsdelivr
        # Monaco bundle (which can be slow/unavailable in CI/headless).
        print("\nsource page (Mapping tab + OCSF version dropdown)…")
        page.get_by_role("button", name="Mapping").first.click()
        page.wait_for_timeout(WAIT_TAB)
        toolbar = page.locator(".mapping-tab .editor-toolbar")
        bbox = toolbar.bounding_box()
        clip = None
        if bbox:
            # Capture from the topbar down through the toolbar (with a bit
            # of room above and below for context). Page width is fine.
            clip = {
                "x": 0,
                "y": 0,
                "width": VIEWPORT["width"],
                "height": int(bbox["y"] + bbox["height"]) + 40,
            }
        shot("source-mapping", "Mapping tab toolbar with 'Lint against OCSF' dropdown", clip=clip)

        # 5. Audit page
        print("\naudit log…")
        page.goto(f"{args.base_url}/audit")
        page.wait_for_timeout(WAIT_LOAD)
        shot("audit", "Audit trail — every save attempt recorded")

        browser.close()

    print(f"\n{len(list(OUT_DIR.glob('*.png')))} screenshot(s) in {OUT_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
