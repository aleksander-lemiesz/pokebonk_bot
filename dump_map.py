# -*- coding: utf-8 -*-
"""Dump ekranu 'Choose map' — selektory kafelków mapy i przycisku startu."""
import json
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
PROFILE = ROOT / "chrome-profile"

JS = r"""
() => {
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight));
  const btns = [...document.querySelectorAll('button')].filter(vis).map(b => {
    const r = b.getBoundingClientRect();
    return {cls: String(b.className).slice(0,70), text: (b.textContent||'').trim().slice(0,50),
            title: (b.title||'').slice(0,60), box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]};
  });
  const ui = document.getElementById('ui');
  return { uiText: ui ? (ui.innerText||'').replace(/\s+/g,' ').slice(0,400) : '', btns };
}
"""

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE), headless=False, channel="chrome",
        ignore_default_args=["--enable-automation"],
        args=["--window-size=1320,920", "--lang=en", "--hide-crash-restore-bubble", "--no-first-run", "--no-default-browser-check"],
        no_viewport=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://pokebonk.com/?lang=en", wait_until="load", timeout=60000)
    page.wait_for_timeout(8000)

    # menu -> Survival -> League -> I choose you -> (choose map)
    page.locator("button.mm-item.endless:not(.coop)").first.click(timeout=5000)
    page.wait_for_timeout(2500)
    page.locator("button.ls-go").first.click(timeout=5000)
    page.wait_for_timeout(2500)

    d = page.evaluate(JS)
    (ROOT / "out" / "map-screen.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    page.screenshot(path=str(ROOT / "shots" / "map-screen.png"))
    print("uiText:", d["uiText"][:200])
    for b in d["btns"]:
        print(f"  {b['cls'][:52]:52} | {b['text'][:44]:44} | {b['box']}")

    # Zostawiamy przeglądarkę otwartą chwilę na ewentualny ręczny rzut oka, potem zamykamy
    page.wait_for_timeout(4000)
    ctx.close()
    pw.stop()
