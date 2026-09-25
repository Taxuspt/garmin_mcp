# HM Coach – adaptiver Trainingsplan mit Garmin

Ziel: **Berliner Halbmarathon, 04.04.2027, unter 1:25** (4:02 /km).
Segeln hat Priorität – die Termine aus der *Team HL Planungstabelle 26/27* sind im Plan geblockt.

## Was die App macht

| Baustein | Was passiert |
|---|---|
| **Plan** (`src/garmin_mcp/coach/plan.py`) | 27 Wochen ab 28.09.2026: Übergang → Grundlage 1/2 → Aufbau → HM-spezifisch → Taper. 4 Lauftage (Di Qualität, Do Qualität, Sa locker, So lang), Mo frei, Rad/Rudern als Grundlage, 2× Kraft. Umfang 27 → 62 km/Woche, 9–12,5 h. Jede 4. Woche Entlastung. |
| **Segeln** (`coach/athlete.json` → `sailing`) | Segeltage werden freigeräumt. Regattatage: nur optional 20' Aktivierung. Trainingstage am Wasser: max. 2 kurze Morgenläufe/Woche. Schlüsseleinheiten wandern auf freie Tage derselben Woche (mit Ruhetag dazwischen), sonst entfallen sie. Nach ≥ 5 Segeltagen folgt eine Wiedereinstiegswoche (−15 %). |
| **Tagesanpassung** | Morgens liest der Coach Garmin Training Readiness (sonst HRV-Status + Schlaf) und die Frische (TSB). 🟡 Schlüsseleinheit ~¼ kürzer, 🔴 nur locker, Schlüsseleinheit wird verschoben oder entfällt. Segeln wird nie angetastet. |
| **Wochenanpassung** | Montags: Umsetzung der Schlüsseleinheiten, Laufumfang, Ø-Readiness und Belastungsquote (ATL/CTL) → Umfang der nächsten Wochen 75–110 %. Segelwochen zählen nicht als „verpasst“. |
| **Tempo** | Trainingstempi kommen aus dem VDOT. Die Garmin-Laktatschwelle hebt den VDOT schrittweise an (Prognose im Dashboard). |
| **Bewertung** | Jede neue Einheit wird mit der geplanten verglichen (Umfang, HF-Zonen, Training Effect, Tempo) → Score 0–100 + Tipps. |
| **Push (ntfy)** | Nach jeder Einheit die Bewertung · morgens um 6 Uhr der Tagesplan mit Readiness-Ampel · sonntags 19 Uhr die Wochenbilanz. |
| **Dashboard** (`dashboard/`) | PWA für den Homescreen: Heute, Woche, Plan, Analyse (Fitness/Ermüdung, Frische, HRV, Schlaf), Einheiten. Daten sind mit deiner Passphrase verschlüsselt (AES-GCM). |

Alles läuft alle 30 Minuten (06–23 Uhr) in GitHub Actions (`.github/workflows/coach.yml`).

## Einrichtung (einmalig, geht komplett am iPad/Handy)

1. **ntfy-App** installieren → „+“ → langes, zufälliges Thema abonnieren (z. B. `karl-coach-7f3k9q2m`).
2. **GitHub-Secrets** anlegen (*Settings → Secrets and variables → Actions*):
   | Secret | Inhalt |
   |---|---|
   | `GARMIN_EMAIL` | deine Garmin-Connect-E-Mail |
   | `GARMIN_PASSWORD` | dein Garmin-Connect-Passwort |
   | `COACH_PASSPHRASE` | lange Passphrase – damit entsperrst du das Dashboard |
   | `NTFY_TOPIC` | dein ntfy-Thema |
3. **Pages** aktivieren: *Settings → Pages → Source: GitHub Actions* (privates Repo braucht einen bezahlten Plan → sonst Repo öffentlich machen; die Daten sind verschlüsselt).
4. Branch nach `main` mergen (geplante Workflows laufen nur vom Standard-Branch).
5. *Actions → Garmin Login → Run workflow*. Fragt Garmin nach einem Code, kommt eine ntfy-Push „Garmin-Code benötigt“ → auf **Code senden** tippen → nur die 6 Ziffern abschicken.
6. *Actions → Garmin Coach → Run workflow*.
7. `https://karllander-cell.github.io/garmin_mcp/` in Safari öffnen → Passphrase → *Teilen → Zum Home-Bildschirm*.

Die Anmeldung wird bei jedem Lauf erneuert und verschlüsselt im Branch `coach-data` gespeichert. Läuft sie ab, versucht der Coach es erst selbst mit E-Mail/Passwort; braucht Garmin einen Code, kommt eine Push → Schritt 5 wiederholen.

*Alternative am Computer:* `uv run garmin-mcp-auth` und `uv run garmin-coach export-tokens` → Ausgabe als Secret `GARMIN_TOKENS` (dann sind E-Mail/Passwort als Secrets nicht nötig).

## Anpassen

- **Segeltermine**: `coach/athlete.json` → `sailing` (`start`, `end`, optional `regatta_from`/`regatta_to`, `tentative`). Termine aus der Tabelle, die noch ein „?“ tragen, sind als `tentative` markiert und im Dashboard als „offen“ gekennzeichnet. Vilamoura-Blöcke sind mit ca. 10 Tagen angesetzt; Regattatage innerhalb der Blöcke sind geschätzt – bitte korrigieren, sobald die Ausschreibungen da sind.
- **Umfang / Ziel**: `volume` und `goal` in derselben Datei.
- **Demo ansehen**: `uv run garmin-coach demo` und dann `dashboard/index.html?demo` über einen lokalen Server öffnen (`python -m http.server -d dashboard`).
