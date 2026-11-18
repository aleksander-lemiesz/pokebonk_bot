# -*- coding: utf-8 -*-
"""Szybka analiza logów bota: statystyki per-run + wzorce śmierci.

Użycie:
    python analyze_log.py                # najnowszy log
    python analyze_log.py logs\bot-XXXX.log
"""
import re
import sys
import glob
import os
import io

RX_RUN = re.compile(r"=== run #(\d+)/(\d+) ===")
RX_DIED = re.compile(r"run #(\d+) status: died")
RX_DONE = re.compile(r"run #(\d+) status: (finished|completed|survived)")
RX_KILLS = re.compile(r"zab=(-?\d+)")
RX_FLIP = re.compile(r"\+(\d+) prze\w+cze\w+ (?:trybu|stanu)")
RX_DEATH = re.compile(r"timer zamarz\w+ (\d+:\d+(?:\.\d+)?) \+ brak HP")
RX_POLOW = re.compile(r"polow: cel d=(\d+)")
RX_CAPTURE = re.compile(r"E: capture")
RX_ULT = re.compile(r"R: ultimate")
RX_DASH = re.compile(r"Shift: ślizg")
RX_ST = {
    "engage": re.compile(r"stan: engage"),
    "reset": re.compile(r"stan: reset"),
    "farm": re.compile(r"stan: farm"),
    "explore": re.compile(r"stan: explore"),
}
RX_BLOCK = re.compile(r"blokada")

# BENCHMARK GRACZA (run ręczny 2026-11-18, Viridian Forest Zone 2 Normal, Season 3):
# 8:47, 1.85k KOS (~210/min), 747.5k dmg, 20 species (2 nowe: Squirtle, Caterpie),
# gracz przeżywa do końca runu (nie ginie na rekordzie).
# Podział dmg: Ultimate 48%, statusy/pasywy 20%, Fire Spin 13%, Support Alphas 10%
# (złapane wcześniej Pokémony walczą W RUNIE — połów = stałe wzmocnienie).
REF_TIME_S = 8 * 60 + 47
REF_KILLS = 1850
REF_KPM = REF_KILLS / (REF_TIME_S / 60.0)


def tsecs(s: str) -> float | None:
    m = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", s or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + float(m.group(2))

def analyze(path: str) -> None:
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    runs = []
    cur = None
    for ln in lines:
        m = RX_RUN.search(ln)
        if m:
            cur = {"n": int(m.group(1)), "deaths": 0, "blocks": 0, "flips": 0,
                   "polow": 0, "capture": 0, "ult": 0, "dash": 0,
                   "engage": 0, "reset": 0, "farm": 0, "explore": 0,
                   "max_kills": -1, "death_time": "", "last_timer": ""}
            runs.append(cur)
            continue
        if cur is None:
            continue
        if RX_DIED.search(ln):
            cur["deaths"] += 1
            md = RX_DEATH.search(ln)
            if md:
                cur["death_time"] = md.group(1)
        elif RX_DONE.search(ln):
            cur["finished"] = True
        if RX_BLOCK.search(ln):
            cur["blocks"] += 1
        mf = RX_FLIP.search(ln)
        if mf:
            cur["flips"] += int(mf.group(1))
        if RX_POLOW.search(ln):
            cur["polow"] += 1
        if RX_CAPTURE.search(ln):
            cur["capture"] += 1
        if RX_ULT.search(ln):
            cur["ult"] += 1
        if RX_DASH.search(ln):
            cur["dash"] += 1
        for k_name, rx_st in RX_ST.items():
            if rx_st.search(ln):
                cur[k_name] += 1
                break
        mk = RX_KILLS.search(ln)
        if mk:
            k = int(mk.group(1))
            if k > cur["max_kills"]:
                cur["max_kills"] = k
        mt = re.search(r"timer=(\d+:\d+(?:\.\d+)?)", ln)
        if mt:
            cur["last_timer"] = mt.group(1)
    print(f"== {os.path.basename(path)} ==")
    for r in runs:
        status = "DIED@" + (r["death_time"] or r["last_timer"]) if r["deaths"] else "FINISHED"
        print(f"run #{r['n']}: {status:18s} zab={r['max_kills']:4d} "
              f"blokady={r['blocks']:3d} zm.stanu={r['flips']:3d} "
              f"polow={r['polow']:2d} capture={r['capture']} R={r['ult']} dash={r['dash']} "
              f"eng/reset/farm/exp={r['engage']}/{r['reset']}/{r['farm']}/{r['explore']}")
    if not runs:
        print("(brak runów)")
    else:
        print(f"-- benchmark gracz: {REF_KILLS} zab / {REF_TIME_S // 60}:{REF_TIME_S % 60:02d} "
              f"(~{REF_KPM:.0f}/min) --")
        for r in runs:
            tsec = tsecs(r["death_time"] or r["last_timer"]) or 0.0
            kpm = r["max_kills"] / (tsec / 60.0) if tsec > 0 else 0.0
            red = REF_KPM / kpm if kpm > 0 else float("inf")
            print(f"        run #{r['n']}: {tsec:5.0f}s, {kpm:5.1f} zab/min "
                  f"(gracz {red:.0f}× szybszy)")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        analyze(sys.argv[1])
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "bot-*.log")
        newest = max(glob.glob(base), key=os.path.getmtime)
        analyze(newest)
