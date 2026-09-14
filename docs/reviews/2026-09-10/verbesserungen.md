# Priorisierte Verbesserungen

Diese Liste setzt den [Sicherheitsreview](sicherheit.md) und den
[Funktionsreview](funktion.md) in abnehmbare Arbeitspakete um. S/M/L
beschreibt den relativen Umfang, keine zugesagte Dauer. Stand 13.09.2026:
vier Positionen sind vollständig umgesetzt (durchgestrichen, mit ✅ und
Verweis auf den jeweiligen Einzelbefund); drei sind teilweise umgesetzt und
bleiben dafür offen aufgeführt. Die übrigen Punkte sind unverändert offen.

## Empfohlene Reihenfolge

| Reihenfolge | Arbeitspaket | Bezug | Umfang | Fertig, wenn … |
|---|---|---|---|---|
| 1 | Frühe Upload-Grenze und Authentifizierung | SEC-02 | M | Große/unangemeldete Requests werden vor vollständigem Spooling abgebrochen. |
| 2 | Chat-Pre-Session an Handoff-Code binden | SEC-01 | M | Ein in Browser A ausgegebener Code ist in Browser B unbrauchbar. |
| 3 | Worker und Retrieval auf dieselbe Modellkonfiguration bringen | FUN-01 | M | Teilweise umgesetzt (13.09.2026): Compose/Helm-Vertrag liefert Worker jetzt URL und Token; Reindexierungs-Smoketest fehlt noch. |
| 4 | ~~Plattform-CI und Chart-Prüfer korrigieren~~ ✅ | FUN-07/08 | M | Erledigt (13.09.2026): eigener Plattform-CI-Job und korrigierter Chart-Prüfer, siehe [funktion.md](funktion.md). |
| 5 | Helm-Bootstrap vor abhängigen Diensten ermöglichen | FUN-04 | M | Ein frischer Stack installiert mit `--wait` und leeren Datenvolumes. |
| 6 | Recovery über IDs und belastbaren Workerstatus entscheiden | FUN-05 | M–L | Aktive Jobs werden auch bei Control-Plane-Störungen nicht doppelt gestartet. |
| 7 | n8n-Bearer und Streaming-Vertrag vervollständigen | FUN-02/03 | S/M | Teilweise umgesetzt (13.09.2026): FUN-02 (Bearer-Header) erledigt; FUN-03 (Streaming-Vertrag) weiterhin offen. |
| 8 | Secret-Dateien sicher schreiben und Passwörter korrekt kodieren | SEC-03, FUN-06 | S–M | Teilweise umgesetzt (13.09.2026): SEC-03 (0600 ab Anlage) erledigt; FUN-06 (Sonderzeichen in Passwörtern) weiterhin offen. |
| 9 | Dokumentpasswörter aus URLs entfernen | SEC-04 | M | Preview, Edit, Delete und Download funktionieren ohne Secret-Query. |
| 10 | Betriebsdiagnostik von Benutzerantworten trennen | SEC-05 | S–M | Synthetische Secret-Marker verlassen die Admin-Diagnose nicht. |
| 11 | ~~Fehlerhafte Sessionabläufe strikt abweisen~~ ✅ | FUN-09 | S | Erledigt (13.09.2026): siehe [funktion.md](funktion.md). |

Die ersten Schritte sollten eigene kleine PRs mit den jeweils beschriebenen
Regressionstests werden. Ein Architekturumbau ist nicht Voraussetzung dafür,
die bestätigten Fehler zu beheben.

## Nachträglich gemeldete UI-Verbesserungen

Ergänzt am 12.09.2026 auf Basis der Nutzerrückmeldung. Alle drei Punkte sind
offen; sie wurden nicht im ursprünglichen Audit verifiziert und zählen daher
nicht zu dessen 14 bestätigten Befunden.

### UI-01 · Zugriff auf das Wissen anderer Teammitglieder ermöglichen

**Ist:** Zusätzliche Mitglieder eines Teams können in der UI noch nicht auf
das Wissen der anderen Teammitglieder zugreifen.

