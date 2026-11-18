# -*- coding: utf-8 -*-
"""Grep klas UI: ekran level-upa i wyboru startera w zminifikowanym kodzie."""
import io
import pathlib
import re

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
PATTERNS = [
    r"levelup\(",
    r".{100}skipEm\w*.{250}",
    r"class:`(?:lu|lv|ch|pk|rr|st)[-\w]*`",
    r".{100}[Ss]tarter.{250}",
    r".{100}roster.{250}",
]
enc = lambda s: s.encode("cp1250", "replace").decode("cp1250")
for name in ["game.js", "menus.js"]:
    src = io.open(BASE / name, encoding="utf-8", errors="replace").read()
    for pat in PATTERNS:
        hits = list(re.finditer(pat, src))
        print(f"== {name} | {pat[:40]} | {len(hits)}")
        for m in hits[:4]:
            s = max(0, m.start() - 120)
            frag = src[s:m.end() + 120].replace("\n", " ")
            print("   ...", enc(frag[:340]))
            print("   ---")
