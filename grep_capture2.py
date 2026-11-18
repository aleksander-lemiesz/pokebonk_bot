# -*- coding: utf-8 -*-
"""Analiza 5: stan weakened, kolor celu na minimapie, logika strzały kompasu."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
src = io.open(BASE / "game.js", encoding="utf-8", errors="replace").read()
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")

for kw, n in [
    ("weakened", 4),      # przejście roaming->weakened (próg HP?)
    (".compass", 2),      # strzała — kiedy widoczna, jaki transform
    ("captureMinimap", 1),
    ("`cap`", 2),
    ("captureColor", 2),
    ("ballUsed", 1),
    ("chestsBought", 1),  # ile balli mamy?
]:
    hits = list(re.finditer(re.escape(kw), src))
    print(f"== {kw} | {len(hits)}")
    for m in hits[:n]:
        s = max(0, m.start() - 160)
        frag = src[s:m.end() + 340].replace("\n", " ")
        print("   ...", enc(frag[:470]))
        print("   ---")