**Soll / Abnahme:** Ein neu hinzugefügtes Teammitglied kann über die UI auf
das für dieses Team freigegebene Wissen anderer Mitglieder zugreifen.
Teamzugehörigkeit und bestehende Freigaben werden berücksichtigt; private
oder ausschließlich für andere Teams freigegebene Inhalte bleiben geschützt.
Den Ablauf mit mindestens zwei Mitgliedern sowie einem Nutzer außerhalb des
Teams prüfen.

### UI-02 · Confluence-Importe durch den Ersteller bearbeiten

**Ist:** Bei einem bereits durchgeführten oder wiederkehrenden
Confluence-Import fehlt dem Ersteller die Option zum Bearbeiten.

**Soll / Abnahme:** Der Ersteller kann beide Importarten in der UI öffnen,
ihre Konfiguration bearbeiten und speichern. Bei wiederkehrenden Importen
lässt sich auch der Zeitplan ändern. Gespeicherte Änderungen sind beim
erneuten Öffnen sichtbar und gelten für nachfolgende Ausführungen.

### UI-03 · Confluence-Verbindungen direkt bei der Import-Einrichtung anlegen

**Ist / Wunsch:** Das Anlegen von Confluence-Verbindungen mit einem Personal
Access Token (PAT) soll auch im Menü zur Einrichtung eines Confluence-Imports
möglich sein.

**Soll / Abnahme:** Während der Import-Einrichtung können eine oder mehrere
Confluence-Verbindungen mit PAT angelegt werden. Neu angelegte Verbindungen
stehen dort unmittelbar zur Auswahl, ohne den bisherigen Einrichtungsstand
zu verlieren. Den Ablauf sowohl ohne bestehende Verbindung als auch beim
Hinzufügen weiterer Verbindungen prüfen.

## Zusätzlicher Nutzerflow-Review vom 12.09.2026

Browserprüfung und zwei günstige Luna-Agenten ergänzen die folgenden offenen
Punkte. Details, Reproduktion und Abnahme stehen im
[UI-Nutzerflow-Review](ui-nutzerflows-2026-09-12.md). Admin-Funktionen waren
ausgeschlossen. Die Browser-Sitzung hatte eine Admin-Rolle und belegt daher
keine vollständige Prüfung der Mitgliederberechtigungen.

| Bezug | Priorität | Arbeitspaket | Verifikation |
|---|---|---|---|
| UI-04 | P1 | ~~Importentwurf beim Wechsel zur Verbindungseinrichtung erhalten~~ ✅ | Erledigt (13.09.2026): Entwurf in `sessionStorage`, siehe [source-form.tsx](../../../services/ingest/frontend/src/components/portal/source-form.tsx). |
| UI-05 | P1 | ~~Erreichbaren nächsten Schritt für Dokumente ohne Wissensbereich anbieten~~ ✅ | Erledigt (13.09.2026): Neuimport-Hinweis mit Link, siehe Jobdetail. |
| UI-06 | P2 | Konto, Verbindungen und Jobdetails verständlich und deutsch benennen | Teilweise umgesetzt (13.09.2026): Konto („API-Zugriff“) und Confluence-Verbindungen vollständig deutsch; die Jobdetailseite trägt weiterhin englische Feldlabels (Filename, Status, Created …). |
| UI-07 | P1 | Hängen gebliebenen Chat-Handoff klären und Ladefehler auffangen | Teilweise umgesetzt (13.09.2026): zeitlich begrenzter Ladezustand mit Fehler und „Erneut prüfen“ ergänzt ([login/page.tsx](../../../services/ingest/frontend/src/app/login/page.tsx)); die eigentliche Ursache des hängenden Handoffs ist weiterhin ungeklärt. |
| UI-08 | P1 | ~~Markdown-Speichern nach Netzfehler wieder ermöglichen~~ ✅ | Erledigt (13.09.2026): `try/catch/finally` um `saveMarkdown()`. |
| UI-09 | P1 | ~~Qualitätsstufe C konsistent als prüfpflichtig statt pauschal blockiert darstellen~~ ✅ | Erledigt (13.09.2026): Verarbeitung und Prüfung stimmen überein, Activity-Test korrigiert. |
| UI-10 | P2 | ~~Hauptaktion bei laufendem/fehlgeschlagenem Import zum passenden Importstatus führen~~ ✅ | Erledigt (13.09.2026): `activityUrl()` routet auf laufende/fehlgeschlagene Importe. |
| UI-11 | P2 | ~~Ursprüngliches Seitenziel über die Anmeldung hinweg erhalten~~ ✅ | Erledigt (13.09.2026): `returnTo` in beiden Redirect-Pfaden (`api.ts` UND `auth-context.tsx`), mit Regressionstest. |

