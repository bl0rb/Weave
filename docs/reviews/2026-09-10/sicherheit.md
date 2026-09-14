# Sicherheitsprüfung vom 10.09.2026

Stand: Commit `7333479cbcff5395797fc93de66dc38a6ccb8f60`. Umfang und Grenzen
stehen in [README.md](README.md), ausgeführte Prüfungen in
[testprotokoll.md](testprotokoll.md). Die Empfehlungen sind noch nicht umgesetzt.

P1 bedeutet zeitnah beheben, P2 regulär priorisieren, P3 Härtung. Die
Einstufung berücksichtigt die unten genannten Voraussetzungen. Ein grüner
Dependency-Scan deckt diese Fehler im eigenen Code nicht ab.

## SEC-01 · P1 · Chat-Handoff ist nicht an den anfragenden Browser gebunden

**Beleg:** [Chat-Callback](../../../services/chat/src/app/api/auth/sso/callback/route.ts),
Zeilen 46–61; [Login-URLs](../../../services/chat/src/lib/sso.ts), Zeilen 141–144;
[Gateway-Austausch](../../../services/api/backend/app/api/auth.py), ab Zeile 927.

Der Chat-Callback liest einen `code`, tauscht ihn serverseitig gegen eine
Session und setzt sein Cookie. Er verlangt keine zuvor auf dem Chat-Origin
gestartete Anmeldung und prüft weder lokalen State noch eine Browser-Bindung.
Der bestehende Happy-Path-Test in `route.test.ts` setzt mit einer Request-URL
ohne vorheriges Cookie erfolgreich eine Session.

**Auswirkung:** Ein Angreifer kann sich selbst anmelden, seine noch nicht
eingelöste Callback-URL abfangen und das Opfer innerhalb der kurzen
Code-Laufzeit dorthin navigieren lassen. Der Chat des Opfers läuft dann unter
dem Angreiferkonto. Nachfolgende Fragen und Konversationen können für den
Angreifer zugänglich sein. Dies ist Login-CSRF; ein Diebstahl der bisherigen
Opfer-Session wurde nicht nachgewiesen.

**Vorhandener Schutz:** OIDC-State, Nonce und PKCE schützen den vorgelagerten
Gateway-Login. Die Übergabecodes sind zufällig, kurzlebig und einmalig; die
Callback-Adresse ist allowgelistet. Diese Maßnahmen binden den letzten
Übergabeschritt nicht an den Chat-Browser. Auch `SameSite=Lax` verhindert die
Navigation zu diesem GET-Callback nicht.

