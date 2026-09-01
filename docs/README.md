# Dokumentation

## Zum Lesen im Browser

Vier Übersichten als eigenständige HTML-Dateien — einfach im Browser öffnen,
sie brauchen keinen Server und keine Abhängigkeiten außer Webfonts.

| Datei | Was drinsteht |
|---|---|
| [architektur.html](architektur.html) | Gesamtschaubild: die Dienste, beide Pipelines, Datenflüsse und Umsetzungsstand |
| [bauplan.html](bauplan.html) | Der Transformationsplan von PaddleDoc zu Weave, mit abhakbaren Arbeitspaketen je Phase |
| [glossar.html](glossar.html) | 63 Fachbegriffe, je allgemein erklärt und auf ihre konkrete Rolle in Weave übersetzt |
| [betriebshandbuch.html](betriebshandbuch.html) | Variablen, Erstinbetriebnahme, stille Fehlkonfigurationen, Selbsttest |

## Zum Nachschlagen im Repo

- **[betrieb.md](betrieb.md)** — dieselbe Betriebsdoku als Markdown. Das ist die
  maßgebliche Fassung; das HTML ist die lesbare Aufbereitung davon.
- **[adr/](adr/)** — Architekturentscheidungen mit Kontext, Konsequenzen und
  verworfenen Alternativen. Sie beantworten die Frage, die in einem Jahr am
  teuersten wird: „Warum eigentlich so?"
- **[firewall-requirements.md](firewall-requirements.md)** und
  **[supply-chain-hardening-blueprint.md](supply-chain-hardening-blueprint.md)** —
  aus dem PaddleDoc-Erbe, betreffen weiterhin den Ingest-Dienst.

Die Datenverträge zwischen den Diensten liegen nicht hier, sondern zentral in
[../contracts/](../contracts/).