Diese Ergänzungen ändern die Anzahl der ursprünglichen 14 Auditbefunde nicht.

## Weitere Produktwünsche vom 12.09.2026

### UI-12 · Gesamten Wissensbereich mit KI prüfen („KI als Prüfer“)

**Status:** Offen; neuer Produktwunsch, kein nachgewiesener Fehler.

**Ziel:** Auf der Detailseite eines Wissensbereichs einen Button
„Mit KI prüfen“ anbieten. Er startet eine Qualitätsprüfung des gesamten
Wissensbereichs und beantwortet, ob die Dokumente sauber aufbereitet,
verständlich und für die KI-Nutzung geeignet sind.

**Vorgeschlagener Prüfumfang:**

- Pro Dokument: leere oder unvollständige Inhalte, auffällige OCR-Fehler,
  unleserliche Tabellen, zerstörte Formatierung und störende Wiederholungen.
- Über den gesamten Wissensbereich: Duplikate, widersprüchliche Aussagen
  und erkennbare veraltete Angaben. Vermutungen und nicht überprüfbare
  Aussagen ausdrücklich kennzeichnen.
- Ergänzend technische Vollständigkeit prüfen: fehlgeschlagene Verarbeitung,
  ausstehende Freigaben und fehlende Indexierung getrennt von der
  KI-Inhaltsbewertung ausweisen.

**Ablauf / Abnahme:**

- Berechtigte Nutzer starten die Prüfung direkt im Wissensbereich. Vor dem
  Start sind Umfang und verwendetes KI-Modell sichtbar; vorhandene
  Zugriffsrechte gelten auch für Prüfung und Ergebnisbericht.
- Die Prüfung läuft im Hintergrund. Fortschritt, Fehler und Abschluss sind
  sichtbar; der Nutzer kann die Seite verlassen und später zurückkehren.
- Der Bericht zeigt eine Zusammenfassung sowie priorisierte Auffälligkeiten
  mit betroffenem Dokument, konkreter Fundstelle, Begründung und empfohlenem
  nächsten Schritt. Dokumentübergreifende Befunde verlinken alle betroffenen
  Quellen.
- Der Bericht nennt geprüfte Dokumentversionen, Prüfzeitpunkt und Abdeckung
  (geprüft, übersprungen, fehlgeschlagen). Änderungen nach der Prüfung machen
  den Bericht als veraltet erkennbar. Eine Teilprüfung darf nicht als Prüfung
  des gesamten Wissensbereichs erscheinen.
- Die Bewertung unterscheidet „keine Auffälligkeiten gefunden“,
  „Prüfbedarf“ und „nicht prüfbar“. Sie ist eine Qualitätsbewertung, keine
  Garantie fachlicher Richtigkeit. Eine automatische Bereinigung kann der
  Nutzer anschließend über UI-13 starten; Freigaben bleiben separate
  Nutzeraktionen.
- Zur Abnahme einen Wissensbereich mit sauberen Dokumenten, absichtlichen
  OCR-/Tabellenfehlern, Duplikaten und Widersprüchen prüfen; Fundstellen,
  vollständige Abdeckung und verständliche nächste Schritte kontrollieren.

### UI-13 · Gefundene Punkte nach der KI-Prüfung automatisch bereinigen

**Status:** Offen; Folgefunktion zu UI-12.

**Ziel:** Im abgeschlossenen Prüfbericht einen Button „Automatisch bereinigen“
anbieten. Er behebt die automatisch korrigierbaren Auffälligkeiten des
Wissensbereichs, etwa Formatierungsfehler, störende Wiederholungen und anhand
der Quelle eindeutig rekonstruierbare OCR-Fehler.

**Ablauf / Abnahme:**

