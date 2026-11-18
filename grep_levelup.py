# -*- coding: utf-8 -*-
"""Grep: implementacja showLevelUp — klasy kart wyboru ulepszeń."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
src = io.open(BASE / "game.js", encoding="utf-8", errors="replace").read()
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")

for pat in [r"showLevelUp\s*\(", r"reroll"]:
    hits = list(re.finditer(pat, src))
    print(f"== {pat} | {len(hits)}")
    for m in hits[:6]:
        s = max(0, m.start() - 150)
        print("   ...", enc(src[s:m.end() + 350].replace("\n", " ")[:500]))
        print("   ---")
