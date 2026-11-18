# -*- coding: utf-8 -*-
"""
PokeBonk Bot v2 — auto-play na koncie użytkownika (online).

Maszyna stanów:
  AUTH  -> czeka na ręczne logowanie (Turnstile rozwiązujesz TY)
  MENU  -> klika Play Classic / Play Survival / potwierdzenia, aż znikną markery menu
  RUN   -> AI na minimapie: ucieczka od wrogów, zbieranie pickupów, interakcje
           [E] (łapanie Pokémonów, jajka, skrzynie, altary) + karty level-upa.
  Koniec runu / powrót do menu -> następny run.

Uruchomienie:
  python bot.py                # jeden run
  python bot.py --runs 3       # trzy runy pod rząd
  python bot.py --mode endless # 'survival' (domyślnie) lub 'endless'
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import re
import sys
import time

from playwright.sync_api import Page, sync_playwright

ROOT = pathlib.Path(__file__).parent
PROFILE = ROOT / "chrome-profile"
LOGS = ROOT / "logs"
OUTS = ROOT / "out"
SHOTS = ROOT / "shots"
GAME_URL = "https://pokebonk.com/?lang=en"

BTN_AUTH = re.compile(r"play as a guest|create account|enter\b", re.I)
# Klikalne przyciski menu (kolejność matter: najpierw ogólne Play, potem tryby)
BTN_MENU_PLAY = re.compile(r"play (classic|survival|endless)|\bplay\b|jugar|empezar", re.I)
BTN_CONFIRM = re.compile(r"\b(start|empezar|go|ok|confirm|confirmar|aceptar|continue|continuar)\b", re.I)
# Tylko twarde markery ekranu końcowego (HUD też ma przycisk 'continue'!)
BTN_RUN_END = re.compile(r"retry|reintentar|once more|otra vez|jugar de nuevo|back to menu|main menu|men[uú] principal", re.I)
FAIL_TEXT = re.compile(r"run checks failed|comprobaciones de la partida", re.I)
# Markery MENU GŁÓWNEGO: dopóki któreś widać, NIE jesteśmy w runie
MENU_MARKER = re.compile(r"play classic|play survival|endless mode|log out|play as a guest|treasures shop", re.I)
# Ekran League (wybór Pokémona) — też nie run
LEAGUE_MARKER = re.compile(r"the league|i choose you|add to favorites", re.I)
# Ekran wyboru mapy — też nie run
MAP_MARKER = re.compile(r"choose map", re.I)
# Marker "run trwa": HUD survivala pokazuje 'Phase 1/3' (league/mapa/menu tego nie mają)
RUN_MARKER = re.compile(r"phase\s+\d+\s*/\s*\d+", re.I)
# Modal 'wznów przerwaną run' (pojawia się gdy przeglądarka została zamknięta w trakcie runu)
RESUME_RX = re.compile(r"resume|restore|unfinished|continue run|wznów|wznow|przywr", re.I)
# Przycisk 'Take' w popupie znalezionego przedmiotu (świat stoi dopóki popup otwarty)
TAKE_RX = re.compile(r"\btake", re.I)

# Priorytety kart level-upa — BENCHMARK GRACZA (run 9:02, 3455 KOS, HP pełne):
# wygrywa tempo zabójstw (dmg/area/projekcje) + sustain (lifesteal/heal-on-hit),
# bo poziomy = moc = więcej zabójstw = level-upy (pętla dodatnia).
# UPGRADE istniejącego ruchu (np. "Vine Whip Level 1 → 2") daje bonusy z levels
# (+109% dmg / -25% cd / +3 hits przy Lv 8) — równie cenny jak nowy ruch.
CARD_SCORE = [
    (re.compile(r"\bUPGRADE\b", re.I), 5),                              # poziom ruchu = ogromny skok
    (re.compile(r"new move|nuevo movimiento|movement\b", re.I), 4),     # nowy atak = kolejny kanał DPS
    (re.compile(r"projectile|proyectil|pp max", re.I), 4),              # +1 pocisk do WSZYSTKICH ruchów
    (re.compile(r"area|área", re.I), 4),                                # AoE = tempo zabójstw
    (re.compile(r"status|sleep|root|stun|freeze|para", re.I), 3),       # statusy = 20% dmg gracza (root tłumu)
    (re.compile(r"lifesteal|robo de vida|on.?hit", re.I), 4),           # sustain per hit (jak Synthesis)
    (re.compile(r"attack speed|veloc\.? de ataque", re.I), 3),          # DPS
    (re.compile(r"damage|dañ|crít|crit|special|physical|ataque", re.I), 3),
    (re.compile(r"evol|evoluc", re.I), 3),
    (re.compile(r"heal|cura|regen|max hp|maxhp|ps\b|vitamin|vitamina", re.I), 2),  # utility heal
    (re.compile(r"evasion|evasión|dodge|speed|velocidad", re.I), 2),    # defensywa (X Defense u gracza 37%)
    (re.compile(r"xp|exp|experiencia|luck|suerte|coin|dólar|dolar|pick", re.I), 1),
]
# Karty "pułapki" — Zero korzyści bojowych (waluty, kosmetyki)
CARD_TRAP = re.compile(r"nugget|pokédollar|pokedollar|dólar|dolar\b", re.I)

# Odczyt minimapy: klastry kolorów (wrogowie/cel łapania/skrzynie/portal) względem gracza w centrum
MINIMAP_JS = r"""
() => {
  const c = document.querySelector('canvas.minimap');
  if (!c || !c.offsetWidth) return null;
  const ctx = c.getContext('2d');
  if (!ctx) return null;
  const W = c.width, H = c.height, CX = W/2, CY = H/2;
  const d = ctx.getImageData(0, 0, W, H).data;
  const buckets = {};
  for (let y = 0; y < H; y += 2) {
    for (let x = 0; x < W; x += 2) {
      const distC = Math.hypot(x - CX, y - CY);
      if (distC < 8) continue;               // marker gracza w centrum
      const i = (y * W + x) * 4;
      const r = d[i], g = d[i+1], b = d[i+2], a = d[i+3];
      if (a < 220) continue;
      const mx = Math.max(r,g,b), mn = Math.min(r,g,b);
      if (mx < 60) continue;                 // ciemne tło
      if (mx - mn < 40) continue;            // szarości/biel (siatka)
      const key = ((r>>5)<<10) | ((g>>5)<<5) | (b>>5);
      const bk = buckets[key] || (buckets[key] = {n:0, sx:0, sy:0, r:0, g:0, b:0});
      bk.n++; bk.sx += x; bk.sy += y; bk.r += r; bk.g += g; bk.b += b;
    }
  }
  const clusters = Object.values(buckets).filter(b => b.n >= 3).map(b => ({
    n: b.n,
    x: +(b.sx/b.n - CX).toFixed(1),          // offset od gracza (px, +x = prawo ekranu)
    y: +(b.sy/b.n - CY).toFixed(1),          // +y = dół ekranu
    col: '#' + [b.r,b.g,b.b].map(v => Math.round(v/b.n).toString(16).padStart(2,'0')).join(''),
  })).sort((a,b) => b.n - a.n).slice(0, 14);
  // Raycast v2: 16 promieni — dystans do KRAWĘDZI terenu metodą spadku luminancji.
  // Teren w mapie = jaśniejszy; poza mapą = przygaszony/mrok. Mierzymy jasność wzdłuż
  // promienia (uśredniona mała łuczka) i zapamiętujemy ostatni promień, gdzie teren
  // był wciąż jasny (>= 60% jasności przy graczu).
  const NDIR = 16, rays = new Array(NDIR).fill(0);
  for (let k = 0; k < NDIR; k++) {
    const a = k * 2 * Math.PI / NDIR;
    const lums = new Array(62).fill(0);
    for (let ri = 0; ri < 62; ri++) {
      const r = 10 + ri * 3;
      let s = 0, cnt = 0;
      for (let da = -0.07; da <= 0.071; da += 0.035) {
        const x = Math.round(CX + Math.cos(a + da) * r), y = Math.round(CY + Math.sin(a + da) * r);
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        const i = (y * W + x) * 4;
        if (d[i + 3] < 100) continue;
        s += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
        cnt++;
      }
      lums[ri] = cnt ? s / cnt : 0;
    }
    const base = Math.max(...lums.slice(0, 5)) || 1;   // jasność terenu przy graczu
    let far = 0;
    for (let ri = 0; ri < 62; ri++) {
      if (lums[ri] > base * 0.55) far = 10 + ri * 3;
    }
    rays[k] = Math.min(far, 195);
  }
  return { w: W, h: H, scale: (W/2)/60, clusters, rays, ndir: NDIR };  // 200px = 60 jednostek świata
}
"""


def parse_compass_angle(tr: str | None) -> float | None:
    """Kąt strzały kompasu (cel łapania) w radianach.
    Konwencja CSS: rotate(0) = strzała w górę ekranu, dodatnie = clockwise.
    matrix(a,b,...) -> theta = atan2(b, a). Do weryfikacji z logów."""
    if not tr or tr == "none":
        return None
    m = re.search(r"matrix\(([-\d.eE]+),\s*([-\d.eE]+)", tr)
    if m:
        return math.atan2(float(m.group(2)), float(m.group(1)))
    m = re.search(r"rotate\(([-\d.eE]+)", tr)
    if m:
        return math.radians(float(m.group(1)))
    return None


def classify_cluster(cl: dict) -> str:
    """Przybliżona klasyfikacja koloru klastra z minimapy (do kalibracji z logów)."""
    col = cl.get("col", "")
    try:
        r = int(col[1:3], 16)
        g = int(col[3:5], 16)
        b = int(col[5:7], 16)
    except Exception:
        return "other"
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 70 or mx - mn < 30:
        return "other"
    if r >= 170 and (r - g) >= 95 and g <= 115 and b <= 130:
        return "enemy"     # nasycona czerwień (#e8443a, #fe5a6a) — NIE brąz terenu
    if b > r and (b - g) >= 25:
        return "other"     # fiolet (#c88bfe, #c071ff) — drzewa/cień lasu, NIE cel łapania
    # Dzikie Pokémon na mapie (encounter) — jasny róż/pomarańcz, ähnny do cap ale
    # inny odcień (#fab4b4, #ffd13a...). Sprawdzane PRZED cap, żeby nie trafić do cap.
    if r >= 230 and g >= 150 and b >= 140 and b <= 210 and (r - g) >= 40:
        return "wild"      # dzikie Pokémon na mapie do spotkania i złapania
    if r >= 190 and g >= 140 and b >= 130 and (r - g) >= 14:
        return "cap"       # jasny róż/krem (#fac7d3, #ffefc7) — znacznik celu/skrzyni
    if r >= 200 and g >= 105 and b <= 110:
        return "pickup"    # złoto (#d8a831, #fe8a2a) — monety/xp (nie ciemny brąz terenu)
    if g >= 130 and (g - b) >= 30 and (g - r) >= 20:
        return "heal"      # zieleń — jagody/leczenie
    if b >= 130 and r <= 140:
        return "portal"    # niebieski — portal
    return "other"


HEAL_CARD = re.compile(r"heal|cura|regen|max hp|vitamin|vitamina", re.I)


def pick_best_card(cards: list[dict], hp_now) -> dict | None:
    """Najlepsza karta wg CARD_SCORE; przy niskim HP heal dostaje ogromny bonus,
    waluty/kosmetyki karą (zero wartości bojowej). Zwraca też wynik dla loga."""
    low = hp_now is not None and hp_now[0] < 45
    best, best_s = None, -1e9
    for c in cards:
        s = 0.0
        for rx, v in CARD_SCORE:
            if rx.search(c["text"]):
                s = max(s, float(v))
        if CARD_TRAP.search(c["text"]):
            s -= 2.0
        if low and HEAL_CARD.search(c["text"]):
            s += 6.0
        s += random.random() * 0.1
        c["score"] = int(s)
        if s > best_s:
            best, best_s = c, s
    return best if best is not None else (cards[0] if cards else None)


HUD_TIMER = re.compile(r"\b(\d{1,2}:\d{2})(?:\.\d)?\b")


def hud_timer(ui_text: str) -> str | None:
    m = HUD_TIMER.search(ui_text)
    return m.group(1) if m else None


def keys_from_vector(dx: float, dy: float) -> set[str]:
    """Wektor ekranowy (dx w prawo, dy w dół) -> klawisze (kamera: W=góra)."""
    keys: set[str] = set()
    if dy < -0.35:
        keys.add("w")
    if dy > 0.35:
        keys.add("s")
    if dx > 0.35:
        keys.add("d")
    if dx < -0.35:
        keys.add("a")
    return keys

MODE_CHOICE = {"survival": re.compile(r"play survival|survival", re.I), "endless": re.compile(r"endless mode|endless", re.I)}


SNAPSHOT = r"""
() => {
  const vis = (el) => !!(el && (el.offsetWidth || el.offsetHeight));
  const btns = [...document.querySelectorAll('button')].filter(vis).map(b => ({
    text: (b.textContent || '').trim().slice(0, 80),
    cls: String(b.className || ''),
    title: b.title || '',
    box: (() => { const r = b.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })(),
  }));
  const ui = document.getElementById('ui');
  // Karty level-upa żyją w kontenerze div.cards (ekran League używa ls-card — NIE łapać)
  const cards = [...document.querySelectorAll('.cards > *')]
    .filter(vis).slice(0, 8).map(c => ({
      text: (c.innerText || '').replace(/\s+/g, ' ').slice(0, 120),
      box: (() => { const r = c.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })(),
    }));
  return {
    t: performance.now(),
    btns,
    cards,
    uiText: ui ? (ui.innerText || '').replace(/\s+/g, ' ').slice(0, 500) : '',
    hidden: document.hidden,
    paused: (() => { const el = document.querySelector('.pz.panel, .panel.pz, .pz'); return !!(el && (el.offsetWidth || el.offsetHeight)); })(),
    prompt: (() => { const el = document.querySelector('.prompt'); return (el && el.offsetWidth) ? (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 140) : null; })(),
    compass: (() => {
      // game.js: DWA elementy — .compass (strzałka; cel POZA ekranem; rotate na
      // dzieci[0]=.cp-tip) i .compass.mktag (cel NA ekranie; pozycja=kierunek).
      // Stary querySelector('.compass') zawsze łapał strzałkę, więc gdy widoczny
      // był tylko mktag, kompas=None zawsze. Normalizuję oba do rotate(θrad)
      // (konwencja gry: θ = atan2(dy,dx) ekranu + PI/2 — tak ustawia .cp-tip).
      const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
      const arr = document.querySelector('.compass:not(.mktag)');
      const mk = document.querySelector('.compass.mktag');
      if (vis(arr)) {
        const tip = arr.children[0];
        return tip ? tip.style.transform : null;
      }
      if (vis(mk)) {
        const r = mk.getBoundingClientRect();
        const a = Math.atan2(r.top + r.height / 2 - innerHeight / 2,
                             r.left + r.width / 2 - innerWidth / 2);
        return `rotate(${a + Math.PI / 2}rad)`;
      }
      return null;
    })(),
    compass_d: (() => {
      // .cp-d (dzieci[2] strzałki / tekst mktaga) = dystans do celu w metrach
      const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
      const arr = document.querySelector('.compass:not(.mktag)');
      const mk = document.querySelector('.compass.mktag');
      if (vis(arr) && arr.children[2]) return (arr.children[2].textContent || '').trim();
      if (vis(mk)) return (mk.textContent || '').trim();
      return null;
    })(),
    hp: (() => { const m = ((ui && ui.innerText) || '').match(/^\s*(\d+)\s+Lv\s+(\d+)/); return m ? [+m[1], +m[2]] : null; })(),
  };
}
"""


class Bot:
    def __init__(self, runs: int, max_minutes: float, hz: float, mode: str):
        self.runs_n = runs
        self.max_minutes = max_minutes
        self.hz = hz
        self.mode = mode
        self.log = collections.deque(maxlen=600)
        self.pw = sync_playwright().start()
        PROFILE.mkdir(exist_ok=True)
        self._clean_shutdown_flag()
        launch_kwargs = dict(
            user_data_dir=str(PROFILE),
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=["--window-size=1320,920", "--lang=en", "--hide-crash-restore-bubble", "--no-first-run", "--no-default-browser-check",
                  # nie dław gry, gdy okno jest przykryte innymi:
                  "--disable-backgrounding-occluded-windows",
                  "--disable-background-timer-throttling",
                  "--disable-backgrounding-rendering",
                  "--disable-features=CalculateNativeWinOcclusion"],
            no_viewport=True,
        )
        self.launch_kwargs = launch_kwargs
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
            self.logf("uruchomiono systemowy Chrome (profil bota)")
        except Exception:
            self.ctx = self.pw.chromium.launch_persistent_context(**launch_kwargs)
            self.logf("uruchomiono wbudowany Chromium (profil bota)")
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        pages_info = [(p.url[:60]) for p in self.ctx.pages]
        self.logf(f"strony kontekstu: {len(self.ctx.pages)} {pages_info}")
        # Jeśli restoracja sesji zostawiła duplikaty, zostawiamy tylko stronę gry
        for p in self.ctx.pages[1:]:
            if "pokebonk" not in p.url:
                p.close()
                self.logf(f"zamknięto zbędną stronę: {p.url[:60]}")
        # Emulacja focusu: strona zawsze "ma focus" — blur nie czyści klawiszy,
        # gra nie pauzuje przy przełączaniu okien (cd. też: enable_focus_emulation)
        self.cdp = None
        self.enable_focus_emulation()

    def _clean_shutdown_flag(self) -> None:
        """Ustawia 'czyste zamknięcie' w profilu, żeby Chrome nie pokazywał bubble 'Przywrócić strony?'."""
        prefs = PROFILE / "Default" / "Preferences"
        try:
            import json as _json
            data = _json.loads(prefs.read_text(encoding="utf-8")) if prefs.exists() else {}
            prof = data.setdefault("profile", {})
            prof["exit_type"] = "Normal"
            prof["exited_cleanly"] = True
            prefs.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.logf("profil: znacznik czystego zamknięcia ustawiony")
        except Exception as e:
            self.logf(f"profil: nie udało się ustawić exit_type ({e})")

    def enable_focus_emulation(self) -> None:
        """CDP: strona zawsze uznaje, że ma focus (brak blur => gra nie czyści klawiszy ani nie pauzuje)."""
        try:
            if self.cdp is None:
                self.cdp = self.ctx.new_cdp_session(self.page)
            self.cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            self.logf("focus emulation: ON")
        except Exception as e:
            self.logf(f"focus emulation FAIL: {e}")

    def reopen_browser(self) -> None:
        """Restart przeglądarki (np. gdy okno zostało zamknięte w trakcie sesji)."""
        try:
            self.ctx.close()
        except Exception:
            pass
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(channel="chrome", **self.launch_kwargs)
            self.logf("restart: uruchomiono systemowy Chrome")
        except Exception:
            self.ctx = self.pw.chromium.launch_persistent_context(**self.launch_kwargs)
            self.logf("restart: uruchomiono wbudowany Chromium")
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.cdp = None
        self.open_game()

    # ------------------------------------------------------------------ util
    def logf(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log.append(line)
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = (sys.stdout.encoding or "utf-8")
            safe = line.encode(enc, "replace").decode(enc)
            print(safe, flush=True)

    def snap(self) -> dict:
        st = self.page.evaluate(SNAPSHOT)
        try:
            st["minimap"] = self.page.evaluate(MINIMAP_JS)
        except Exception:
            st["minimap"] = None
        return st

    def save_state(self, label: str) -> None:
        OUTS.mkdir(exist_ok=True)
        SHOTS.mkdir(exist_ok=True)
        try:
            data = self.snap()
            (OUTS / f"{label}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            self.page.screenshot(path=str(SHOTS / f"{label}.png"))
        except Exception as e:
            self.logf(f"save_state FAIL {label}: {e}")

    def click_box(self, box) -> None:
        x, y, w, h = box
        self.page.mouse.click(x + w / 2, y + h / 2)

    def click_btn(self, b: dict) -> None:
        self.click_box(b["box"])

    def click_text(self, text: str) -> bool:
        loc = self.page.get_by_text(text, exact=False).first
        try:
            box = loc.bounding_box(timeout=2000)
        except Exception:
            box = None
        if box:
            self.click_box(box)
            return True
        return False

    # ------------------------------------------------------------- fazy gry
    def open_game(self) -> None:
        self.page.goto(GAME_URL, wait_until="load", timeout=60000)
        self.page.bring_to_front()
        self.page.wait_for_timeout(6000)
        self.enable_focus_emulation()  # upewnij się po nawigacji
        self.logf("gra wczytana")

    def is_auth_screen(self, st: dict) -> bool:
        return any(BTN_AUTH.search(b["text"]) for b in st["btns"])

    def in_menu(self, st: dict) -> bool:
        if MENU_MARKER.search(st["uiText"]) or LEAGUE_MARKER.search(st["uiText"]) or MAP_MARKER.search(st["uiText"]):
            return True
        # ekrany League/mapy rozpoznajemy też po charakterystycznych przyciskach
        return any("ls-go" in b["cls"] or "ls-tile" in b["cls"] or "rg-item" in b["cls"] or "rg-realm" in b["cls"] for b in st["btns"])

    def wait_for_human_auth(self, timeout_s: float = 1200.0) -> bool:
        t0 = time.time()
        hinted = False
        while time.time() - t0 < timeout_s:
            st = self.snap()
            if not self.is_auth_screen(st) and self.in_menu(st):
                self.logf("auth zaliczone — jesteś w menu gry")
                return True
            if not hinted:
                print("\n" + "=" * 62)
                print(" TERMINAL: zaloguj się w oknie przeglądarki bota.")
                print(" Kliknij 'Play as a guest!' albo zaloguj na swoje konto.")
                print(" (Turnstile przepuszczają ludzie — klikasz TY)")
                print("=" * 62 + "\n")
                hinted = True
            time.sleep(2)
        self.logf("timeout oczekiwania na logowanie")
        return False

    def menu_step(self, st: dict) -> str:
        """Jeden krok przez menu. Zwraca co zrobił."""
        # -3) Popup przedmiotu w trakcie runu (świat stoi): klik 'Take' — nigdy Sell.
        if RUN_MARKER.search(st["uiText"]):
            take = next((b for b in st["btns"] if TAKE_RX.search(b["text"]) and "ghost" not in b["cls"]), None)
            if take:
                self.logf("popup runu: klikam 'Take'")
                self.click_btn(take)
                self.page.wait_for_timeout(900)
                return "popup-take"

        # -2) Modal 'wznów przerwaną run' (po zamknięciu przeglądarki w trakcie runu):
        #     kliknij Resume zanim cokolwiek innego — inaczej zakrywa menu i zjada kliknięcia.
        for b in st["btns"]:
            if RESUME_RX.search(b["text"]) and not re.search(r"log out|language|shop", b["text"], re.I):
                self.logf(f"resume: klikam {b['text'][:40]!r}")
                self.click_btn(b)
                self.page.wait_for_timeout(2500)
                return "resume"

        # -1) Ekran wyników po runie: Retry / Back to menu
        for b in st["btns"]:
            if BTN_RUN_END.search(b["text"]):
                self.logf(f"wyniki: klikam {b['text'][:30]!r}")
                self.click_btn(b)
                self.page.wait_for_timeout(2000)
                return "run-end"

        # 0) Ekran mapy: wybierz odblokowaną mapę + tryb Survival + Start!
        if MAP_MARKER.search(st["uiText"]) or any("rg-item" in b["cls"] for b in st["btns"]):
            return self.map_step(st)

        # 0b) Ekran League (wybór Pokémona): klikamy 'I choose you!' — start runu
        try:
            go = self.page.locator("button.ls-go").first
            if go.is_visible():
                self.logf("league: klikam 'I choose you!' — start")
                try:
                    go.click(timeout=4000)
                except Exception as e:
                    self.logf(f"league: go.click FAIL ({e}) — próbuję mouse.click")
                    box = go.bounding_box()
                    if box and box.get("width"):
                        self.click_box([box["x"], box["y"], box["width"], box["height"]])
                self.page.wait_for_timeout(3000)
                return "league-go"
        except Exception as e:
            self.logf(f"league: check FAIL: {e}")

        # 1) wybór trybu po KLASIE elementu (teksty menu są sklejone: "PlayClassic mode")
        #    classic -> mm-item play | survival -> mm-item endless (bez coop)
        if self.mode == "survival":
            sel = "button.mm-item.endless:not(.coop)"
        else:
            sel = "button.mm-item.play"
        try:
            loc = self.page.locator(sel).first
            if loc.is_visible():
                self.logf(f"menu: klikam tryb ({sel})")
                clicked = True
                try:
                    loc.click(timeout=4000)
                except Exception:
                    clicked = False
                if not clicked:
                    self._mode_fail = getattr(self, "_mode_fail", 0) + 1
                    if self._mode_fail >= 2:
                        self.logf("menu: klik nie przechodzi — Escape (modal?) + klik współrzędny")
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(700)
                        box = loc.bounding_box()
                        if box and box.get("width"):
                            self.click_box([box["x"], box["y"], box["width"], box["height"]])
                        self._mode_fail = 0
                else:
                    self._mode_fail = 0
                self.page.wait_for_timeout(2200)
                return "mode"
            else:
                self.logf("menu: tryb niewidoczny wg is_visible (choć może być w snapie)")
        except Exception as e:
            self.logf(f"menu: mode check FAIL: {e}")

        # 2) potwierdzenia (start / continue / ok) — nie dotykamy Log out / Language / Shop
        for b in st["btns"]:
            if BTN_CONFIRM.search(b["text"]) and not re.search(r"log out|language|shop|treasures|fullscreen|glossary|favorites|moves|plan|leaderboard", b["text"], re.I):
                self.logf(f"menu: klikam potwierdzenie {b['text']!r}")
                self.click_btn(b)
                self.page.wait_for_timeout(2000)
                return "confirm"

        # 2b) wyjście ze sklepu (Treasures Shop / "Talk to Meowth (shop)") —
        # przyciski sklepu są wykluczone z potwierdzeń powyżej, więc szukamy
        # Back/Close/Exit osobiście; jeśli nie ma, Escape; jeśli_escape nie pomaga,
        # klikamy w lewy górny róg ekranu (typowe miejsce przycisku zamknięcia).
        is_shop = re.search(r"shop|treasure|meowth", st["uiText"], re.I) or \
            any(re.search(r"shop|treasure", b.get("cls", ""), re.I) for b in st["btns"])
        if is_shop:
            shop_exit = next((b for b in st["btns"]
                              if re.search(r"(back|close|exit|leave|^\s*x\s*$|done|ok|menu)", b["text"].strip(), re.I)
                              and "ghost" not in b["cls"]), None)
            if shop_exit:
                self.logf(f"sklep: wychodzę klikając {shop_exit['text'][:30]!r}")
                self.click_btn(shop_exit)
                self.page.wait_for_timeout(2000)
                return "shop-exit"
            # Escape jako fallback (czasem zamyka dialog sklepu)
            self.logf("sklep: brak przycisku wyjścia — Escape")
            sync_keys(set())
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(1500)
            return "shop-escape"
        return "unknown"

    def map_step(self, st: dict) -> str:
        """Ekran 'Choose map': region -> mapa -> tryb -> Start!"""
        # region (Gen 1 Kanto / Gen 2 Johto) — aktywny ma 'on'
        realm_on = next((b for b in st["btns"] if "rg-realm" in b["cls"] and "on" in b["cls"]), None)
        if realm_on is None:
            realm_any = next((b for b in st["btns"] if "rg-realm" in b["cls"]), None)
            if realm_any:
                self.logf(f"mapa: wybieram region {realm_any['text'][:20]!r}")
                self.click_btn(realm_any)
                self.page.wait_for_timeout(1200)
                return "map-realm"
            return "unknown"
        # mapa w aktywnym regionie — 'on' = wybrana; wybieramy pierwszą odblokowaną
        item_on = next((b for b in st["btns"] if "rg-item" in b["cls"] and "on" in b["cls"]), None)
        if item_on is None:
            item_free = next((b for b in st["btns"] if "rg-item" in b["cls"] and "locked" not in b["cls"]), None)
            if item_free:
                self.logf(f"mapa: wybieram {item_free['text'][:30]!r}")
                self.click_btn(item_free)
                self.page.wait_for_timeout(1200)
                return "map-item"
            self.logf("mapa: wszystkie mapy zablokowane?!")
            return "unknown"
        # tryb gry: w survival chcemy kafel 'rg-mode on endless'
        modes = [b for b in st["btns"] if "rg-mode" in b["cls"]]
        if modes:
            want_endless = self.mode == "survival"
            target = next((b for b in modes if "endless" in b["cls"]), modes[0])
            is_on = "on" in target["cls"]
            is_endless = "endless" in target["cls"]
            if is_on and (is_endless == want_endless):
                pass  # ustawione poprawnie
            else:
                self.logf(f"mapa: przełączam tryb na {'Survival' if want_endless else 'Classic'}")
                self.click_btn(target)
                self.page.wait_for_timeout(1000)
                return "map-mode"
        start = next((b for b in st["btns"] if b["cls"].startswith("btn big red")), None)
        if start:
            self.logf("mapa: klikam Start!")
            self.click_btn(start)
            self.page.wait_for_timeout(4000)
            return "map-start"
        return "unknown"

    def run_menu_phase(self, timeout_s: float = 180.0) -> bool:
        """Klika menu aż pojawi się HUD runu (RUN_MARKER) — czyli run faktycznie trwa."""
        t0 = time.time()
        unknown_streak = 0
        it = 0
        last_ui: str | None = None
        same_ui = 0
        while time.time() - t0 < timeout_s:
            it += 1
            st = self.snap()
            # Wiszące KARTY / modal "Got it" z poprzedniego runu (np. po stallsie):
            # bez tego pętla liczyła 30× 'nieznany ekran' i poddawała sesję.
            got = next((b for b in st["btns"]
                        if re.match(r"^(got it|ok|entendido|understood)\W*$", b["text"].strip(), re.I)
                        and "ghost" not in b["cls"]), None)
            if got:
                self.logf(f"menu: zamykam modal {got['text'][:20]!r}")
                self.click_btn(got)
                self.page.wait_for_timeout(700)
                continue
            if st["cards"]:
                self.logf("menu: wiszące karty level-upa — wybieram pierwszą")
                m1 = re.search(r"\[(\d)\]\s*$", st["cards"][0]["text"])
                if m1:
                    self.page.keyboard.press(m1.group(1))
                else:
                    x, y, w, h = st["cards"][0]["box"]
                    self.page.mouse.move(x + w / 2, y + h / 2)
                    self.page.wait_for_timeout(120)
                    self.page.mouse.click(x + w / 2, y + h / 2)
                self.page.wait_for_timeout(900)
                continue
            # HUD runu + żywy Pokémon + timer, który FAKTYCZNIE idzie
            # (ekran śmierci/popup: 'Phase X/3' zostaje, ale HP znika albo timer stoi)
            if RUN_MARKER.search(st["uiText"]) and st.get("hp"):
                t_a = hud_timer(st["uiText"])
                self.page.wait_for_timeout(1300)
                st2 = self.snap()
                t_b = hud_timer(st2["uiText"])
                if t_a != t_b and RUN_MARKER.search(st2["uiText"]):
                    self.logf(f"[menu it{it}] HUD runu wykryty — run trwa")
                    return True
                self.logf("HUD jak w runie, ale timer stoi / HP znika — popup albo ekran śmierci")
            # Ekran nie reaguje (np. modal 'wznów grę' zakrywa przyciski) → Escape + dump
            if st["uiText"] == last_ui:
                same_ui += 1
            else:
                same_ui = 0
                last_ui = st["uiText"]
            if same_ui == 5:
                self.logf("menu: ekran nie reaguje — Escape (modal/popup?)")
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(700)
            if same_ui >= 8 and same_ui % 4 == 0:
                self.save_state(f"menu-stall-{int(time.time())}")
            if it <= 6 or it % 5 == 0:
                self.logf(f"[menu it{it}] ui={st['uiText'][:90]!r}")
            res = self.menu_step(st)
            if res == "unknown":
                unknown_streak += 1
                if unknown_streak == 1 or unknown_streak % 3 == 0:
                    self.logf(f"menu: nieznany ekran (próba {unknown_streak})")
                    self.save_state(f"menu-unknown-{int(time.time())}")
                if unknown_streak == 2:
                    # może wisiały popup/news — spróbuj Escape
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(800)
                if unknown_streak >= 30:
                    self.logf("menu: 30 nieznanych ekranów — poddaję tę fazę")
                    return False
                time.sleep(1.5)
            else:
                unknown_streak = 0
        self.logf("timeout fazy menu")
        return False

    # -------------------------------------------------------------- w runie
    CAPTURE_RX = re.compile(r"lanzar|throw|arrojar|rzut|atrapar|catch|pok[eé].?ball|amigo.?ball|luchar|alli[eé]?s|apoyo|equipo|friend.?ball|friend", re.I)  # rzut ball'em: zwykłe złapanie (Lanzar) LUB sojusznik z osłabionej Alfy (Amigo Ball / Friend Ball: ... this run)
    OPEN_RX = re.compile(r"open|abrir", re.I)                    # otwarcie ball-itemu/skrzyni (popup!)
    EGG_RX = re.compile(r"egg|huevo", re.I)
    CHEST_RX = re.compile(r"chest|cofre|key|llave", re.I)
    ALTAR_RX = re.compile(r"altar", re.I)
    PORTAL_RX = re.compile(r"portal", re.I)
    GENERIC_E_RX = re.compile(r"\[e\]|press|teclea|aprieta", re.I)
    # Dzikie Pokémon na mapie (encounter) — prompt typu "Wake Snorlax",
    # "Encuentro", "Pokémon salvaje", itp.
    WILD_RX = re.compile(r"wild|encounter|salvaje|encontrar|wake|dziki|spotka|pok[eé]mon", re.I)

    def play_run(self) -> str:
        dt = 1.0 / self.hz
        t0 = time.time()
        arc = 0.0
        turn_dir = 1
        held: set[str] = set()
        last_ui_log = 0.0
        last_mm_log = 0.0
        run_gone_t: float | None = None
        no_hp_t: float | None = None
        last_timer: str | None = None
        timer_frozen_t: float | None = None
        last_e_t = 0.0
        last_hp: int | None = None
        capture_count = 0
        kills = -1                       # zabójstwa z HUD ("SURVIVAL <czas> <N>")
        fight_kills0 = 0                 # zabójstwa przy wejściu do walki (pomiar skuteczności)
        cap_close_t = 0.0                # od kiedy bot stoi <=26 px od celu łapania
        cap_dump_t = 0.0                 # kiedy ostatni dump diag-a "cap bez prompta"
        wild_close_t = 0.0               # od kiedy bot stoi <=20 px od dzikiego Pokémon
        wild_dump_t = 0.0                # kiedy ostatni dump diag-a "wild bez prompta"
        strafe_t = 0.0                   # ostatni odskok-unik w ENGAGE (antypociskowy)
        last_mode_log = 0.0
        mode_skipped = 0                 # przełączenia stanu maskowane przez throttle
        hp_prev = None                   # HP z poprzedniego ticku (detektor burstu)
        enemy_pos_list = []              # pozycje wrogów z ostatniego ticku (kara w RESET)
        bot_state = "explore"            # FSM: engage / reset / farm / explore
        state_until = 0.0                # commitment: najwcześniejsza zmiana stanu
        st_reason = ""                   # powód ostatniego przejścia (log)
        prev_state_logged = ""           # ostatni zalogowany stan
        engage_min_d = 999.0             # min. dystans do celu w ENGAGE (anti-hover)
        engage_last_prog = 0.0           # czas ostatniego postępu w ENGAGE
        engage_tgt_d_last = 999.0        # dystans do wybranego celu z zeszłego ticku
        heading = [0.0, -1.0]          # aktualny kierunek biegu (ekranowe x,y; -y = góra)
        last_want: frozenset = frozenset()
        stuck_t = 0.0
        prev_static: list = []         # statyczne klastry z poprzedniej klatki (pomiar ruchu)
        vel_est = [0.0, 0.0]           # estymowana prędkość gracza (px minimapy / s)
        blocked_t = 0.0
        escape_t = 0.0
        escape_dir = [1.0, 0.0]
        edge_bias = [0.0, 0.0]         # odpychajka od ostatnio napotkanej krawędzi
        last_escape_t = 0.0
        escape_count = 0
        last_act_log = 0.0
        last_trap_dump = 0.0
        obj = None                     # aktualny cel aktywności (kind, x, y)
        target_t = 0.0
        last_measure_t = 0.0           # kiedy ostatnio zmierzono prędkość (>=2 dopasowań)
        no_block_until = 0.0           # spokój po manewrze ucieczki
        orbit_sign = 1.0 if random.random() < 0.5 else -1.0   # kierunek orbity (walka)
        last_orbit_flip = 0.0
        r_cd = 0.0                       # cooldown ultimate (R)
        dash_cd = 0.0                    # cooldown ślizgu (Shift)
        r_fire = False                   # flagi akcji na ten tick (konsumowane po sync_keys)
        dash_fire = False
        fight_prev = False
        last_escape_dir = [1.0, 0.0]   # ostatni kierunek ucieczki (anty-pętla)
        last_resync = 0.0
        last_popup_t = 0.0
        popup_tries = 0
        last_timer_moved: str | None = None
        card_stall = 0
        last_card_text: str | None = None
        last_hidden_log = 0.0

        def sync_keys(want: set[str]) -> None:
            for k in list(held - want):
                self.page.keyboard.up(k)
                held.discard(k)
            for k in want - held:
                self.page.keyboard.down(k)
                held.add(k)

        def finish(s: str, label: str) -> str:
            for k in list(held):
                self.page.keyboard.up(k)
                held.discard(k)
            if s in ("died", "ended"):
                # spróbuj kliknąć 'continue' na ekranie śmierci/wyników
                try:
                    for b in st["btns"]:
                        if BTN_CONFIRM.search(b["text"]) and not re.search(r"log out|shop|fullscreen|language", b["text"], re.I):
                            self.click_btn(b)
                            self.page.wait_for_timeout(1500)
                            break
                except Exception:
                    pass
            return s

        status = "timeout"
        while time.time() - t0 < self.max_minutes * 60:
            loop_t0 = time.time()
            try:
                st = self.snap()
            except Exception as e:
                self.logf(f"snap FAIL: {e}")
                status = "crashed"
                break

            if FAIL_TEXT.search(st["uiText"]):
                self.logf("SERWER ODRZUCIŁ RUN (Run checks failed) — zatrzymuję bota")
                self.save_state("run-checks-failed")
                status = "rejected"
                break

            # Karty level-upa: wybór wg CARD_SCORE (+heal przy niskim HP).
            # Drabinka ratunkowa: hover+klik -> inna karta / dolna część -> Escape -> przerwanie.
            hp = st.get("hp")
            if hp is not None:
                last_hp = hp[0]
            if st["cards"]:
                # TUTORIALOWY MODAL "Got it" NAD kartami (np. 'Ultimate ready!
                # Press R'): jesterties zjada kliki i Escape go ZWALNIERZ nie zamyka
                # (12 prób bez efektu → rekordowo run utknął na 1:06). Klikaj go
                # ZAWSZE na starcie, zanim cokolwiek innego.
                got = next((b for b in st["btns"]
                            if re.match(r"^(got it|ok|entendido|understood)\W*$", b["text"].strip(), re.I)
                            and "ghost" not in b["cls"]), None)
                if got:
                    self.logf(f"karty: zamykam modal {got['text'][:20]!r}")
                    sync_keys(set())
                    self.click_btn(got)
                    self.page.wait_for_timeout(700)
                    continue
                card = pick_best_card(st["cards"], hp)
                if card:
                    if card["text"] == last_card_text:
                        card_stall += 1
                    else:
                        card_stall = 0
                        last_card_text = card["text"]
                    # okno w tle / zminimalizowane — przyciągnij z powrotem (kliknięcia
                    # bywają ignorowane, gdy gra wewnętrznie zatrzymana po utracie focusu)
                    if st.get("hidden"):
                        self.logf("karty: strona w tle — bring_to_front")
                        try:
                            self.page.bring_to_front()
                        except Exception:
                            pass
                        self.enable_focus_emulation()
                        self.page.wait_for_timeout(400)
                    # pauza NA overlayem kart zjada kliknięcia — najpierw odpauzuj
                    if st.get("paused"):
                        self.logf("karty: pauza pod overlayem — Escape najpierw")
                        sync_keys(set())
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(700)
                        continue
                    # popup (Take/Sell) schowany POD kartami — kliknij najpierw jego
                    take = next((b for b in st["btns"] if TAKE_RX.search(b["text"]) and "ghost" not in b["cls"]), None)
                    if take and card_stall >= 2:
                        self.logf(f"karty: pod spodem popup — klikam 'Take' ({take['text'][:24]!r})")
                        sync_keys(set())
                        self.click_btn(take)
                        self.page.wait_for_timeout(900)
                        continue
                    if card_stall >= 6:
                        self.logf(f"karty: {card_stall} prób bez efektu — Escape + dump")
                        self.save_state("cards-stuck")
                        sync_keys(set())
                        self.page.keyboard.press("Escape")
                        self.page.wait_for_timeout(600)
                        if card_stall >= 12:
                            self.logf("karty: nie do zamknięcia po 12 próbach — przerywam run")
                            status = finish("stalled", "cards-stuck")
                            break
                        continue
                    sync_keys(set())
                    # SKRÓT KLAWISZOWY: karty mają etykiety [1]/[2]/[3] — wybór
                    # klawiszem omija hover/pointerawait i overlaye; mysz tylko
                    # dla kart bez skrótu albo po 3 nieudanych próbach
                    m_num = re.search(r"\[(\d)\]\s*$", card["text"])
                    if m_num and card_stall < 3:
                        self.page.keyboard.press(m_num.group(1))
                    elif card_stall >= 3:
                        # klikaj PO PRZEKŁADANIU: inna karta z rzędu, poza skrótem,
                        # w dolnej części kafla (tam zwykle siedzi klikalny przycisk)
                        alt = st["cards"][card_stall % len(st["cards"])]
                        bx, by, bw, bh = alt["box"]
                        px, py = bx + bw / 2, by + bh * 0.72
                        self.page.mouse.move(px, py)  # hover — część UI wymaga pointerover
                        self.page.wait_for_timeout(120)
                        self.page.mouse.click(px, py)
                    else:
                        x, y, w, h = card["box"]
                        px, py = x + w / 2, y + h / 2
                        self.page.mouse.move(px, py)
                        self.page.wait_for_timeout(120)
                        self.page.mouse.click(px, py)
                    if card_stall <= 2 or card_stall % 3 == 0:
                        self.logf(f"level-up: {card['text'][:44]!r} (hp={last_hp}, s={card.get('score', '?')}, próba {card_stall + 1})")
                    self.page.wait_for_timeout(900)
                continue
            else:
                card_stall = 0
                last_card_text = None

            # Pauza (np. po zminimalizowaniu okna) — odpauzowuj Escape'em
            if st.get("paused"):
                self.logf("pauza wykryta — odpauzowuję (Escape)")
                sync_keys(set())  # gra czyści stan klawiszy — next loop wciśnie od zera
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(800)
                continue

            # Tutorialowy modal "Got it" W BIEGU (np. 'Ultimate ready! Press R'):
            # zjada input i zamarza świat — kliknij natychmiast
            got = next((b for b in st["btns"]
                        if re.match(r"^(got it|ok|entendido|understood)\W*$", b["text"].strip(), re.I)
                        and "ghost" not in b["cls"]), None)
            if got:
                self.logf(f"modal: klikam {got['text'][:20]!r}")
                sync_keys(set())
                self.click_btn(got)
                self.page.wait_for_timeout(500)
                continue
            # POWIADOMIENIA typu "X joins your League" / "caught" — przycisk
            # potwierdzenia, którego dotąd bot nie znał (gracz: Charmander
            # 'joins your League' na ekranie wyników; w runie podobne toasty)
            notif = next((b for b in st["btns"]
                          if re.search(r"join|unirse|caught|atrapad|añad|a[nñ]ad|add to (team|league|party)", b["text"], re.I)
                          and "ghost" not in b["cls"]
                          and not re.search(r"favorites", b["text"], re.I)), None)
            if notif:
                self.logf(f"notyfikacja: klikam {notif['text'][:32]!r}")
                sync_keys(set())
                self.click_btn(notif)
                self.page.wait_for_timeout(700)
                continue

            # Samonaprawa po ręcznej aktywności użytkownika: co ~12 s odśwież stan
            # klawiszy (gra czyści je na blurze; użytkownik mógł wcisnąć swoje).
            if time.time() - last_resync > 12.0:
                last_resync = time.time()
                if held:
                    sync_keys(set())  # następny sync_keys(want) wciśnie od zera

            # Okno w tle/zminimalizowane (np. po aktywności użytkownika): przyciągnij
            # i ponów emulację focusu — bez tego gra bywa wewnętrznie zatrzymana.
            if st.get("hidden") and time.time() - last_hidden_log > 8.0:
                last_hidden_log = time.time()
                self.logf("strona w tle (document.hidden) — bring_to_front + focus emulation")
                try:
                    self.page.bring_to_front()
                    self.enable_focus_emulation()
                except Exception as e:
                    self.logf(f"bring_to_front FAIL: {e}")

            texts = {b["text"].lower() for b in st["btns"]}
            # Koniec runu: twarde markery ekranu końcowego ORAZ brak HUD runu
            if any(BTN_RUN_END.search(t) for t in texts) and not RUN_MARKER.search(st["uiText"]) and not st["cards"]:
                self.logf(f"koniec runu — przyciski: {sorted(t for t in texts if t)[:5]}")
                self.save_state("run-end")
                status = finish("ended", "run-end")
                break

            # HUD runu zniknął na dobre (ekran wyników bez 'Retry' albo powrót do menu):
            if not RUN_MARKER.search(st["uiText"]):
                if run_gone_t is None:
                    run_gone_t = time.time()
                elif time.time() - run_gone_t > 4:
                    self.logf("HUD runu zniknął (>4s) — run zakończony/śmierć")
                    self.save_state("run-gone")
                    status = finish("ended", "run-gone")
                    break
            else:
                run_gone_t = None

            in_run = RUN_MARKER.search(st["uiText"]) is not None

            # ŚMIERĆ #1: HUD bez HP ('111 Lv 1 ...' -> 'Lv 1 ...') przez >4.5 s.
            # WYJĄTEK: timer zamrożony (popup/pauza/przejście fazy — HP znika na
            # bannerze fazowym; tak zakończyła się faza 1 przy 5:00, nie śmiercią).
            if in_run and hp is None:
                frozen_now = timer_frozen_t is not None and (time.time() - timer_frozen_t) > 1.0
                if frozen_now:
                    no_hp_t = None  # przejście fazowe/popup — to nie jest śmierć
                elif no_hp_t is None:
                    no_hp_t = time.time()
                elif time.time() - no_hp_t > 4.5:
                    mk = re.search(r"SURVIVAL\s+(\d+:\d+(?:\.\d+)?)\s+(\d+)", st["uiText"])
                    self.logf(f"HUD bez HP >4.5s — POKÉMON MARTWY (przetrwano {hud_timer(st['uiText'])}, "
                              f"zab={int(mk.group(2)) if mk else '?'}, łapy: {capture_count})")
                    self.save_state("death")
                    status = finish("died", "death")
                    break
            else:
                no_hp_t = None

            # Zamarznięty timer:
            #  - żywy (hp jest)  -> popup przedmiotu/pauza: klik 'Take', ew. Escape
            #  - martwy (hp brak)-> śmierć (zapasowo; zwykle łapie to check wyżej)
            now_t = time.time()
            tm = hud_timer(st["uiText"])
            world_frozen = False
            if in_run and not st["cards"] and tm:
                if tm == last_timer:
                    if timer_frozen_t is None:
                        timer_frozen_t = time.time()
                    ft = time.time() - timer_frozen_t
                    world_frozen = ft > 1.0
                    if hp is not None and ft > 3.0 and now_t - last_popup_t > 2.5:
                        last_popup_t = now_t
                        take = next((b for b in st["btns"]
                                     if TAKE_RX.search(b["text"]) and "ghost" not in b["cls"]), None)
                        if take:
                            self.logf(f"popup: klikam 'Take' ({take['text'][:24]!r})")
                            sync_keys(set())
                            self.click_btn(take)
                            self.page.wait_for_timeout(900)
                        else:
                            popup_tries += 1
                            self.logf(f"świat zamarł (timer {tm}, hp={last_hp}) — Escape (popup/pauza) [{popup_tries}]")
                            sync_keys(set())
                            self.page.keyboard.press("Escape")
                            self.page.wait_for_timeout(700)
                        last_timer = None
                        timer_frozen_t = None
                        if popup_tries >= 4:
                            self.logf("świat zamarł trwale po 4 próbach — kończę run")
                            self.save_state("world-frozen")
                            status = finish("died", "world-frozen")
                            break
                    elif hp is None and ft > 15:
                        self.logf(f"timer zamarzł {tm} + brak HP — śmierć")
                        self.save_state("timer-frozen")
                        status = finish("died", "timer-frozen")
                        break
                else:
                    last_timer = tm
                    timer_frozen_t = None
                    if tm != last_timer_moved:
                        popup_tries = 0  # świat ruszył — licznik prób zerujemy
                    last_timer_moved = tm

            el = time.time() - t0

            # PROMPT [E]: łapanie Pokémonów, jajka, skrzynie, altary, portal
            # UWAGA: gra renderuje prompt rzutu przez ui.$q0hn8k() (nie przez div .prompt),
            # więc st.get("prompt") zawsze = None. Szukamy więc treści w uiText:
            # '[E] Lanzar Poké Ball' / '[E] Amigo Ball: luchará contigo ...'
            prompt = st.get("prompt")
            prompt_ui = st.get("uiText") or ""
            prompt_text = prompt if prompt else None
            if not prompt and re.search(r"\[E\]\s*(lanzar|amigo|throw|catch|pok[eé].?ball|luchar)", prompt_ui, re.I):
                prompt_text = prompt_ui  # fallback: prompt z uiText (nie ma div .prompt)
            now_t = time.time()
            # NIE wchodź do sklepu — prompt "Talk to Meowth (shop)" to pułapka;
            # bot klikał "SOLD" w kółko i utknął. Uciekaj zamiast klikać E.
            if prompt_text and re.search(r"shop|meowth", prompt_text, re.I):
                bot_state, state_until, st_reason = "reset", el + 1.2, "sklep-ucieczka"
                continue
            if prompt_text and now_t - last_e_t > 1.0:
                low = last_hp is not None and last_hp < 30
                kind = None
                if self.OPEN_RX.search(prompt_text):
                    kind = "chest"      # 'Open Poké Ball (free)' / skrzynia -> popup po E
                elif self.CAPTURE_RX.search(prompt_text):
                    kind = "capture"
                elif self.EGG_RX.search(prompt_text):
                    kind = "egg"
                elif self.ALTAR_RX.search(prompt_text):
                    kind = "altar"
                elif self.WILD_RX.search(prompt_text):
                    kind = "wild"      # dzikie Pokémon na mapie — prompt [E] do spotkania
                elif self.CHEST_RX.search(prompt_text):
                    kind = None if low else "chest"
                elif self.PORTAL_RX.search(prompt_text):
                    kind = None if low else "portal"
                elif self.GENERIC_E_RX.search(prompt_text):
                    kind = None if low else "e"
                if kind and not (kind != "capture" and low):
                    self.page.keyboard.press("e")
                    last_e_t = now_t
                    if kind == "capture":
                        capture_count += 1
                    self.logf(f"E: {kind} ({prompt_text[:56]!r})")
                    self.page.wait_for_timeout(120)

            # RUCH: najpierw estymacja FAKTYCZNEGO ruchu gracza (pasowanie statycznych
            # klastrów klatka-do-klatki — świat przesuwa się przeciwnie do ruchu gracza).
            # Wrogów pomijamy: ruszają się sami. Potem detekcja blokady (krawędź mapy!).
            mm = st.get("minimap")
            clusters = mm.get("clusters") if isinstance(mm, dict) else None
            if clusters:
                # Filtr artefaktów HUD: dolny-lewy róg canvasu minimapy zawiera stałe
                # elementy interfejsu (#fd695a w (18,183), ramka (3,181)/(8,184)) —
                # nigdy się nie ruszają i fałszywie liczą jako "wróg"/"obiekt".
                clusters = [c for c in clusters if not (c["x"] < 27 and c["y"] > 168)]
            used_mm = bool(clusters)
            ang = parse_compass_angle(st.get("compass"))

            cur_static = []
            for cl in clusters or []:
                k = classify_cluster(cl)
                if k != "enemy" and math.hypot(cl["x"], cl["y"]) < 160:
                    cur_static.append((k, cl["x"], cl["y"]))
            if prev_static and cur_static and escape_t <= 0:
                disp = []
                for (k0, x0, y0) in prev_static:
                    bd, best = 16.0, None
                    for (k1, x1, y1) in cur_static:
                        if k1 != k0:
                            continue
                        dd = math.hypot(x1 - x0, y1 - y0)
                        if dd < bd:
                            bd, best = dd, (x1, y1)
                    if best:
                        disp.append((best[0] - x0, best[1] - y0))
                if len(disp) >= 2:
                    xs = sorted(p[0] for p in disp)
                    ys = sorted(p[1] for p in disp)
                    ivx, ivy = -xs[len(xs) // 2] / dt, -ys[len(ys) // 2] / dt
                    if math.hypot(ivx, ivy) < 60:  # odfiltruj teleporty / zmiany strefy
                        vel_est[0] = 0.5 * vel_est[0] + 0.5 * ivx
                        vel_est[1] = 0.5 * vel_est[1] + 0.5 * ivy
                    last_measure_t = time.time()
                else:
                    vel_est[0] *= 0.85
                    vel_est[1] *= 0.85
            else:
                vel_est[0] *= 0.85
                vel_est[1] *= 0.85
            prev_static = cur_static

            # BLOKADA: klawisze wciśnięte, a gracz stoi w miejscu (krawędź mapy / drzewo).
            # Wymaga ŚWIEŻEGO pomiaru prędkości (>=2 dopasowanych obiektów w ostatnich 2 s)
            # — bez tego brak punktów odniesienia != blokada (to generowało fałszywe alarmy).
            measure_fresh = (time.time() - last_measure_t) < 2.0
            if held and escape_t <= 0 and not world_frozen and measure_fresh \
                    and time.time() >= no_block_until \
                    and math.hypot(vel_est[0], vel_est[1]) < 3.0:
                blocked_t += dt
            elif blocked_t > 0 and (not measure_fresh or not held):
                blocked_t = 0.0
            if (blocked_t > 1.3 or (stuck_t > 6.0 and not world_frozen)) and escape_t <= 0:
                now_e = time.time()
                repeated = (now_e - last_escape_t) < 6.0
                last_escape_t = now_e
                escape_count = escape_count + 1 if repeated else 1
                if escape_count >= 3:
                    # przypięty w rogu/kieszeni: wymuś RESET — walka tuż po manewrze
                    # pchała z powrotem pod ścianę (cykl blokada→walka→blokada)
                    bot_state = "reset"
                    state_until = el + 3.0
                    st_reason = "przypiecie"
                esc_src = "geom"
                rays_e = (mm.get("rays") if isinstance(mm, dict) else None) or []
                mx_r = max(rays_e) if rays_e else 0
                mn_r2 = min(rays_e) if rays_e else 0
                if rays_e and mx_r >= 100 and (mx_r - mn_r2) >= 25:
                    # uciekaj w ŚRODEK najbezpieczniejszego sektora: okno ważone ±2
                    # promienie — środkuje między dwoma otwartymi sektorami i NIGDY
                    # nie wybiera kierunku tuż przy granicy z murem (fix remisów
                    # "ray0 pod -0°", które ciągnęły bota wzdłuż ściany w kolejny mur)
                    nd_e = len(rays_e)
                    wsc = [rays_e[k] + 0.6 * rays_e[(k - 1) % nd_e] + 0.6 * rays_e[(k + 1) % nd_e]
                           + 0.25 * rays_e[(k - 2) % nd_e] + 0.25 * rays_e[(k + 2) % nd_e]
                           for k in range(nd_e)]
                    # Wrogowie blokują wyjścia z kieszeni: sektor z wrogiem <60 px
                    # dostaje karę — ucieczka "w otwartą stronę" kończyła się na
                    # wrogu stojącym w tunelu (run: [ray0] ×4 pod rząd przy ścianie)
                    for k in range(nd_e):
                        a_k = k * (2 * math.pi / nd_e)
                        for ed, ex, ey, _ew in enemy_pos_list:
                            ae = math.atan2(ey, ex)
                            da = math.atan2(math.sin(ae - a_k), math.cos(ae - a_k))
                            if abs(da) < 1.3 * (2 * math.pi / nd_e):
                                wsc[k] -= max(0.0, (60.0 - ed)) * 1.2
                    bk = max(range(nd_e), key=lambda i: wsc[i])
                    a = bk * (2 * math.pi / nd_e)
                    escape_dir = [math.cos(a), math.sin(a)]
                    esc_src = f"ray{bk}"
                    escape_t = 1.8
                elif repeated and escape_count >= 2:
                    # narożnik: manewr nie pomógł — zawróć całkiem (180°)
                    a = math.atan2(heading[1], heading[0]) + math.pi
                    escape_dir = [math.cos(a), math.sin(a)]
                    esc_src = "180"
                else:
                    sign = 1.0 if random.random() < 0.5 else -1.0
                    a = math.atan2(heading[1], heading[0]) + sign * math.radians(150)
                    escape_dir = [math.cos(a), math.sin(a)]
                    esc_src = "150"
                # Anty-pętla: ten sam kierunek zawodził świeżo (narożnik/zbocze)?
                # Obróć o ±60° zamiast wpychać się drugi raz w to samo.
                d0 = last_escape_dir[0] * escape_dir[0] + last_escape_dir[1] * escape_dir[1]
                if escape_count >= 2 and d0 > 0.72:
                    rot = math.radians(60) * (1 if escape_count % 2 else -1)
                    a2 = math.atan2(escape_dir[1], escape_dir[0]) + rot
                    escape_dir = [math.cos(a2), math.sin(a2)]
                    esc_src += "+60"
                last_escape_dir = list(escape_dir)
                edge_bias = [-heading[0] * 2.0, -heading[1] * 2.0]  # odpychajka od ściany
                if escape_t <= 0:
                    escape_t = 1.4
                blocked_t = stuck_t = 0.0
                vel_est[0] = vel_est[1] = 0.0
                no_block_until = now_e + 1.0  # krótki spokój po manewrze
                bot_state = "reset"           # po blokadzie: najpierw wyjście z kieszeni
                state_until = el + 2.0
                st_reason = "blokada"
                self.logf(f"blokada — ucieczka [{esc_src}] pod {math.degrees(math.atan2(escape_dir[1], escape_dir[0])):.0f}° (narożnik? {escape_count})")

            if escape_t > 0:
                # MANEWR UCIECZKI: nadpisuje steering — bez wygładzania w stronę ściany
                escape_t -= dt
                edge_bias[0] *= 0.99
                edge_bias[1] *= 0.99
                hx = escape_dir[0] + edge_bias[0] * 0.4
                hy = escape_dir[1] + edge_bias[1] * 0.4
                hm = math.hypot(hx, hy) or 1.0
                heading[0], heading[1] = hx / hm, hy / hm
                want = keys_from_vector(heading[0], heading[1]) or {"w"}
            elif used_mm:
                # --- wrogowie: zbierz pozycje (decyzje i ruch niżej — FSM) ---
                enemy_list: list[tuple[float, float, float, float]] = []  # (d, x, y, w)
                boss_list: list[tuple[float, float, float, float]] = []   # (d, x, y, w) — boss ma duży klaster
                for cl in clusters:
                    if classify_cluster(cl) == "enemy":
                        d = max(math.hypot(cl["x"], cl["y"]), 4.0)
                        w = min(cl["n"], 24) / 8.0
                        if cl["n"] >= 35:
                            boss_list.append((d, cl["x"], cl["y"], w))
                        else:
                            enemy_list.append((d, cl["x"], cl["y"], w))
                enemy_list.sort()
                boss_list.sort()
                # Celowane są tylko wrogowie <=300 px — dalej na minimapie to
                # zgiełk klasifikacji (600-790 px) albo przeciwnik poza sensownym
                # zasięgiem podejścia; ignoruje ich także kara w RESET
                enemy_list = [e for e in enemy_list if e[0] <= 300.0]
                boss_list = [e for e in boss_list if e[0] <= 300.0]
                nearest_enemy_d = enemy_list[0][0] if enemy_list else 999.0
                nearest_boss_d = boss_list[0][0] if boss_list else 999.0
                boss_active = nearest_boss_d <= 90.0
                enemy_pos = [(x, y) for _, x, y, _ in enemy_list]
                enemy_pos_list = enemy_list   # 4-krotki (d,x,y,w) — kara w RESET
                close_cnt = sum(1 for d, _, _, _ in enemy_list if d < 45)
                # CEL ŁAPANIA (cap = osłabiony Pokémon/elita do przyłączenia do
                # League): najbliższy klastr cap <50 px. Zerowe złapania w całej
                # historii bota (3/138) — połów dostaje teraz PRIORYTET w FSM.
                cap_c = None
                cap_c_d = 999.0
                wild_c = None
                wild_c_d = 999.0
                for cl in clusters:
                    k = classify_cluster(cl)
                    d_c = math.hypot(cl["x"], cl["y"])
                    if k == "cap" and cl["n"] < 50:
                        if d_c < cap_c_d:
                            cap_c, cap_c_d = cl, d_c
                    elif k == "wild" and cl["n"] < 60:
                        if d_c < wild_c_d:
                            wild_c, wild_c_d = cl, d_c
                # KRAWĘDZIE (raycast luminancji) — 16 sektorów; używane WYŁĄCZNIE
                # do wyboru kierunku (widowisko ważone + wall-slide), NIE do repulsji
                rays = mm.get("rays") or []
                nd = len(rays)
                mn_r = min(rays) if rays else 999
                # ===== FSM: ENGAGE / RESET / FARM / EXPLORE — jedno zachowanie naraz =====
                # Wzorce z botów do podobnych gier (bullet-heaven / survivors-like):
                #  1) ZERO sumowania sił: stan wybiera JEDEN punkt celu, pursuit czysty,
                #     wall-slide po stycznej gdy droga zablokowana (zamiast repulsji).
                #  2) ENGAGE: pierścień ~15 px wokół JEDNEGO wroga (auto-ataki 2-6 j.)
                #     + orbita; ANTI-HOVER: brak postępu 4 s → RESET. Stanie na dystansie
                #     40-90 px = zjadanie pocisków bez zadawania obrażeń (run: 80 s
                #     "walki" na 40-60 px, 0 zabójstw, 111→8 HP — główne źródło śmierci).
                #  3) RESET: zobowiązanie 2.5-3 s w najszerszy sektor (kara za wrogów),
                #     bez celów i bez walki — koniec flipowania co tick.
                #  4) FARM: cel aktywności (cap/heal/pickup/portal) jako punkt celu.
                panic = last_hp is not None and last_hp < 25
                defensive = last_hp is not None and last_hp < 55
                hp_now = last_hp if last_hp is not None else 111
                burst = hp_prev is not None and last_hp is not None and hp_prev - last_hp >= 20
                hp_prev = last_hp
                others_close = max(0, close_cnt - 1)
                # KIESZEŃ = PRAWDZIWA pułapka: ściany Z BLISKOBIEGĄCYMI wrogami.
                # Runy 22:48-22:53 (Viridian Forest): rays łapią FOLIARZ — 6 sektorów
                # <45 px i mn_r<30 zdarzało się CIĄGLE przy otoczenie=0-1 → reset(kieszeń)
                # co kilka sekund, 4× Shift/run przed krzakami, ENGAGE zablokowany przez
                # bramę mn_r>=30 (run #2: 0 zab w 1:29, hp 111→4). Drzewa ≠ wrogowie:
                # bez wrogów <=55 px to NIE kieszeń, tylko teren (wall-slide załatwia).
                pocket = bool(rays) and nd == 16 and nearest_enemy_d <= 55 \
                    and sum(1 for rr in rays if rr < 45) >= 6 and mn_r < 30
                # AGRESJA: walka to stan DOMYŚLNY gdy wróg w zasięgu — wejście od hp 40
                # (level-up leczy do pełna, więc wymiana ciosów się OPŁACA), bez limitu
                # "otoczenia" (cel = najbliższy wróg, orbita automatycznie unika).
                # Brama dystansu 90 px: sesja 22:09 wchodziła w ENGAGE z d=118 i
                # biegła 100 px przez pole gry w poziomie 1 — śmierć zanim wsadziła cios.
                # Bramy mn_r tu NIE MA: w lesie mn_r<30 prawie zawsze (foliarz) i
                # ENGAGE byłby zablokowany na stałe; ściany obsługuje wall-slide.
                # BOSS: zadaje obrażenia z daleka i ma duży zasięg melee — PRZY BOSCIE
                # UNIKAJ, nie walcz: ENGAGE tylko gdy boss daleko (>80px) i jesteś
                # bardzo zdrowy (hp>=85). Wszystko inne = retreat/heal.
                if boss_active:
                    engage_ok = bool(enemy_list) and hp_now >= 85 \
                        and nearest_boss_d > 80 and nearest_enemy_d <= 90
                else:
                    engage_ok = bool(enemy_list) and hp_now >= 40 \
                        and nearest_enemy_d <= 90
                st_reason = ""
                state_before = bot_state
                # --- przejścia stanów awaryjne (JEDYNIE burst/prawa kieszeń/krytyczne hp) ---
                if burst or pocket or hp_now < 20:
                    bot_state = "reset"
                    state_until = el + (1.8 if pocket else (1.2 if burst else 2.0))
                    st_reason = "burst" if burst else ("kieszen" if pocket else "hp krytyczne")
                elif cap_c is not None and cap_c_d <= 65 and hp_now >= 70 \
                        and bot_state != "reset" and not prompt:
                    # ŁAPANIE MA PRIORYTET nad koszeniem (poza awaryjnym resetem):
                    # złapany Pokémon dołącza do League NA STAŁE, a Support Alphas
                    # walczą w każdym kolejnym runie (u gracza 17% dmg / 1137 KOs).
                    # Zaangażowanie 3 s: dojście + pierścień + handler [E] Lanzar
                    # sam wyciska rzut, gdy prompt się pojawi.
                    prev_kind = obj[0] if obj else None
                    obj = ("cap", cap_c["x"], cap_c["y"])
                    if prev_kind != "cap" and el - last_act_log > 4:
                        last_act_log = el
                        self.logf(f"polow: oslabiona elita d={int(cap_c_d)} (hp={last_hp}, wróg d={int(nearest_enemy_d)})")
                    bot_state = "farm"
                    state_until = el + 3.0
                    st_reason = "polow"
                    # ŚLEPY RZUT: sesje 22:35/22:41 — bot stał na d=10-20 px przez
                    # dziesiątki sekund, a prompt [E] NIGDY nie wstał (prompt div
                    # może mieć inną treść niż CAPTURE_RX albo nie wstawać wcale).
                # ŚLEPY RZUT: działa niezależnie od stanu FSM — gdy cap jest
                # bezpośrednio obok (d<20, elita już osłabiona), bot zdrowy (hp>=40)
                # i brak wrogów w pobliżu, wciśnij E co 1s.
                # Gra nie pokazuje promptu w div .prompt (renderuje przez
                # ui.$q0hn8k()), więc wykrywamy rzut przez uiText (powyżej)
                # i tu robimy ślepy E jako fallback.
                if cap_c is not None and cap_c_d < 20 and hp_now >= 40 \
                        and nearest_enemy_d >= 35 \
                        and not st.get("cards") and not st.get("paused"):
                    if cap_close_t == 0.0:
                        cap_close_t = el
                    elif el - cap_close_t > 1.0:
                        cap_close_t = el
                        try:
                            self.page.keyboard.press("e")
                        except Exception:
                            pass
                        last_e_t = time.time()
                        self.logf(f"E: blind rzut przy cap d={int(cap_c_d)}")
                        if el - cap_dump_t > 90:
                            cap_dump_t = el
                            self.save_state("cap-no-prompt")
                else:
                    cap_close_t = 0.0
                # ŚLEPY RZUT dla dzikich Pokémon (wild): gdy jest bezpośrednio obok
                # (d<20), bot zdrowy (hp>=40) i brak wrogów w pobliżu, wciśnij E
                # co 1.5s — fallback gdy prompt nie wstaje w div .prompt.
                if wild_c is not None and wild_c_d < 20 and hp_now >= 40 \
                        and nearest_enemy_d >= 35 \
                        and not st.get("cards") and not st.get("paused"):
                    if wild_close_t == 0.0:
                        wild_close_t = el
                    elif el - wild_close_t > 1.5:
                        wild_close_t = el
                        try:
                            self.page.keyboard.press("e")
                        except Exception:
                            pass
                        last_e_t = time.time()
                        self.logf(f"E: blind rzut przy wild d={int(wild_c_d)}")
                        if el - wild_dump_t > 90:
                            wild_dump_t = el
                            self.save_state("wild-no-prompt")
                else:
                    wild_close_t = 0.0
                if el >= state_until:
                    if bot_state == "reset":
                        # po RESECIE: WRACAJ DO WALKI tylko gdy wystarczająco zdrowy
                        # (po panic hp<45 nie wracaj od razu do melee — giniesz)
                        if engage_ok and hp_now >= 55:
                            bot_state, st_reason = "engage", ""
                        elif obj and obj[0] == "heal" and hp_now < 70:
                            bot_state, st_reason = "farm", ""
                        else:
                            bot_state, st_reason = "explore", ""
                        state_until = el + 1.2
                    elif bot_state == "engage":
                        if boss_active:
                            if hp_now < 45:
                                bot_state, state_until, st_reason = "reset", el + 2.0, "boss hp<45"
                            elif hp_now < 75:
                                # BOSS PANIC: boss zadaje duże obrażenia — wycofaj się wcześniej
                                bot_state, state_until, st_reason = "reset", el + 1.2, "boss panic"
                        elif hp_now < 22:
                            bot_state, state_until, st_reason = "reset", el + 2.0, "hp<22"
                        elif hp_now < 45:
                            # PANIC RETREAT: nie czekaj do hp<22 — przy <45 już
                            # wycofaj się, żeby nie face-tankać pocisków do zera
                            bot_state, state_until, st_reason = "reset", el + 1.2, "panic"
                        elif not enemy_list:
                            bot_state, state_until, st_reason = "explore", el + 1.0, "brak wroga"
                        else:
                            # trzymaj ENGAGE też przy murze — wall-slide sam wykręca
                            # tor; obsługa "mn_r<35" tutaj wywoływała flicker
                            # engage↔explore przy KAŻDEJ ścianie (log 22:09)
                            state_until = el + 1.0          # podtrzymanie bez loga
                    elif bot_state == "farm":
                        # FARM = przerwa między walkami (jagody) lub spokojne
                        # zbieranie. WRÓG BLISKO przy zdrowym bo(cie) = WRACAJ
                        # DO WALKI — run #1 (0 zab., 60 s po pickupach przy
                        # wrogach 25-67 px) nigdy więcej.
                        if obj is None:
                            bot_state = "engage" if engage_ok else "explore"
                            state_until = el + 1.0
                            st_reason = ""
                        elif obj[0] == "heal":
                            if hp_now >= 95:
                                bot_state = "engage" if engage_ok else "explore"
                                state_until = el + 1.0
                                st_reason = "wyleczony"
                            else:
                                state_until = el + 1.0
                        elif obj[0] == "wild":
                            # dzikie Pokémon na mapie (encounter): podejdź i czekaj
                            # na prompt [E]; nie walcz, nie wchodź w melee
                            if nearest_enemy_d < 45 and hp_now < 60:
                                bot_state, state_until, st_reason = "reset", el + 1.2, "wild ryzyko"
                            else:
                                state_until = el + 1.0        # trzymaj dystans + [E]
                        elif obj[0] == "cap":
                            # polowanie znosi WIĘCEJ ryzyka niż zbieranie (nagroda
                            # trwała): przerywaj dopiero przy realnym zagrożeniu
                            if hp_now < 45:
                                bot_state, state_until, st_reason = "reset", el + 1.2, "ranne polowanie"
                            elif nearest_enemy_d < 22 and hp_now >= 50:
                                bot_state, state_until, st_reason = "engage", el + 1.0, "obrona"
                            elif nearest_enemy_d < 22:
                                bot_state, state_until, st_reason = "reset", el + 1.0, "obrona"
                            else:
                                state_until = el + 1.0        # trzymaj pierścień + [E]
                        elif nearest_enemy_d < 50 or hp_now < 70:
                            if engage_ok:
                                bot_state, state_until, st_reason = "engage", el + 1.0, "wróg w pogoni"
                            else:
                                bot_state, state_until, st_reason = "reset", el + 1.2, "wycofanie"
                        else:
                            state_until = el + 1.0
                    else:  # explore
                        # cap (łapanie) wchodzi do FARM przy umiarkowanym spokoju —
                        # requirement "wróg >=70 px" floatował połów: farm=1 w całej
                        # sesji 22:09 mimo cap-klasterów w promieniu 30-70 px
                        calm_obj = obj is not None and (
                            obj[0] == "heal"
                            or (obj[0] == "cap" and hp_now >= 70 and nearest_enemy_d >= 35)
                            or (obj[0] == "wild" and hp_now >= 70 and nearest_enemy_d >= 40)
                            or (hp_now >= 80 and nearest_enemy_d >= 70))
                        if calm_obj:
                            bot_state, state_until, st_reason = "farm", el + 1.0, ""
                        elif engage_ok and hp_now >= 55:
                            bot_state, state_until, st_reason = "engage", el + 1.0, ""
                        elif enemy_list and hp_now < 40:
                            bot_state, state_until, st_reason = "reset", el + 1.2, "ranny"
                        else:
                            state_until = el + 1.0
                if bot_state == "engage" and state_before != "engage":
                    engage_min_d, engage_last_prog = nearest_enemy_d, el
                    engage_tgt_d_last = nearest_enemy_d
                # --- ANTI-HOVER w ENGAGE: brak postępu 4 s → RESET ---
                # ALE: gdy bot JUŻ dotarł do pierścienia zabijania (min_d <= 18 px
                # = 2-6 j. świata = zasięg auto-ataków), stanie w miejscu to SUKCES
                # (krąży i zabija), nie hover. Kara tylko gdy utknął DALEKO od ringu.
                if bot_state == "engage" and enemy_list:
                    # postęp = dystans do celu (skupisko może być dalej niż najbliższy wróg)
                    prog_now = min(nearest_enemy_d, engage_tgt_d_last)
                    if prog_now < engage_min_d - 3.0:
                        engage_min_d = prog_now
                        engage_last_prog = el
                    elif el - engage_last_prog > 4.0 and engage_min_d > 18.0:
                        bot_state, state_until, st_reason = "reset", el + 2.5, "bezpostep"
                        engage_min_d, engage_last_prog = 999.0, el
                # --- log stanów (jak wcześniej: throttle + licznik przełączeń) ---
                fight = bot_state == "engage"
                # akcje jednorazowe (uruchamiane po sync_keys poniżej):
                #  R = Ultimate — u gracza 38% całego dmg, ale TYLKO z bliska:
                #  sesja 22:09 paliła R z dystansu 40-60 px (6-9×/run na darmo).
                #  Warunek: wróg w zasięgu ataków (<=28 px) — wtedy AoE ma cel.
                #  Shift = ślizg-dash — przebice z kieszeni ZAMIAST biernego resetu
                #  BOSS: R na większym dystansie (45px) — boss ma większy zasięg obrażeń,
                #  więc ultymalne AoE z bezpieczniejszej odległości.
                r_fire = fight and nearest_enemy_d <= (45.0 if boss_active else 35.0) \
                    and hp_now >= 30 and el >= r_cd
                dash_fire = bot_state == "reset" and pocket and el >= dash_cd
                if fight != fight_prev:
                    mk = re.search(r"SURVIVAL\s+(?:\S+\s+)?(\d+:\d+(?:\.\d+)?)\s+(\d+)", st["uiText"])
                    nk = int(mk.group(2)) if mk else -1
                    if fight:
                        fight_kills0 = max(nk, 0)
                    elif fight_kills0 >= 0 and nk >= 0 and nk > fight_kills0 and el - last_act_log > 3:
                        last_act_log = el
                        self.logf(f"walka dała +{nk - fight_kills0} zab.")
                    fight_prev = fight
                if bot_state != prev_state_logged:
                    mode_skipped += 1
                    prev_state_logged = bot_state
                    if bot_state == "engage" or el - last_mode_log > 2.5:
                        if mode_skipped > 1 and bot_state != "engage":
                            self.logf(f"(+{mode_skipped} przełączeń stanu od ostatniego loga)")
                        mode_skipped = 0
                        last_mode_log = el
                        last_act_log = el
                        self.logf(f"stan: {bot_state}" + (f" ({st_reason})" if st_reason else "")
                                  + f" (d={int(nearest_enemy_d)}, otoczenie={close_cnt}, hp={last_hp})")
                # --- PUNKT CELU wg stanu (JEDEN cel, bez sumowania sił) ---
                gx = gy = 0.0
                if bot_state == "engage" and enemy_list:
                    # cel = wróg w NAJGĘSTSZYM otoczeniu (AoE zabija wielu naraz,
                    # jak u gracza: 3455 KOS w 9 min to AoE topiące ekrany);
                    # ALE tylko w promieniu 55 px — inaczej bot "goni chmarę"
                    # na drugim końcu ekrana i ODSUWA się od najbliższego wroga
                    # (log: engage d=55 → d=70). Poza promieniem → najbliższy.
                    def _tgt(e5):
                        _d5, ex5, ey5, _w5 = e5
                        nb = sum(1 for _d2, ox, oy, _w2 in enemy_list
                                 if math.hypot(ox - ex5, oy - ey5) < 30.0)
                        return 10.0 * nb - _d5 * 0.05
                    cand5 = [e for e in enemy_list if e[0] <= 55.0] or enemy_list[:1]
                    fd, fx, fy, _fw = max(cand5, key=_tgt)
                    engage_tgt_d_last = fd
                    fux, fuy = fx / fd, fy / fd
                    pvx, pvy = -fuy * orbit_sign, fux * orbit_sign
                    # MELEE ESCAPE: wróg <15 px = w zasięgu melee — natychmiastowy
                    # odskok 40 px w tył. Ma priorytet nad orbitą/strafingiem;
                    # bez tego wróg wbija się w bota (rays 10-16 px w lesie) i
                    # face-tankuje do zera mimo dystansu 27 px (logi: ginie z
                    # hp 126 bo wróg na 10-16 px).
                    if fd < 15.0:
                        # MELEE ESCAPE: wróg <15 px — odskok w tył. Przy boscie
                        # większy odskok (50px), bo boss ma większy zasięg obrażeń.
                        escape_dist = 50.0 if boss_active else 40.0
                        gx, gy = -fux * escape_dist, -fuy * escape_dist
                        if el - last_act_log > 1:
                            last_act_log = el
                            self.logf(f"melee-escape: wróg d={int(fd)} — odskok {int(escape_dist)}px")
                    # ANTYPociskowy straf: co ~1.3 s odskok radialny od wroga
                    # (pociski lecą prosto — boczne ruszenie je mine; face-tank
                    # na orbicie zadaje obrażenia bez potrzeby — główny powód
                    # szybkich śmierci hp 100→0 w <40 s)
                    elif el - strafe_t > 1.3:
                        gx, gy = -fux * 13.0, -fuy * 13.0
                        strafe_t = el
                        if el - last_act_log > 2:
                            last_act_log = el
                            self.logf(f"straf: odskok od wroga d={int(fd)}")
                    elif nd == 16:
                        ka_e = int(round(math.atan2(fy, fx) / (2 * math.pi / 16))) % 16
                        if rays[ka_e] < 30 and el - last_orbit_flip > 1.0:
                            orbit_sign = -orbit_sign
                            last_orbit_flip = el
                            pvx, pvy = -pvx, -pvy
                    if boss_active:
                        # BOSS: dystans 50px — nie zbliżaj się, boss zadaje obrażenia z daleka
                        if fd > 55:
                            gx = fux * (fd - 50.0) * 0.85 + pvx * 7.0
                            gy = fuy * (fd - 50.0) * 0.85 + pvy * 7.0
                        elif fd > 45:
                            gx = fux * (fd - 50.0) * 0.8 + pvx * 9.0
                            gy = fuy * (fd - 50.0) * 0.8 + pvy * 9.0
                        elif fd < 42:
                            # uciekaj od bossa (melee-escape większy od normalnego)
                            gx = -fux * 12.0 + pvx * 9.0
                            gy = -fuy * 12.0 + pvy * 9.0
                        else:
                            # na bezpiecznym dystansie 45-50px: czysta orbita
                            gx, gy = pvx * 10.0, pvy * 10.0
                            gx, gy = pvx * 10.0, pvy * 10.0
                    elif fd > 32:
                        # dojście na pierścień zabijania ~27 px: auto-ataki trafiają
                        # z ~20 px, wróg melee ma zasięg ~15-20 px — 27 px to bezpieczny
                        # dystans (bot zadaje dmg, nie face-tankuje). Łuk z odchyleniem bocznym.
                        gx = fux * (fd - 27.0) * 0.85 + pvx * 7.0
                        gy = fuy * (fd - 27.0) * 0.85 + pvy * 7.0
                    elif fd > 24:
                        # domykanie orbity: styczna + słaby radialny składowy
                        gx = fux * (fd - 27.0) * 0.8 + pvx * 9.0
                        gy = fuy * (fd - 27.0) * 0.8 + pvy * 9.0
                    elif fd < 20:
                        # za głęboko (wewnątrz zasięgu melee wróga): odepchnięcie po stycznej
                        gx = -fux * 9.0 + pvx * 9.0
                        gy = -fuy * 9.0 + pvy * 9.0
                    else:
                        # na pierścieniu 25-30 px: czysta orbita (DPS z auto-ataków + unik)
                        gx, gy = pvx * 10.0, pvy * 10.0
                elif bot_state == "reset" and nd == 16:
                    wsc = [rays[k] + 0.6 * rays[(k - 1) % nd] + 0.6 * rays[(k + 1) % nd]
                           + 0.25 * rays[(k - 2) % nd] + 0.25 * rays[(k + 2) % nd]
                           for k in range(nd)]
                    for k in range(nd):
                        a_k = k * (2 * math.pi / nd)
                        for ed, ex, ey, _ew in enemy_pos_list:
                            ae = math.atan2(ey, ex)
                            da = math.atan2(math.sin(ae - a_k), math.cos(ae - a_k))
                            if abs(da) < 1.3 * (2 * math.pi / nd):
                                wsc[k] -= max(0.0, (60.0 - ed)) * 1.2
                    if hp_now < 65:
                        # jagody po drodze: bonus w sektorze z heal — przy ranach
                        # uciekaj KU bezpieczeństwu, nie tylko OD wrogów
                        for cl in clusters:
                            if classify_cluster(cl) == "heal":
                                dh = math.hypot(cl["x"], cl["y"])
                                if dh < 100:
                                    ka_h = int(round(math.atan2(cl["y"], cl["x"]) / (2 * math.pi / nd))) % nd
                                    wsc[ka_h] += 2.5 if hp_now < 40 else 1.2
                    bp = max(range(nd), key=lambda i: wsc[i])
                    a_r = bp * (2 * math.pi / nd)
                    gx, gy = math.cos(a_r), math.sin(a_r)
                elif bot_state == "farm" and obj:
                    d_o = max(math.hypot(obj[1], obj[2]), 4.0)
                    if obj[0] == "cap" and d_o < 45:
                        # pierścień wokół celu łapania — osłabianie auto-atakami,
                        # prompt [E] Lanzar obsłuży rzut
                        oux, ouy = obj[1] / d_o, obj[2] / d_o
                        if d_o > 24:
                            gx, gy = obj[1] * 0.85, obj[2] * 0.85
                        else:
                            gx, gy = -ouy * orbit_sign * 10.0, oux * orbit_sign * 10.0
                    elif obj[0] == "wild" and d_o < 45:
                        # dzikie Pokémon na mapie — podejdź do dystansu ~15px i
                        # czekaj na prompt [E]; nie wchodź w melee
                        oux, ouy = obj[1] / d_o, obj[2] / d_o
                        if d_o > 16:
                            gx, gy = obj[1] * 0.85, obj[2] * 0.85
                        else:
                            gx, gy = -ouy * orbit_sign * 8.0, oux * orbit_sign * 8.0
                    else:
                        gx, gy = obj[1], obj[2]
                else:
                    # EXPLORE: kompas (cel łapania) gdy spokój, inaczej najszerszy sektor
                    if ang is not None and not prompt and nearest_enemy_d >= 50 and hp_now >= 50:
                        gx, gy = math.sin(ang), -math.cos(ang)  # założenie: rotate(0) = góra
                    elif nd == 16:
                        wsc = [rays[k] + 0.6 * rays[(k - 1) % nd] + 0.6 * rays[(k + 1) % nd]
                               for k in range(nd)]
                        bp = max(range(nd), key=lambda i: wsc[i])
                        a_x = bp * (2 * math.pi / nd)
                        gx, gy = math.cos(a_x), math.sin(a_x)
                    else:
                        gx, gy = heading[0], heading[1]
                # --- PURSUIT + WALL-SLIDE: zejście po stycznej przy murze ---
                dm_ = math.hypot(gx, gy)
                if dm_ > 1e-3:
                    dx_, dy_ = gx / dm_, gy / dm_
                    if nd == 16:
                        b_ = int(round(math.atan2(dy_, dx_) / (2 * math.pi / 16))) % 16
                        if min(rays[(b_ - 1) % 16], rays[b_], rays[(b_ + 1) % 16]) < 35:
                            left = min(rays[(b_ - 2) % 16], rays[(b_ - 1) % 16], rays[b_])
                            right = min(rays[b_], rays[(b_ + 1) % 16], rays[(b_ + 2) % 16])
                            rot_ = 2 if right >= left else -2
                            a_p = math.atan2(dy_, dx_) + rot_ * (2 * math.pi / 16)
                            dx_, dy_ = math.cos(a_p), math.sin(a_p)
                            b2_ = int(round(math.atan2(dy_, dx_) / (2 * math.pi / 16))) % 16
                            if min(rays[(b2_ - 1) % 16], rays[b2_], rays[(b2_ + 1) % 16]) < 35:
                                a_p += rot_ * (2 * math.pi / 16)
                                dx_, dy_ = math.cos(a_p), math.sin(a_p)
                    sm = 0.5
                    nhx = sm * heading[0] + (1 - sm) * dx_
                    nhy = sm * heading[1] + (1 - sm) * dy_
                    nm_ = math.hypot(nhx, nhy) or 1.0
                    heading[0], heading[1] = nhx / nm_, nhy / nm_
                want = keys_from_vector(heading[0], heading[1]) or {"w"}
                # CEL AKTYWNOŚCI (co 2.5 s): jagody (hp<75) > łapanie > portal > pickup.
                # POŁÓW NIE JEST KOSMETYKĄ: złapane Pokémony wchodzą do League
                # (2/138 → 3/138: Charmander), a Support Alphas z WCZEŚNIEJSZYCH
                # złapań walczą w każdym kolejnym runie — u gracza zrobiły 17% dmg
                # (Skarmory: 1137 KOs, 717k dmg, Bond 5). Każde złapanie = trwałe
                # wzmocnienie przyszłych runów.
                # Progi zagrożenia per-rodzaj: pickupy zbierane nawet przy wrogu 40+ px
                # (auto-zbieranie przy przejściu), portal/łapanie wymagają więcej spokoju.
                # Cel odrzucany gdy LEŻY przy wrogu (<55 px) lub przy krawędzi mapy.
                if time.time() - target_t > 2.5:
                    target_t = time.time()
                    best, bs = None, 0.0
                    for cl in clusters:
                        kind = classify_cluster(cl)
                        d = max(math.hypot(cl["x"], cl["y"]), 4.0)
                        # TODO: "rock smach" — niszczenie skał/przeszkód na mapie.
                        # W game.js skały (boulder/rocks) to dekoracje, nie ma wyraźnej
                        # kategorii na minimapie. Gdy gra doda klastr np. "rock" /
                        # "breakable", dodać tu: elif kind == "rock": sc = X.
                        if panic:
                            if kind != "heal" or d > 120:
                                continue
                            sc = 4.0
                        elif kind == "heal" and d < 110 and hp_now < 75:
                            sc = 5.0 if boss_active else 3.5   # przy boscie leczenie wyższym priorytetem (cap=6.0)
                        elif kind == "wild" and cl["n"] < 60 and d < 140 and nearest_enemy_d >= 40:
                            sc = 4.0   # dzikie Pokémon na mapie (encounter) — spotkanie i złapanie
                        elif kind == "cap" and cl["n"] < 50 and d < 130 and nearest_enemy_d >= 35:
                            sc = 6.0   # cap = osłabiona elita DO ZŁAPANIA (League) LUB altar (kapliczka) —
                                       # oba mają ten sam kolor na minimapie; altar daje buff obrażeń (game.js: buffs.has('altar'))
                        elif kind == "portal" and d < 80 and nearest_enemy_d >= 70:
                            sc = 3.0
                        elif kind == "pickup" and d < 100 and nearest_enemy_d >= 50:
                            sc = 3.0   # pickup包括 chest (skrzynie z Poké Ballami/Great Ballami) —
                                       # kluczowe dla capture i League (te same kolory co pickup)
                        else:
                            continue
                        # nie biegaj po coś, co leży przy wrogu (cap: tylko tuż-tuż,
                        # 35 px — elity często leżą przy pozostałościach walki)
                        veto_d = 35 if kind == "cap" else 55
                        if kind != "heal":
                            if any(math.hypot(cl["x"] - ex, cl["y"] - ey) < veto_d for ex, ey in enemy_pos):
                                continue
                        # nie goniń czegoś, co leży przy KRAWĘDZI (promień w tym
                        # kierunku krótki = tuż za celem pustka/mur) — wyjątek: heal
                        if rays and len(rays) == 16:
                            ka = int(round(math.atan2(cl["y"], cl["x"]) / (2 * math.pi / 16))) % 16
                            lim = 90 if kind == "heal" else 110
                            # 3-bin: korytarz obok celu też się liczy (wego mouth)
                            if min(rays[(ka - 1) % 16], rays[ka], rays[(ka + 1) % 16]) < lim:
                                continue
                        sc /= (1.0 + d / 45.0)
                        if sc > bs:
                            bs, best = sc, (kind, cl["x"], cl["y"])
                    if best != obj:
                        obj = best
                        if obj and el - last_act_log > 5:
                            last_act_log = el
                            self.logf(f"cel: {obj[0]} d={int(math.hypot(obj[1], obj[2]))} (wróg d={int(nearest_enemy_d)}, hp={last_hp})"
                                      + (" [panic]" if panic else " [def]" if defensive else ""))

            else:
                # fallback (brak minimapy): kiting łukiem
                if el % 8.0 < dt and random.random() < 0.5:
                    turn_dir *= -1
                arc += dt * turn_dir * 1.4
                want = {"w"}
                want.add("a" if math.sin(arc) > 0 else "d")
                heading[0] = 1.0 if "d" in want else -1.0
                heading[1] = -0.4
                nm = math.hypot(heading[0], heading[1]) or 1.0
                heading[0] /= nm
                heading[1] /= nm

            # Anti-stuck (zapasowy, gdy brak punktów odniesienia na minimapie):
            # te same klawisze >6 s → wymuś manewr ucieczki
            wf = frozenset(want)
            if wf and wf == last_want:
                stuck_t += dt
            else:
                stuck_t = 0.0
                last_want = wf
            sync_keys(want)
            # jednorazowe akcje bojowe po zsynchronizowaniu kierunku (uważać:
            # karty level-upa przechwytują klawisze — stąd tylko tutaj, nie w FSM)
            if r_fire:
                r_cd = el + 12.0
                try:
                    self.page.keyboard.press("r")
                    self.logf(f"R: ultimate (wrogów blisko={close_cnt}, hp={last_hp})")
                except Exception:
                    pass
            if dash_fire:
                dash_cd = el + 5.0
                try:
                    self.page.keyboard.press("Shift")
                    self.logf(f"Shift: ślizg z kieszeni (mn_r={mn_r:.0f})")
                except Exception:
                    pass

            if el - last_ui_log >= 20:
                last_ui_log = el
                ang_s = "?" if ang is None else f"{math.degrees(ang):.0f}deg"
                kd_s = str(st.get("compass_d") or "?")
                m_k = re.search(r"SURVIVAL\s+(?:\S+\s+)?(\d+:\d+(?:\.\d+)?)\s+(\d+)", st["uiText"])
                if m_k:
                    nk = int(m_k.group(2))
                    delta = "" if kills < 0 else f" (+{nk - kills})" if nk > kills else " (0)"
                    kills = nk
                else:
                    delta = ""
                    kills = -1
                self.logf(f"t+{int(el)}s hp={last_hp} zab={kills}{delta} timer={tm} stan={bot_state}({st_reason or '-'}) kier=({heading[0]:.2f},{heading[1]:.2f}) prompt={str(st.get('prompt'))[:40]!r} kompas={ang_s} d={kd_s} ui={st['uiText'][:110]!r}")
                ui_txt = st["uiText"]
                if re.search(r"shiny|variocolor|apareci|reu\\.n|encounter", ui_txt, re.I):
                    self.logf(f"!? spotkanie w uiText: {ui_txt[:160]!r}")
            if used_mm and el - last_mm_log >= 30:
                last_mm_log = el
                kinds = [(classify_cluster(c), c["n"], int(math.hypot(c["x"], c["y"])), c["col"]) for c in clusters[:6]]
                ry = mm.get("rays") or []
                ry_s = "/".join(str(int(r)) for r in ry) if ry else "?"
                self.logf(f"minimap: {kinds} kompas={st.get('compass')!r} d={st.get('compass_d')!r} rays={ry_s}")

            sleep = dt - (time.time() - loop_t0)
            if sleep > 0:
                time.sleep(sleep)

        for k in list(held):
            self.page.keyboard.up(k)
        self.logf(f"run end: zab={kills}, capture_count={capture_count}, elapsed={int(el)}s, status={status}")
        return status

    # ---------------------------------------------------------------- main
    def main(self) -> int:
        LOGS.mkdir(exist_ok=True)
        self.open_game()

        st = self.snap()
        if self.is_auth_screen(st):
            if not self.wait_for_human_auth():
                self.save_state("auth-timeout")
                return 1
        else:
            self.logf("sesja aktywna — pomijam logowanie")

        for i in range(1, self.runs_n + 1):
            self.logf(f"=== run #{i}/{self.runs_n} ===")
            try:
                if not self.run_menu_phase():
                    self.logf("nie udało się wejść w run — zapisuję dump i kończę")
                    self.save_state("cannot-start")
                    break
                status = self.play_run()
                self.logf(f"run #{i} status: {status}")
                if status == "rejected":
                    break
            except Exception as e:
                # Przeglądarka zamknięta / strona padła — restart i kolejny run.
                self.logf(f"sesja przerwana ({type(e).__name__}: {str(e)[:90]}) — restart przeglądarki")
                self.save_state("session-crash")
                try:
                    self.reopen_browser()
                except Exception as e2:
                    self.logf(f"restart FAIL: {e2} — kończę sesję")
                    break
            # kolejna iteracja: run_menu_phase sam kliknie wyniki/menu do nowego runu
            # (a jeśli gra pokaże modal 'wznów grę', menu_step kliknie Resume)

        self.logf("koniec sesji — okno przeglądarki zostaje otwarte 60 s")
        try:
            self.page.wait_for_timeout(60000)
        except Exception:
            pass  # gracz może zamknąć okno przed upływem grace
        (LOGS / f"bot-{time.strftime('%Y%m%d-%H%M%S')}.log").write_text("\n".join(self.log), encoding="utf-8")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--max-minutes", type=float, default=20.0)
    ap.add_argument("--speed", type=float, default=10.0, help="Hz pętli decyzyjnej")
    ap.add_argument("--mode", choices=["survival", "endless"], default="survival")
    args = ap.parse_args()
    bot = Bot(runs=args.runs, max_minutes=args.max_minutes, hz=args.speed, mode=args.mode)
    try:
        return bot.main()
    finally:
        try:
            bot.ctx.close()
            bot.pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