- Der Bericht unterscheidet automatisch behebbare Punkte von solchen, die
  eine fachliche Entscheidung benötigen. Der Nutzer kann alle geeigneten
  Punkte oder eine Auswahl mit einem Klick zur Bereinigung starten.
- Die Bereinigung läuft im Hintergrund und zeigt Fortschritt sowie je
  Befund „behoben“, „nicht behoben“ oder „manuelle Prüfung nötig“ an.
  Widersprüchliche Fakten oder fehlende Inhalte werden ohne belastbare
  Grundlage nicht durch erfundene Angaben ersetzt.
- Änderungen werden als neue Dokumentversionen gespeichert. Originale und
  bisher freigegebene Stände bleiben erhalten; ein Vorher-/Nachher-Vergleich
  und das Zurücknehmen der Bereinigung sind möglich. Duplikate werden nicht
  unwiderruflich gelöscht.
- Nur Dokumente mit entsprechender Bearbeitungsberechtigung und dem noch
  aktuellen geprüften Stand werden verändert. Zwischenzeitlich geänderte
  Dokumente werden mit verständlichem Hinweis zur erneuten Prüfung markiert.
- Nach der Bereinigung werden die betroffenen Punkte erneut geprüft. Der
  Ergebnisbericht zeigt behobene und verbleibende Auffälligkeiten sowie die
  Aktion zum Prüfen und Freigeben der bereinigten Versionen.
- Zur Abnahme mehrere Dokumente gemeinsam bereinigen: nachvollziehbare
  Korrekturen, unveränderte Originale, Rücknahme, Teilfehler und erneute
  Qualitätsprüfung überprüfen. Eine wiederholte Ausführung darf bereits
  behobene Punkte nicht erneut verändern.

## Ergänzende Verbesserungen ohne bestätigten Exploit

### Tests näher an den Betrieb bringen

- Eine PostgreSQL-/pgvector-Teststrecke ergänzen. Die aktuellen SQLite-Tests
  prüfen die echte Postgres-Vektorsuche, Indizes und alle migrationsspezifischen
  SQL-Pfade nicht vollständig.
- Einen durchgehenden Test mit frischen Diensten aufbauen: Anmeldung → Upload
  → Verarbeitung → Freigabe → Indexierung → Suche/Chat → Entzug der Freigabe.
  Zwei Nutzer mit unterschiedlichen Teams müssen getrennte Ergebnisse sehen.
- Broker-/Worker-Ausfälle gezielt injizieren; Recovery und Statusanzeigen
  dürfen Ungewissheit nicht als Erfolg oder leere Aktivitätsmenge darstellen.
- Echte OCR-, Embedding- und Reranker-Modelle in einer separaten, bewusst
  gestarteten Teststrecke prüfen. Die schnellen PR-Tests behalten ihre Fakes.
- Frontend-Verifikation aus `npm ci` und der deklarierten Node-Version starten.
  Die vorgefundenen `node_modules` enthielten trotz neuer Lockfiles alte
  Next.js-Dateien. Ein Build aus solchen Beständen belegt den aktuellen
  Lockfile-Stand nicht.

### Interne Berechtigungen enger fassen

`CHAT_CONFIG_SERVICE_TOKEN` erschließt mehreren internen Consumern mehrere
Konfigurationen mit entschlüsselten Credentials. Das ist die bestehende
Vertrauensgrenze, kein nachgewiesener Zugriff durch normale Benutzer.
Eigene Consumer-Tokens/Audiences mit getrennten Projektionen begrenzen den
Schaden eines kompromittierten Dienstes. Quelle:
[interne Provider-API](../../../services/ingest/backend/app/api/chat_provider.py),
Zeilen 51–58, und entsprechende Retrieval-/Bot-Endpunkte.

Interne Compose-Portfreigaben möglichst entfernen oder für lokale Diagnose
an Loopback binden. Netzwerkregeln zwischen Diensten anhand der bereits
dokumentierten Kommunikationspfade ergänzen. Service-Authentifizierung
weiterhin beibehalten; die bestehende flache Netzstruktur ist in der
Wurzel-README bereits offen beschrieben.

### Produktvertrag für Bot-Metadaten festlegen

