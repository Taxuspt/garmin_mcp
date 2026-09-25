---
name: ask-me
description: Interview the user before building something. Use when the user says "ask me", "frag mich", or asks Claude to ask everything it needs before starting a bigger feature, app or plan. Collects requirements in short rounds of multiple-choice questions, then summarizes the decisions before implementation.
---

# Ask me – Anforderungs-Interview

Ziel: Vor der Umsetzung alle Entscheidungen einsammeln, die das Ergebnis wirklich verändern – ohne den User mit Fragen zu überfluten.

## Ablauf

1. **Erst recherchieren, dann fragen.** Lies Repo, verbundene Datenquellen (Garmin, Strava, Drive, Kalender) und vorhandene Notizen. Frage nichts, was sich dort beantworten lässt – nenne stattdessen, was du gefunden hast.
2. **Fragen in Runden** über das `AskUserQuestion`-Tool stellen: max. 4 Fragen pro Runde, jeweils 2–4 konkrete Optionen, die empfohlene Option zuerst mit „(Empfohlen)“ markiert.
3. **Priorität der Fragen:**
   - Ziel & Erfolgskriterium (wofür, bis wann, woran gemessen)
   - Harte Rahmenbedingungen (Zeitbudget, Geräte, Plattform, Datenquellen, Budget)
   - Präferenzen, die das Design ändern (Sprache, Stil, Benachrichtigungen)
   - Nice-to-haves zuletzt
4. **Nicht fragen**, wenn es eine sinnvolle Konvention gibt – dann Default wählen und in der Zusammenfassung erwähnen.
5. **Maximal 3 Runden.** Danach mit Annahmen weitermachen.
6. **Zusammenfassung:** Entscheidungen + getroffene Annahmen als kurze Liste ausgeben (und bei Bedarf in `docs/requirements.md` ablegen), dann mit der Umsetzung starten.

## Sprache

Antworte in der Sprache des Users (hier: Deutsch).
