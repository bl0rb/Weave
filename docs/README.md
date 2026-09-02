# Dokumentation

## Zum Lesen im Browser

Fünf Übersichten als eigenständige HTML-Dateien — einfach im Browser öffnen,
sie brauchen keinen Server und keine Abhängigkeiten außer Webfonts. Sie sind
die im Repo gepflegte Fassung der veröffentlichten Artefakte; die Kopien auf
claude.ai werden aus genau diesen Dateien neu veröffentlicht.

| Datei | Was drinsteht |
|---|---|
| [architektur.html](architektur.html) | Gesamtschaubild: die Dienste, beide Pipelines, Datenflüsse und Umsetzungsstand |
| [architektur-detail.html](architektur-detail.html) | Alle neun Dienste im Detail plus der zeitliche Ablauf einer Wissensfrage |
| [bauplan.html](bauplan.html) | Der Transformationsplan von PaddleDoc zu Weave mit abhakbaren Arbeitspaketen je Phase — bis Phase 6 (Monorepo, eine Konfiguration) |
| [glossar.html](glossar.html) | 82 Fachbegriffe, je allgemein erklärt und auf ihre konkrete Rolle in Weave übersetzt |
| [betriebshandbuch.html](betriebshandbuch.html) | Dienste aus Betreibersicht, Variablen, Anmeldung, stille Fehlkonfigurationen, Selbsttest |

## Zum Nachschlagen im Repo

- **[betrieb.md](betrieb.md)** — dieselbe Betriebsdoku als Markdown. Das ist die
  maßgebliche Fassung; das HTML ist die lesbare Aufbereitung davon.
- **[adr/](adr/)** — sieben Architekturentscheidungen mit Kontext, Konsequenzen und
  verworfenen Alternativen, zuletzt 0006 (föderierte Anmeldung) und 0007
  (zentrale Chat-Provider-Konfiguration). Sie beantworten die Frage, die in einem Jahr am
  teuersten wird: „Warum eigentlich so?"
- **[firewall-requirements.md](firewall-requirements.md)** und
  **[supply-chain-hardening-blueprint.md](supply-chain-hardening-blueprint.md)** —
  aus dem PaddleDoc-Erbe, betreffen weiterhin den Ingest-Dienst.

Die Datenverträge zwischen den Diensten liegen nicht hier, sondern zentral in
[../contracts/](../contracts/).
Das [Wissensportal](wissensportal.md) beschreibt Freigaben, `document.released`, Betriebskonfiguration und den aktuellen Funktionsumfang.

Die Konfiguration der Plattform ist keine Doku, sondern Code: `weave.yaml` im
Wurzelverzeichnis und `scripts/weave_config.py` (`render`, `check`) — erklärt in
[betrieb.md](betrieb.md) Abschnitt 11.
