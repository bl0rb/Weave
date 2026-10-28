Das Wissen einer Organisation liegt an zwei Orten: in Dokumenten, die niemand
wiederfindet, und in Köpfen, die gerade nicht am Platz sind. Weave nimmt sich
den ersten Teil vor — es liest PDFs, Office-Dateien, Scans, E-Mails,
Meeting-Transkripte, Confluence-Seiten und Websites ein, macht strukturierten
Text mit erhaltenen Metadaten daraus und beantwortet damit Fragen im Chat, in
der Desktop-App oder über MCP in VS Code, mit der Quelle neben jeder Aussage,
sodass man eine Antwort nachprüfen statt glauben muss.

Drei Festlegungen prägen das Produkt und erklären die meisten Eigenheiten, über
die man später stolpert: Verarbeitet ist nicht veröffentlicht — indexiert wird
nur, was jemand ausdrücklich freigibt; Berechtigungen wirken in der Suchabfrage
selbst und nicht als Filter danach; und ohne gefundene Quellen gibt es keine
Antwort statt einer flüssig formulierten ohne Beleg. Die Plattform läuft auf
eigener Infrastruktur, das Sprachmodell ist austauschbar, und im
Auslieferungszustand startet der Stack mit Attrappen-Modellen ohne jede
Verbindung nach außen — er lässt sich also durchprobieren, bevor Daten oder
Budget das Haus verlassen. Ausformuliert steht das im Abschnitt
[What Weave is for](https://github.com/bl0rb/Weave/blob/main/README.md#what-weave-is-for)
der Wurzel-README.

## Wo anfangen

| Rolle | Zuerst | Danach |
|---|---|---|
| **Fachanwender** — legt Wissensbereiche an, lädt Quellen hoch, prüft und gibt frei, fragt im Chat | [[Erste-Schritte]] | [[Wissensbereiche-und-Freigaben]], [[Chat-und-Bots]], [[Haeufige-Fragen]] |
| **Administrator** — pflegt Nutzer, Gruppen, Anmeldeverfahren, Wissensbereiche, Bots und die KI-Einstellungen im Portal | [[Rechte-und-Sichtbarkeit]] | [[Wissensbereiche-und-Freigaben]], [Wissensportal-Doku](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md) |
| **Betreiber** — bringt den Stack zum Laufen, setzt Werte, sucht Fehler | [[Betrieb]] | [docs/betrieb.md](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md) (maßgeblich), [deploy/README.md](https://github.com/bl0rb/Weave/blob/main/deploy/README.md) |
| **Entwickler** — ändert einen Dienst oder programmiert gegen seine Verträge | [README, Abschnitt „Repository layout“](https://github.com/bl0rb/Weave/blob/main/README.md#repository-layout) | [contracts/](https://github.com/bl0rb/Weave/blob/main/contracts/README.md), [docs/adr/](https://github.com/bl0rb/Weave/blob/main/docs/adr/README.md) |

Wer das Wiki zuletzt auf dem Stand von v0.7.1 gelesen hat: [[Was-ist-neu]]
fasst zusammen, was sich bis v0.7.17 geändert hat — unter anderem heißen
„Teams“ in der Oberfläche jetzt „Gruppen“, und „Aufgaben“ ist in „Dokumente“
aufgegangen.

Wer sich vorher über einen Begriff klar werden will: `docs/glossar.html` erklärt
die Fachbegriffe jeweils allgemein und in ihrer konkreten Rolle in Weave. Die
Datei ist zum Öffnen im Browser gedacht, GitHub zeigt sie nur als Quelltext an —
[Übersicht aller Doku-Artefakte](https://github.com/bl0rb/Weave/blob/main/docs/README.md).

## Wo was steht

Dieses Wiki ist der Einstieg, nicht die Doku. Es ordnet ein: in welcher
Reihenfolge man etwas liest, warum eine Sache so gebaut ist, und was regelmäßig
missverstanden wird. Die verbindlichen Fassungen bleiben im Repository und
werden von hier nur verlinkt —
[docs/betrieb.md](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md)
für den Betrieb,
[docs/wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md)
für Freigaben und Indexierungsstatus,
[contracts/](https://github.com/bl0rb/Weave/blob/main/contracts/README.md)
für die Datenverträge zwischen den Diensten,
[docs/adr/](https://github.com/bl0rb/Weave/blob/main/docs/adr/README.md)
für die Architekturentscheidungen samt verworfener Alternativen. Diese Dateien liegen
neben dem Code, ändern sich mit ihm und gewinnen im Zweifel gegen jede Aussage
auf einer Wiki-Seite. Wenn eine Wiki-Seite eine Tabelle mit Variablen, Ports
oder Vertragsfeldern zu enthalten scheint, ist sie veraltet — nachgeschlagen
wird im Repository.
