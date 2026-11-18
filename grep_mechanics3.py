# -*- coding: utf-8 -*-
"""Analiza 3: minimapa DOM — wrogowie/pickupy jako elementy? Promień spawnu."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
src = io.open(BASE / "game.js", encoding="utf-8", errors="replace").read()
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")

for kw, n in [("minimap", 21), ("spawnTick", 2), ("mmDot", 2), ("dot", 3), ("blip", 2)]:
    hits = list(re.finditer(re.escape(kw), src, 0 if kw.islower() else re.IGNORECASE))
    print(f"== {kw} | {len(hits)}")
    for m in hits[:n]:
        s = max(0, m.start() - 150)
        frag = src[s:m.end() + 320].replace("\n", " ")
        print("   ...", enc(frag[:440]))
        print("   ---")
