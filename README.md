# PokéBonk Bot

Python/Playwright bot dla [PokéBonk](https://pokebonk.com) grający na koncie użytkownika online w trybie Survival (Endless).

## Funkcje

- **FSM (Finite State Machine)** — jeden cel naraz: ENGAGE (domyślny gdy wróg ≤90 px i hp≥40), RESET (tylko burst/prawdziwa kieszeń/hp<20), FARM, EXPLORE
- **Agresywne zabijanie** — bot nie ucieka, walczy agresywnie, orbituje wokół wrogów na bezpiecznym dystansie (~27 px)
- **Łapanie Pokémonów do League** — ślepy E przy osłabionych elitach (cap), obsługa Friend Ball / Amigo Ball, prompt rzutu wykrywany w uiText
- **Jajka i skrzynie** — podnoszenie jajek (hatch after 200 KOs), otwieranie skrzyń z Poké Ballami/Great Ballami
- **Altary (kapliczki)** — zbieranie buffów obrażeń z altarów na mapie
- **Karty level-up** — wybór klawiszami [1]/[2]/[3] lub myszką, obsługa modala "Got it"
- **Powiadomienia** — klikanie przycisków "joins your League", "caught" itp.
- **Unikanie bossa** — dystans 50px, panic retreat przy hp<75, R na dystansie 45px
- **Melee-escape + strafing** — odskok 40-50px gdy wróg <15px, unikanie pocisków co 1.3s
- **Sklep-ucieczka** — unikanie pułapki "Talk to Meowth (shop)"

## Wymagania

- Python 3.12+
- Playwright (`pip install playwright` + `playwright install chromium`)
- Profil Chrome z zalogowanym kontem (Turnstile — logowanie raz ręcznie)

## Uruchomienie

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" bot.py --runs 3 --max-minutes 15
```

Workdir: `C:\Users\aleks\Documents\Projekt domyślny\pokebonk-bot`

## Struktura projektu

| Plik | Przeznaczenie |
|------|-------------|
| `bot.py` | Główny bot — FSM, łapanie, unikanie, statystyki |
| `analyze_log.py` | Statystyki per-run + benchmark gracza |
| `.gitignore` | Ignoruje chrome-profile, logs, shots, out, *.pyc |

## Benchmark gracza

- Czas: 8:47
- KOS: 1.85k (~210/min)
- Dmg: 747.5k
- Gatunki: 20 species (2 nowe: Squirtle, Caterpie)

## Ważne

- **Nie usuwaj folderu `chrome-profile/`** — zawiera sesję logowania (Turnstile)
- **Nie uruchamiaj bota bez wyraźnej prośby**
- Wyniki porównuj tylko vs benchmark gracza, nie vs inne boty