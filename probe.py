# -*- coding: utf-8 -*-
"""
probe.py — rekonesans PokeBonk (https://pokebonk.com)

Uruchamia grę w Chromium, dumpuje strukturę #ui + przyciski + zrzut ekranu.
Przydatne do odkrywania selektorów zanim napiszemy strategię bota.

Przykłady:
  python probe.py --out start
  python probe.py --out po-kliku --click "text=Play" --wait 3
  python probe.py --out test-wasd --key KeyW --hold 2000 --key-test-after-click "text=Play"
"""
import argparse
import json
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent
SHOTS = ROOT / "shots"
OUTS = ROOT / "out"

# Wstrzykiwany JS: utrzymywany w stronie zestaw trzymanych klawiszy + dump DOM.
INJECT = r"""
() => {
  if (!window.__pb) {
    const held = new Set();
    const send = (type, code) => {
      // Gra czyta e.key (litery) i e.code (reszta); ustawiamy oba spójnie.
      const key = code.startsWith('Key') ? code.slice(3).toLowerCase() : code;
      window.dispatchEvent(new KeyboardEvent(type, {
        key, code, bubbles: true, cancelable: true,
      }));
    };
    window.__pb = {
      down: (code) => { if (!held.has(code)) { held.add(code); send('keydown', code); return true; } return false; },
      up:   (code) => { if (held.has(code))  { held.delete(code); send('keyup', code); return true; } return false; },
      upAll: () => { for (const c of [...held]) { held.delete(c); send('keyup', c); } return held.size; },
      tap: (code, ms = 80) => { send('keydown', code); setTimeout(() => send('keyup', code), ms); return true; },
      held: () => [...held],
    };
  }
  return Object.keys(window.__pb);
}
"""

DUMP = r"""
() => {
  const outline = (el, depth, maxDepth) => {
    if (!el || depth > maxDepth) return null;
    let ownText = '';
    for (const n of el.childNodes) if (n.nodeType === 3) ownText += n.textContent;
    ownText = ownText.replace(/\s+/g, ' ').trim().slice(0, 60);
    const kids = [];
    if (depth < maxDepth) for (const c of el.children) kids.push(outline(c, depth + 1, maxDepth));
    const o = {
      tag: el.tagName.toLowerCase(),
      cls: (el.className && String(el.className)) || '',
      text: ownText,
      title: el.title || '',
      aria: el.getAttribute('aria-label') || '',
    };
    if (kids.length) o.kids = kids.filter(Boolean);
    return o;
  };
  const ui = document.getElementById('ui');
  const buttons = [...document.querySelectorAll('button')].map(b => ({
    cls: String(b.className || ''),
    text: (b.textContent || '').trim().slice(0, 60),
    title: b.title || '',
    aria: b.getAttribute('aria-label') || '',
    visible: !!(b.offsetWidth || b.offsetHeight),
  }));
  return {
    url: location.href,
    readyState: document.readyState,
    uiChildren: ui ? ui.children.length : -1,
    outline: outline(ui, 0, 4),
    buttons,
    canvasSize: (() => { const c = document.getElementById('game'); return c ? [c.width, c.height] : null; })(),
  };
}
"""


def dump(page, label: str) -> dict:
    OUTS.mkdir(exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    data = page.evaluate(DUMP)
    p = OUTS / f"{label}.json"
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    page.screenshot(path=str(SHOTS / f"{label}.png"))
    n_btn = sum(1 for b in data["buttons"] if b["visible"])
    print(f"[dump {label}] uiChildren={data['uiChildren']} buttons={n_btn} canvas={data['canvasSize']}")
    for b in data["buttons"]:
        if b["visible"]:
            print(f"   btn: cls={b['cls']!r} text={b['text']!r} title={b['title']!r}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dump")
    ap.add_argument("--url", default="https://pokebonk.com/?lang=en")
    ap.add_argument("--click", action="append", default=[], help="selektor do kliknięcia (kolejność zachowana)")
    ap.add_argument("--wait", type=float, default=2.0, help="pauza po każdym kliku (s)")
    ap.add_argument("--settle", type=float, default=6.0, help="czekanie na start gry (s)")
    ap.add_argument("--key", action="append", default=[], help="klawisz do przytrzymania w teście WASD")
    ap.add_argument("--hold", type=float, default=2.0, help="czas trzymania klawisza (s)")
    ap.add_argument("--viewport", default="1280x800")
    args = ap.parse_args()

    w, h = (int(x) for x in args.viewport.split("x"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--window-size=%d,%d" % (w + 16, h + 80)])
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(args.url, wait_until="load", timeout=60000)
        page.evaluate(INJECT)
        page.wait_for_timeout(int(args.settle * 1000))

        dump(page, args.out + "-1-start")

        for i, sel in enumerate(args.click):
            try:
                page.click(sel, timeout=5000)
                print(f"[click {i}] OK {sel}")
            except Exception as e:
                print(f"[click {i}] FAIL {sel}: {e}")
            page.wait_for_timeout(int(args.wait * 1000))
            dump(page, f"{args.out}-2-{i}-after-{i}")

        if args.key:
            held = args.key
            print(f"[keytest] przytrzymuję {held} na {args.hold}s")
            page.evaluate("(codes) => codes.forEach(c => window.__pb.down(c))", held)
            page.wait_for_timeout(int(args.hold * 1000))
            dump(page, f"{args.out}-3-after-keys")
            page.evaluate("() => window.__pb.upAll()")

        page.wait_for_timeout(1500)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
