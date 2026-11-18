# -*- coding: utf-8 -*-
"""Szukanie w zminifikowanych plikach gry: gdzie definiowany jest backend 'local'."""
import io
import pathlib

BASE = pathlib.Path(r"C:\Users\aleks\AppData\Local\Temp\opencode\pokebonk-recon")
PATTERNS = [r"['\"`]local['\"`]", r"api\.pokebonk", r"backend\s*=", r"new\s+\w+Backend"]

for name in ["game.js", "menus.js"]:
    src = io.open(BASE / name, encoding="utf-8", errors="replace").read()
    for pat in PATTERNS:
        hits = list(__import__("re").finditer(pat, src))
        print(f"== {name} | {pat} | {len(hits)}")
        for m in hits[:8]:
            s = max(0, m.start() - 170)
            frag = src[s:m.end() + 170].replace("\n", " ")
            print("   ...", frag[:360])
            print("   ---")
