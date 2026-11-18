# -*- coding: utf-8 -*-
"""Analiza 4: mechanika łapania — capture, pokeball, weaken, prompt w DOM."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
src = io.open(BASE / "game.js", encoding="utf-8", errors="replace").read()
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")

for kw, n in [("k.Capture", 6), ("capture", 6), ("catchT", 3), ("throwBall", 3),
              ("balls", 3), ("ballCount", 3), ("captureable", 2), ("capturable", 4),
              ("cp-btn", 3), ("capture-btn", 2), ("prompt", 3)]:
    hits = list(re.finditer(re.escape(kw), src))
    print(f"== {kw} | {len(hits)}")
    for m in hits[:n]:
        s = max(0, m.start() - 150)
        frag = src[s:m.end() + 320].replace("\n", " ")
        print("   ...", enc(frag[:430]))
        print("   ---")