Die authentifizierte Bot-Liste und Detailansicht können Teamnamen,
Collection-Slugs und Systemprompts zugangsbeschränkter Bots enthalten.
Die Ausführung prüft die Bot-Berechtigung separat. Ob die Metadaten ebenfalls
vertraulich sind, ist eine Produktentscheidung. Quelle:
[Gateway-Bot-API](../../../services/api/backend/app/api/bots.py), Zeilen 14–30;
[Runtime-Projektion](../../../services/runtime/backend/app/api/internal.py),
Zeilen 45–72.

Empfehlung: Nur ausführbare Bots oder eine ausdrücklich reduzierte öffentliche
Projektion anzeigen. Mit einem Nutzer außerhalb des Bot-Teams testen.
Dieser Punkt wird nicht als bestätigter Zugriff auf fremde Dokumente gewertet.

### Diagnose, Readiness und Betrieb

- Für Embeddings/Reranker Liveness und Modellbereitschaft trennen. Die
  Embeddings-Readiness verwendet derzeit den auch während des Ladens mit 200
  antwortenden Health-Endpunkt. Ein separates `/ready` sollte erst nach
  erfolgreichem Modellladen bereit melden. Quelle:
  [Probe](../../../deploy/charts/weave/templates/embeddings-deployment.yaml),
  Zeilen 121–132.
- Fehlende/alte Provider-Konfiguration mit Version und Fehlerkategorie melden,
  ohne Schlüssel zu protokollieren. Die Admin-Oberfläche sollte erkennen
  lassen, welcher Worker welchen Konfigurationsstand verwendet.
- Python-Locks für die übrigen Dienste ebenfalls mit Hashes versehen;
  Container-Basisimages und Modellrevisionen reproduzierbar pinnen. Die
  vorhandenen SHA-Pins der GitHub Actions beibehalten.
- Die sechs Ingest-Lint-Warnungen zu interner Navigation bereinigen, wenn die
  betroffenen Komponenten ohnehin bearbeitet werden. Sie sind keine
  Sicherheitslücke.

### URL- und Rendering-Härtung

- Confluence-Antwortlinks zusätzlich auf HTTPS-Erhalt prüfen. Der vorhandene
  Vergleich nutzt Host **und effektiven Port**: Ein normaler Wechsel von
  HTTPS:443 nach HTTP:80 wird bereits verhindert. HTTP auf explizitem Port 443
  ist ein engerer Sonderfall; ein allgemeiner Downgrade wurde nicht bestätigt.
  Quelle: [Hostprüfung](../../../services/ingest/backend/app/services/confluence.py),
  Zeilen 284–326.
- Ungültige URL-Ports früh als Konfigurationsfehler behandeln und URL-Parser-
  Exceptions einheitlich übersetzen. Ein vollständiger API-Reproduktionspfad
  für einen daraus entstehenden 500 wurde in diesem Audit nicht ausgeführt.
- Nonce-/Hash-basierte CSP statt `unsafe-inline` prüfen. Sanitisiertes Markdown
  und URL-Schema-Prüfungen beibehalten; keine bestätigte XSS-Senke gefunden.

## Bewusst nicht als Sicherheitsbefund gewertet

- Headerlose Public-POSTs allein belegen kein Browser-CSRF: Moderne
  Cross-Origin-Browserrequests senden Origin, auch `null` wird vom bestehenden
  Guard abgewiesen. Ein konkreter Browser-Angriff auf diesen Ingest-Pfad wurde
  nicht nachgewiesen. SEC-01 betrifft einen anderen, belegten Handoff-Pfad.
- DB-gespeicherte Legacy-Dateipfade verdienen eine zentrale Root-Prüfung;
  ein erreichbarer Nutzerpfad zum Setzen beliebiger Serverpfade wurde nicht
  gefunden.
- Besitz eines internen Service-Tokens verschiebt die Vertrauensgrenze.
  Daraus allein folgt kein Autorisierungs-Bypass für normale API-Nutzer.
- HMAC-Events besitzen bereits teilweise Idempotenz. Zusätzliche Body- und
  Replay-Grenzen sind sinnvoll, aber es wurde kein Freigabe- oder ACL-Bypass
  durch Replay nachgewiesen.
