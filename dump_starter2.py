# -*- coding: utf-8 -*-
"""Diagnoza 2: co się dzieje po kliknięciu Survival — klatka po klatce + Escape przed klikem."""
import json
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
PROFILE = ROOT / "chrome-profile"
SHOTS = ROOT / "shots"

JS = r"""
() => {
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight));
  const btns = [...document.querySelectorAll('button')].filter(vis).map(b => ({
    cls: String(b.className).slice(0,60), text: (b.textContent||'').trim().slice(0,40) }));
  const ui = document.getElementById('ui');
  const overlays = [...document.querySelectorAll('body > div')].map(d => String(d.className||d.id||'').slice(0,50));
  return { uiText: ui ? (ui.innerText||'').replace(/\s+/g,' ').slice(0,220) : '', btns, overlays };
}
"""


def dump(page, label):
    d = page.evaluate(JS)
    (ROOT / "out" / f"d2-{label}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    page.screenshot(path=str(SHOTS / f"d2-{label}.png"))
    key = [b for b in d["btns"] if any(k in b["cls"] for k in ("mm-item", "ls-go", "back-btn", "pill"))]
    print(f"== {label}: {d['uiText'][:110]!r}")
    for b in key[:10]:
        print(f"   {b['cls'][:44]:44} | {b['text'][:38]}")
    return d


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False, channel="chrome",
        ignore_default_args=["--enable-automation"],
        args=["--window-size=1320,920", "--lang=en", "--hide-crash-restore-bubble", "--no-first-run", "--no-default-browser-check"],
        no_viewport=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://pokebonk.com/?lang=en", wait_until="load", timeout=60000)
    page.wait_for_timeout(8000)
    dump(page, "0-load")

    # Zamknij ewentualny popup klawiszem Escape (trusted)
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    dump(page, "1-after-esc")

    loc = page.locator("button.mm-item.endless:not(.coop)").first
    try:
        box = loc.bounding_box(timeout=3000)
        print(f">> Survival box: {box}")
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    except Exception as e:
        print(">> FAIL:", e)
    for i, wait in enumerate([1, 2, 4], start=2):
        page.wait_for_timeout(wait * 1000)
        dump(page, f"{i}-t+{wait}s")

    page.wait_for_timeout(1000)
    ctx.close()
    pw.stop()
