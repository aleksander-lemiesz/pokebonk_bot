# -*- coding: utf-8 -*-
"""Diagnoza: co widzimy po kliknięciu Survival w zalogowanym profilu bota."""
import json
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
PROFILE = ROOT / "chrome-profile"
SHOTS = ROOT / "shots"

JS = r"""
() => {
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight));
  const cards = [...document.querySelectorAll('[class*="card"],[class*="choice"],[class*="option"]')]
    .filter(vis).map(c => ({cls: String(c.className).slice(0,80), text: (c.innerText||'').replace(/\s+/g,' ').slice(0,80)}));
  const btns = [...document.querySelectorAll('button')].filter(vis).map(b => ({
    cls: String(b.className).slice(0,60), text: (b.textContent||'').trim().slice(0,60), title: b.title||'' }));
  const ui = document.getElementById('ui');
  return { uiText: ui ? (ui.innerText||'').replace(/\s+/g,' ').slice(0,500) : '', btns, cards };
}
"""


def dump(page, label):
    d = page.evaluate(JS)
    (ROOT / "out" / f"diag-{label}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    page.screenshot(path=str(SHOTS / f"diag-{label}.png"))
    print(f"== {label}: uiText={d['uiText'][:160]!r}")
    for b in d["btns"][:14]:
        print(f"   btn cls={b['cls']!r} text={b['text']!r}")
    for c in d["cards"][:6]:
        print(f"   card cls={c['cls']!r} text={c['text']!r}")
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
    dump(page, "0-menu")

    try:
        loc = page.locator("button.mm-item.endless:not(.coop)").first
        box = loc.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        print(">> kliknięto Survival")
    except Exception as e:
        print(">> klik Survival FAIL:", e)
    page.wait_for_timeout(3500)
    dump(page, "1-after-survival")
    page.wait_for_timeout(1500)
    ctx.close()
    pw.stop()
