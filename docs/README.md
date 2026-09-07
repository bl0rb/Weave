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
- **[firewall-requirements.md](firewall-requirements.md)** — aus dem
  PaddleDoc-Erbe, betrifft weiterhin den Ingest-Dienst.
- **Lieferketten-Härtung** — der Blueprint dazu steht als Beitrag auf
  [werkworks.de](https://werkworks.de/blog/supply-chain-blueprint/)
  ([englisch](https://werkworks.de/en/blog/supply-chain-blueprint/)) und wird
  dort gepflegt; eine zweite Fassung hier würde nur auseinanderlaufen. Was
  davon in **diesem** Repository umgesetzt ist: M1 (alle Actions in
  `.github/workflows/pr-ci.yml` auf Commit-SHA gepinnt, Helm-Tarball per
  `sha256sum` geprüft), M4 (der Frontend-Build scheitert an einem
  `npm audit`-Fund) und M5 (`.github/dependabot.yml` deckt alle neun Dienste
  ab). M3 nur teilweise — hash-gelockt sind bislang allein
  `services/ingest/backend/requirements.txt` und `requirements-worker.txt`;
  die übrigen sieben sind auf exakte Versionen gepinnt, aber ohne Prüfsummen.
- **[bereinigung.md](bereinigung.md)** — verbindliche Integrationsgrenzen,
  entfernte Altpfade und Kandidaten für spätere, kontrollierte Rückbauten.
- **[screenshots/user-wiki/](screenshots/user-wiki/)** — 31 aktuelle Browseraufnahmen
  in doppelter Pixeldichte mit Beispieldaten für das User-Wiki. Die
  [Galerie](screenshots/user-wiki/index.html) enthält alle Original-PNGs und den
  [Beispielablauf als GIF](screenshots/user-wiki/weave-example-process.gif):
  Anmelden → Hochladen → Verarbeiten → Freigeben → Chat-Mockup.
- **[diagrams/](diagrams/)** — gezeichnete Schaubilder als HTML-Quelle plus
  exportiertes SVG. `network-topology` gehört zum Netzabschnitt der
  [Wurzel-README](../README.md#network-design).
- **[integrations/mail-ingestion.md](integrations/mail-ingestion.md)** — wie
  Dokumente per Mail hereinkommen und warum die frühere separate Mail-API
  entfernt wurde.

Die Datenverträge zwischen den Diensten liegen nicht hier, sondern zentral in
[../contracts/](../contracts/).
Das [Wissensportal](wissensportal.md) beschreibt Freigaben, `document.released`, Betriebskonfiguration und den aktuellen Funktionsumfang.

Die Konfiguration der Plattform ist keine Doku, sondern Code: `weave.yaml` im
Wurzelverzeichnis und `scripts/weave_config.py` (`render`, `check`) — erklärt in
[betrieb.md](betrieb.md) Abschnitt 11.
