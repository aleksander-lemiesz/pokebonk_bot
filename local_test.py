# -*- coding: utf-8 -*-
"""Test trybu local: blokujemy api.pokebonk.com + Cloudflare, gra ma spaść na backend lokalny."""
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
SHOTS = ROOT / "shots"

BLOCK = ("api.pokebonk.com", "challenges.cloudflare.com")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    def route_handler(route):
        route.abort()

    page.route("**/*", lambda route: route.abort() if any(b in route.request.url for b in BLOCK) else route.continue_())

    page.goto("https://pokebonk.com/?lang=en", wait_until="load", timeout=60000)
    page.wait_for_timeout(7000)
    print("[start] btns:", page.evaluate(
        "() => [...document.querySelectorAll('button')].filter(b=>b.offsetWidth).map(b=>(b.textContent||'').trim().slice(0,40))"))
    page.screenshot(path=str(SHOTS / "local-0.png"))

    page.get_by_text("Play as a guest!").click()
    page.wait_for_timeout(4000)
    txt = page.evaluate("() => (document.getElementById('ui')?.innerText || '').replace(/\\s+/g,' ').slice(0, 400)")
    btns = page.evaluate("() => [...document.querySelectorAll('button')].filter(b=>b.offsetWidth).map(b=>(b.textContent||'').trim().slice(0,40))")
    print("[po kliku] text:", txt[:300])
    print("[po kliku] btns:", btns)
    page.screenshot(path=str(SHOTS / "local-1.png"))

    browser.close()
