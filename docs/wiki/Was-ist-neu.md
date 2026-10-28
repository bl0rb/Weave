# Was ist neu

Diese Seite ordnet ein, was sich seit v0.7.1 geändert hat und an welcher
Stelle man es im Alltag bemerkt. Sie ersetzt nicht den
[Changelog](https://github.com/bl0rb/Weave/blob/main/services/ingest/CHANGELOG.md)
— dort stehen alle Einträge je Version, mit Migrationsnummern und API-Pfaden.
Hier steht, was man wissen muss, wenn man das Wiki zuletzt auf dem Stand von
v0.7.1 gelesen hat. Stand dieser Seite: **v0.7.17** (27. Oktober 2026).

## Was im Portal anders aussieht

- **„Teams“ heißen in der Oberfläche jetzt „Gruppen“** (seit v0.7.3). In den
  API-Feldern, Verträgen und der Datenbank bleibt es bei `team` und
  `read_teams`; nur Portal und Administration sprechen von Gruppen. Wo dieses
  Wiki „Gruppe“ sagt, ist dasselbe gemeint wie früher „Team“.
- **Eine Seite „Dokumente“ statt „Aufgaben“ und „Dokumente“** (v0.7.14). Die
  Ansicht hat Voreinstellungen — *Zu prüfen* (Standard), *Alle*, *Geparkt*,
  *Fehler*, *Von mir freigegeben* — und eine Filterleiste für Suche,
  Wissensbereich, Stand, Bewertung und Quelle. Die Filter stehen in der URL
  und lassen sich als Link weitergeben. Die Seite `/aufgaben` gibt es nicht
  mehr; „Prüfen und freigeben“ ist die Voreinstellung *Zu prüfen*.
- **Eine Übersicht als Startseite** mit zentraler Suche (Dateinamen,
  Confluence-Titel, Pfade) und Kennzahlen je Wissensbereich, Sync-Status,
  Wartezeit bis zur Prüfung und Freigaben je Woche.
- **Der Wissensbereich ist eine Verwaltungsseite:** Quellen mit „Jetzt
  synchronisieren“, Zugriff, Wartung (neu indizieren, ZIP-Export) und ein
  Link auf seine gefilterten Dokumente. Die Dokumentliste selbst wohnt unter
  „Dokumente“.
- **„Verbindungen“ ist ein eigener Navigationseintrag.** Confluence- und
  Website-Verbindungen samt ihren Importen stehen dort.
- **Eine Prüfansicht für alles** (v0.7.15): fertige Dokumente öffnen aus den
  Auftragsdetails in der Prüfansicht; die technischen Angaben stecken in einem
  Dialog „Technische Details“. Die Prüfung markiert Schwächen im Text und holt
  auf Wunsch eine Empfehlung vom Chat-Provider.
- **Die Administration ist in fünf Bereiche gegliedert:** *Konten &
  Zugriffe* (Personen, Gruppen, Anmeldung, Wissensbereiche & Zugriff), *Bots &
  KI* (Bots, Chat & LLM, Suche & Modelle, Wissensgedächtnis), *Verarbeitung &
  Quellen*, *Betrieb & Sicherheit* (Sicherung & Wiederherstellung,
  Konfiguration, Technische Identitäten, Werkzeuge) und *Systemstatus*. Die
  alten Pfade `/admin/menschen` und `/admin/wissen` leiten weiter.
- Der VL-Benchmark ist entfernt (v0.7.7); VL-Verbindungen bleiben als
  Verarbeitungsprofil nutzbar.

## Neue Quellen

- **Websites und Intranetseiten** (v0.7.17): eine Website-Verbindung (ohne
  Anmeldung, HTTP Basic oder Bearer-Token) und ein Import mit Startadresse und
  Umfang — nur diese Seite, ihr Bereich oder die ganze Website — samt
  verlinkten PDF-/Office-Dokumenten und Aktualisierungsrhythmus. Adressen im
  privaten Netz bleiben gesperrt, bis ein Administrator sie an der Verbindung
  freigibt. Der Chat zeigt zu solchen Quellen Abrufdatum und, bei
  Rechtsquellen, den ausgewiesenen Stand.
- **Quellenkatalog** (v0.7.17): kuratierte Einträge wie einzelne Gesetze, die
  jeder in einen eigenen Wissensbereich übernehmen kann; nur Administratoren
  pflegen den Katalog.
- **Meeting-Transkripte als `.vtt`** (v0.7.12) aus Teams, Zoom, Webex oder
  Google Meet, mit optionalen Aufräum-Anweisungen in freier Sprache, die der
  zentrale Chat-Provider auf das Transkript anwendet.
- **Mehrere synchronisierte Confluence-Importe je Verbindung** (v0.7.7). Ein
  Import ist jetzt eine Konfiguration mit Umfang, Ziel-Wissensbereich und
  Zeitplan; vorher hatte eine Verbindung genau einen Auto-Sync, und ein
  zweiter Import änderte still, was synchronisiert wurde. Abgebrochene
  Importläufe nehmen seit v0.7.12 von selbst wieder auf.

## Neues im Chat

- **Wissensgedächtnis** (v0.7.9, Opt-in): Zusammenfassungen je Dokument und
  Übersichten je Wissensbereich, aus denen Bots Fragen wie „Was weißt du?“
  oder „Welche Dokumente gibt es zu Y?“ beantworten — mit Quellen, nur im
  Rahmen der Leserechte. Ab v0.7.10 lenkt das Gedächtnis auch die reguläre
  Suche auf die passenden Dokumente. Details in [[Chat-und-Bots]].
- **Je Bot einstellbar:** eine Einstiegsfrage vor der ersten Wissensantwort,
  gesperrte Themen mit fester Antwort, ein Hinweistext neben dem Eingabefeld
  und ob die Quellenleiste gezeigt wird (v0.7.14, v0.7.15).
- **Quellen auf Knopfdruck:** „Quellen anzeigen“ unter jeder Antwort, und es
  werden nur die Quellen gelistet, die die Antwort als `[N]` zitiert.
- **Bots als Dokument** (v0.7.12): jeder Bot lässt sich als YAML/JSON
  exportieren und mit Probelauf wieder importieren; Geheimnisse und Freigaben
  wandern nie mit.
- **„API-Zugang“ im Chat ist standardmäßig aus** (v0.7.14). Der Betreiber
  schaltet die Selbstbedienung für persönliche Tokens mit
  `SELF_SERVICE_API_TOKENS_ENABLED` wieder ein; früher ausgestellte Tokens
  bleiben gültig.

## Weave auf dem Desktop

Seit v0.7.8 gibt es eine Tray- beziehungsweise Menüleisten-App für macOS und
Windows ([clients/desktop](https://github.com/bl0rb/Weave/tree/main/clients/desktop)):
Tastenkürzel, Bot wählen, fragen, Antwort mit Quellen — oder alle lesbaren
Wissensbereiche durchsuchen. Die Anmeldung läuft über das SSO der
Organisation, das Sitzungstoken liegt im Schlüsselbund des Betriebssystems.
Aus dem Web-Chat heraus lässt sich die App mit einem Klick einrichten
(„Desktop-App einrichten“); dabei wandert nur die Serveradresse, nie ein
Zugangsdatum. Betreiberseitig braucht es `NATIVE_LOGIN_ENABLED` in Weave-API
und die öffentliche API-Adresse; siehe [[Betrieb]].

## Für Betreiber

- **Service-Credential statt persönlichem Admin-Token** für den
  Registry-Sync von Knowledge: `WEAVE_KNOWLEDGE_INGEST_API_TOKEN` wird vor dem
  ersten Start erzeugt, der zyklische Schritt der Erstinbetriebnahme entfällt
  ([[Erste-Schritte]]). Der erste Administrator lässt sich über
  Bootstrap-Variablen automatisch anlegen.
- **S3 als Objektspeicher** (v0.7.11) für Originale, Bilder und Markdown,
  mit Migration in beide Richtungen im laufenden Betrieb; optional mit
  serverseitiger Verschlüsselung (v0.7.12). **Sicherungen nach S3** und
  Wiederherstellung aus dem Bucket (v0.7.16).
- **Laufende Konfiguration in der Oberfläche** (v0.7.16): jede Einstellung
  mit maskierten Geheimnissen, Verbindungstests für Datenbank, Redis, S3 und
  Sicherungsverzeichnis, Export als `.env` oder JSON.
- **Lizenz:** ab v0.7.9 MIT mit Commons Clause — nutzen, ändern und selbst
  betreiben bleibt frei, verkaufen oder als Dienstleistung anbieten nicht.
  Versionen bis v0.7.8 bleiben unter reiner MIT-Lizenz.
- Neue Architekturentscheidungen: [ADR-0008](https://github.com/bl0rb/Weave/blob/main/docs/adr/0008-besitz-freigaben-metadaten.md)
  (Besitz und Freigaben für Wissensbereiche und Bots) und
  [ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md)
  (Wissensgedächtnis).

Die Betriebsseite dieses Wikis nennt die zugehörigen Abschnitte des
Betriebshandbuchs: [[Betrieb]].