**Empfehlung:** Vor beiden SSO-Einstiegen eine kurzlebige Chat-Pre-Session
anlegen und den späteren Code kryptografisch an deren Nonce/Verifier binden.
Die Korrelation muss beim Gateway-Handoff und beim Chat-Austausch erhalten
bleiben. Ein frei mitgesendeter State ohne Bindung an den ausgegebenen Code
reicht nicht. Callback-Allowlist, Einmalverwendung und HttpOnly-Cookies
beibehalten; Pre-Session nach Abschluss löschen. Das entspricht dem
[OWASP-Ansatz gegen Login-CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

**Abnahme:** Zwei getrennte Browser-Kontexte A/B: Der von A erhaltene gültige
Code darf in B keine Session setzen. Fehlender/falscher Verifier sowie Replay
werden abgewiesen; der vollständige legitime Login funktioniert weiterhin.

## SEC-02 · P1 · Upload-Body wird vor Anmeldung und Größenprüfung verarbeitet

**Beleg:** [Upload-Route](../../../services/ingest/backend/app/api/routes.py),
Zeilen 1298–1369; [Storage-Limit](../../../services/ingest/backend/app/services/storage.py),
Zeilen 90–123. Der Multipart-Parser erzeugt das `UploadFile`, bevor die
Handler-Prüfung `save_upload()` greift.

**Isoliert reproduziert:** Bei `MAX_UPLOAD_BYTES=1024` wurden ohne Anmeldung
**2.097.161 Datei-Bytes** in den Multipart-Spool geschrieben. Erst danach
antwortete `POST /api/v1/upload` mit `401`. Die Reproduktion benutzte einen
lokalen ASGI-TestClient und einen knapp 2 MiB großen synthetischen Inhalt;
es wurde kein laufender Server belastet.

**Auswirkung:** Bei direkt erreichbarem Backend oder fehlendem vorgeschaltetem
Body-Limit kann bereits ein nicht angemeldeter Client temporären Speicher,
Plattenplatz und I/O beanspruchen. Parallele oder sehr große Requests können
die Verfügbarkeit beeinträchtigen. Der Versuch belegt die fehlende frühe
Grenze; ein tatsächlicher Ressourcen-Ausfall wurde nicht provoziert.

**Vorhandener Schutz:** Das Handler-Limit begrenzt die spätere Speicherung,
löscht zu große Zielobjekte und der Handler begrenzt die Aufrufrate. Ein vom
Betreiber konfiguriertes Proxy-Limit kann das Risiko senken. Diese Schranken
beenden das Multipart-Lesen im getesteten direkten App-Pfad nicht frühzeitig.

**Empfehlung:** Gesamte Request-Bytes schon vor Multipart-Parsing begrenzen;
`Content-Length` früh prüfen und tatsächlich empfangene Bytes auch ohne diesen
Header zählen. Authentifizierung für Upload-Routen soweit möglich vor dem
Body-Parsing durchführen. Proxy-Grenze, begrenzte Parallelität und Tempfile-
Bereinigung ergänzen. Ein reiner Header-Check reicht bei gestreamten Bodies
nicht. Siehe [OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

**Abnahme:** Zu großer Body mit/ohne Content-Length wird vor vollständiger
Spool-Verarbeitung abgebrochen; keine Jobs oder bleibenden Dateien entstehen.
Unauthentifizierte Uploads werden früh zurückgewiesen. Gültige Uploads knapp
unterhalb des Limits funktionieren einschließlich Multipart-Overhead.

## SEC-03 · P2 · Generierte Secret-Datei kann für andere lokale Nutzer lesbar sein

**Status (13.09.2026):** ✅ Umgesetzt. `render()` schreibt jetzt atomar über
eine temporäre Datei mit Modus 0600 und ersetzt das Ziel erst danach
([weave_config.py](../../../scripts/weave_config.py)), mit Regressionstest in
[test_weave_config.py](../../../scripts/tests/test_weave_config.py).

**Beleg:** [Konfigurationsgenerator](../../../scripts/weave_config.py),
Zeilen 261–272. `Path.write_text()` schreibt alle aufgelösten Secrets ohne
explizite restriktive Dateirechte.

**Reproduktion:** Ein Render mit ausschließlich synthetischen Secrets erzeugte
im Audit `compose.env` mit Modus **0644**. Das Ergebnis hängt von umask und
vorhandenen Dateirechten ab. Bei zugänglichen Elternverzeichnissen dürfen
andere lokale Nutzer diese Datei lesen.

**Vorhandener Schutz:** Die echte `deploy/.env` ist von Git ausgeschlossen;
fehlende Pflichtwerte lassen das Rendern scheitern. Gitignore schützt nicht
vor lokalem Dateizugriff.

**Empfehlung:** Neue Secret-Dateien von Anfang an mit Modus 0600 erzeugen,
atomar ersetzen und auch bestehende zu breite Rechte korrigieren. Nicht erst
nach einer normalen, kurzzeitig lesbaren Anlage `chmod` ausführen. Symlinks
und Fehler bei der Rechtevergabe kontrolliert behandeln.

**Abnahme:** Erstes Rendern unter umask 022 und erneutes Rendern über einer
0644-Datei enden mit 0600. Ein Abbruch hinterlässt keine teilweise geschriebene
oder für andere Nutzer lesbare Secret-Datei.

## SEC-04 · P2 · Dokumentpasswörter stehen in Request-URLs

**Beleg:** [Job-Seite](../../../services/ingest/frontend/src/app/jobs/%5Bid%5D/page.tsx),
Zeilen 182–188, 274–279, 419–427 und 580–584;
[Markdown-Ansicht](../../../services/ingest/frontend/src/components/markdown/markdown-view.tsx),
Zeilen 50–52, 128–131 und 252–255;
[Dokumentbrowser](../../../services/ingest/frontend/src/components/document-browser.tsx),
Zeilen 412–416. Auch die Backend-Verträge nehmen `password` als Query an.

**Auswirkung:** Preview, Speichern, Löschen und Downloads können das
Dokumentpasswort in Proxy-/APM-Logs und kopierbare URLs schreiben. Direkte
Download-Navigationen können weitere Spuren in Browser-Verläufen hinterlassen.
URL-Encoding verbirgt das Passwort nicht.

**Vorhandener Schutz:** Anmeldung und objektbezogene Autorisierung werden im
Backend zusätzlich geprüft. Ein geleaktes Dokumentpasswort allein gewährt
daher keinen Zugriff auf fremde Dokumente.

**Empfehlung:** Passwort in Header oder Body übertragen. Für direkte Bilder
und Downloads kurzlebige, berechtigungsgeschützte Download-Tickets oder
authentifizierten Fetch mit Blob-URL verwenden. Die Änderung muss Frontend,
Backend und CORS-Header gemeinsam abdecken; Log-Redaktion zusätzlich prüfen.

**Abnahme:** Alle betroffenen Flows funktionieren mit Sonderzeichen im
Passwort; keine erzeugte URL enthält `password=` oder das Passwort selbst.
Fremde Benutzer bleiben trotz korrektem Dokumentpasswort ausgeschlossen.

## SEC-05 · P2 · Rohe VL-Diagnostik gelangt in normale Job-Antworten

**Beleg:** [Vision-Verarbeitung](../../../services/ingest/backend/app/services/paddle_service.py),
Zeilen 1155–1164 und 1341–1346;
[Worker-Fehlerablage](../../../services/ingest/backend/app/workers/tasks.py),
Zeile 517; [Job-Serialisierung](../../../services/ingest/backend/app/api/routes.py),
Zeilen 148–155.

Bis zu 400 Zeichen einer Remote-Fehlerantwort werden in eine Exception
übernommen. Der Worker persistiert deren Text als `error_message`; die
Job-Antwort reicht ihn und `processing_info` an berechtigte Job-Leser weiter.
Erfolgreiche Vision-Metadaten enthalten außerdem `api_base`.

**Auswirkung und Grenze:** Interne Endpunktnamen und Diagnoseinhalte können
für Teammitglieder sichtbar werden. Ein tatsächlicher API-Key-Leak wurde
nicht beobachtet; er setzt voraus, dass der Remote-Dienst sensible Daten in
seiner Fehlerantwort spiegelt. Der Code verhindert das Weiterreichen solcher
Antworten jedoch nicht.

**Vorhandener Schutz:** Job-Sichtbarkeit bleibt berechtigungsgebunden;
Provider-Credentials sind verschlüsselt gespeichert. Verbindungsfehler aus
`safe_fetch` werden bereits teilweise auf ungefährliche Labels reduziert.

**Empfehlung:** Normale Antworten auf Verbindungslabel, Fehlerkategorie und
Statuscode begrenzen. Betriebsdiagnosen getrennt und redigiert für Admins
ablegen; `api_base` aus der Benutzerprojektion entfernen, wenn es dem Nutzer
keine notwendige Entscheidung ermöglicht.

**Abnahme:** Synthetische Remote-Fehler mit interner URL und Secret-Marker
dürfen weder im Job-/Benchmark-Response noch unredigiert im Log erscheinen.

## Bestätigte Schutzmaßnahmen

- Retrieval wendet Team- und Collection-Grenzen vor dem Ranking in SQL an.
- Runtime prüft Bot-Berechtigungen; Tools prüft Signatur, Ablauf, Bot-Bindung
  und Scope delegierter Aufrufe.
- Sessions und API-Tokens werden als Hashes gespeichert; viele Admin-,
  Owner- und Team-Prüfungen besitzen Regressionstests.
- `safe_fetch` pinnt aufgelöste IPs und prüft Redirects sowie private und
  Metadata-Ziele. Es wurde kein allgemeiner SSRF-Bypass nachgewiesen.
- Markdown wird mit Sanitizer verarbeitet; im geprüften Rendering wurde kein
  ausnutzbarer XSS-Pfad bestätigt.

Bewusst getrennte Härtungsideen und nicht bestätigte Angriffshypothesen
stehen in [verbesserungen.md](verbesserungen.md).
