# -*- coding: utf-8 -*-
"""Analiza 2: minimapa (DOM?), spawn wrogów, crowd, krzywa XP, merge gemów."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
src = io.open(BASE / "game.js", encoding="utf-8", errors="replace").read()
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")

KEYWORDS = [
    ("map-canvas", 2), ("mapCanvas", 2), ("mapCanvasEl", 1),
    ("getContext", 4), ("getImageData", 2),
    ("showMinimap", 1), ("mapIcons", 1),
    ("toSpawn", 2), ("startWave", 2),
    ("crowdX", 2), ("separation", 2),
    ("merge", 2),
    ("xpChain", 1),
    ("levelUp", 1), ("pendingLevels", 1),
]

for kw, n in KEYWORDS:
    pat = re.escape(kw)
    hits = list(re.finditer(pat, src))
    print(f"== {kw} | {len(hits)}")
    for m in hits[:n]:
        s = max(0, m.start() - 140)
        frag = src[s:m.end() + 280].replace("\n", " ")
        print("   ...", enc(frag[:400]))
        print("   ---")
