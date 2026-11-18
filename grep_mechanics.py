# -*- coding: utf-8 -*-
"""Analiza mechanik gry: spawn, pickupy, magnet, minimapa, altar, ult."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
src = io.open(BASE / "game.js", encoding="utf-8", errors="replace").read()
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")

KEYWORDS = [
    ("minimap", 3), ("miniMap", 3),
    ("xpGem", 2), ("xpOrb", 2), ("magnet", 2),
    ("vitamin", 2), ("candy", 2), ("coin", 2), ("chest", 2),
    ("spawnPos", 3), ("spawnRadius", 2),
    ("nearest(", 2),
    ("altar", 2), ("shrine", 2),
    ("autoUlt", 2), ("ultReady", 2),
]

for kw, n in KEYWORDS:
    pat = re.escape(kw)
    hits = list(re.finditer(pat, src, re.IGNORECASE if kw in ("minimap", "shrine") else 0))
    print(f"== {kw} | {len(hits)}")
    for m in hits[:n]:
        s = max(0, m.start() - 130)
        frag = src[s:m.end() + 260].replace("\n", " ")
        print("   ...", enc(frag[:380]))
        print("   ---")
