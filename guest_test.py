# -*- coding: utf-8 -*-
"""Test logowania gościa: podsłuch sieci + dłuższe czekanie."""
import pathlib
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
SHOTS = ROOT / "shots"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    seen = []

    def on_response(resp):
        url = resp.url
        if any(x in url for x in ("api", "guest", "auth", "backend", "supabase", "firebase", "worker")):
            seen.append((resp.status, url[:140]))

    page.on("response", on_response)
    page.goto("https://pokebonk.com/?lang=en", wait_until="load", timeout=60000)
    page.wait_for_timeout(6000)

    print("[net] klikam Play as a guest!")
    page.get_by_text("Play as a guest!").click()
    for i in range(6):
        page.wait_for_timeout(5000)
        txt = page.evaluate("() => (document.getElementById('ui')?.innerText || '').replace(/\\s+/g,' ').slice(0, 300)")
        btns = page.evaluate("() => [...document.querySelectorAll('button')].filter(b=>b.offsetWidth).map(b=>(b.textContent||'').trim().slice(0,40))")
        print(f"[t+{(i+1)*5}s] text={txt!r}")
        print(f"[t+{(i+1)*5}s] btns={btns}")
        page.screenshot(path=str(SHOTS / f"guest-{(i+1)*5}s.png"))
        if seen:
            print("[net] odpowiedzi:", seen[-6:])

    browser.close()
