# Wissensportal

Das Portal trennt Verarbeitung und Veröffentlichung. `document.processed` bestätigt nur authentifiziert, dass ein Job verarbeitet wurde; die Antwort `awaiting_release` bedeutet, dass noch kein Index-Lauf erfolgt. Regulär indexiert Knowledge ausschließlich `document.released`.

Eine Freigabe ist ein unveränderlicher Snapshot: Pro Job gibt es genau eine Freigabe. Sie darf nur der Dokument- oder Wissensbereichseigentümer oder ein Admin auslösen. Qualitätsstufe C kann nach ausdrücklicher Bestätigung trotz Qualitätswarnung freigegeben werden. Die Bewertung C und die ursprüngliche Empfehlung bleiben unverändert; der signierte Freigabeauftrag enthält `quality_override: true`. Andere Quality-Blocks, Passwortschutz und fehlende Berechtigungen bleiben Sperren. Bei Confluence muss der Import abgeschlossen sein, damit abschließende Links und Hierarchiemetadaten enthalten sind. Für Dokumente ohne Collection zunächst im Portal einen Wissensbereich wählen und die Quelle dort erneut hinzufügen. Eine nachträgliche Zuordnung beliebiger Altaufträge ist noch nicht Teil dieser Oberfläche. Bereits indexierte Legacy-Daten bleiben unverändert.

Der Delivery-Status (`pending`, `sent`, `failed`) beschreibt nur die Zustellung des Webhooks, niemals den Abschluss der Indexierung. Der Chat bleibt eine separate, über `WEAVE_CHAT_PUBLIC_URL` verlinkte Oberfläche.

## Wann ist der Inhalt in der KI-Suche verfügbar?

Nach der Freigabe zeigt das Dokument den Fortschritt automatisch an. **Für KI verfügbar** erscheint erst, wenn Knowledge genau diese Freigabe und ihren Snapshot-Hash bestätigt und die Textabschnitte samt Embeddings vollständig gespeichert sind. Die Abschlusszeit und Anzahl der Textabschnitte stehen im Freigabebereich. Der Status erscheint auch in den Dokumentlisten und in **Verarbeitung**.

Bei ausstehender Indexierung aktualisiert sich die Anzeige alle fünf Sekunden, bei stabilen Zuständen alle 30 Sekunden. Hintergrund-Tabs pausieren; beim Öffnen wird erneut geprüft. Vorschau und Eingaben bleiben dabei unverändert. Ein leerer, ersetzter oder fehlerhafter Index wird gesondert angezeigt. Bei **Indexstatus nicht verfügbar** fehlt gerade die Rückmeldung; eine ältere Bestätigung bleibt dann nicht grün stehen.

Die Abfrage läuft über die bestehende Ingest-Anmeldung. Nur sichtbare Dokumente werden geprüft; der Browser erhält keine Dienst-Zugangsdaten. Die Verbindung nutzt die bereits vorhandene Knowledge-Adresse und den gemeinsamen Webhook-Schlüssel mit einem eigenen Signaturverfahren. Neue Tokens oder eine erneute Freigabe bestehender Dokumente sind nicht nötig. Für dieses Feature Ingest-Backend, Knowledge-API und Ingest-Frontend aktualisieren; die Worker können weiterarbeiten. Details: [Vertrag zum Indexierungsstatus](../contracts/indexing-status.md).

## Betriebskonfiguration

Ingest API und Worker verwenden `PORTAL_KNOWLEDGE_BASE_URL` (im Compose-Stack `http://weave-knowledge:8000`) und `PORTAL_KNOWLEDGE_WEBHOOK_SECRET`. Knowledge verwendet denselben Wert als `WEAVE_INGEST_WEBHOOK_SECRET`. `WEBHOOK_PRIVATE_HOST_ALLOWLIST` behält bestehende n8n-/Admin-Hosts; `weave-knowledge` muss zusätzlich enthalten sein, z. B. `["n8n", "admin-webhook", "weave-knowledge"]`. Fehlt der Portal-Secret, bleibt die Portal-Publikation deaktiviert, während Ingest weiter startet. Im Kubernetes-Chart wird der gemeinsame Wert über ein vorhandenes Secret referenziert.

Knowledge benötigt außerdem das vor dem Deployment erzeugte Service-Credential `WEAVE_KNOWLEDGE_INGEST_API_TOKEN`. Der Konfigurationsrenderer spiegelt es für Ingest nach `KNOWLEDGE_INGEST_API_TOKEN`; API und Worker von Knowledge erhalten es als `WEAVE_INGEST_API_TOKEN`. Ein persönlicher Admin-Token ist nicht erforderlich. Dieser Lesezugang ist vom Webhook-Signaturschlüssel unabhängig. Bei Rotation Ingest und beide Knowledge-Container mit identischem neuem Wert neu erstellen:

```sh
docker compose --env-file deploy/.env -f deploy/docker-compose.weave.yml -f deploy/docker-compose.local.yml up -d --no-deps weave-knowledge weave-knowledge-worker
```

Der Portal-Konfigurationshinweis prüft in dieser Ausbaustufe nur das Vorhandensein von Zieladresse und Signaturschlüssel. Er ist kein Nachweis, dass der Rückabruf mit dem Dienst-Token oder die Indexierung erfolgreich ist.

Confluence-Zugangsdaten sind weiterhin nur für den jeweiligen Eigentümer nutzbar. Die optionale tägliche Aktualisierung verwendet den Umfang des zuletzt gestarteten Imports dieser Verbindung. Sie gilt noch nicht getrennt je Startseite. Neue Dokumentversionen müssen erneut geprüft und freigegeben werden. Individuelle Confluence-Seitenrechte werden nicht automatisch übertragen.

Individuelle Confluence-Seitenrechte werden nicht automatisch als Weave-ACL übernommen. Die unten beschriebene Rücknahme entfernt Inhalte aus Knowledge, nicht aus der Importhistorie oder bereits gespeicherten Chat-Antworten.

## Confluence synchronisieren und fehlende Seiten entfernen

In der Importhistorie (`/imports`) und im einzelnen vergangenen Lauf startet
**Jetzt synchronisieren** einen inkrementellen Sync mit genau dessen Quelle,
Startseite und Optionen. Das funktioniert auch ohne aktivierten Scheduler.
Nur der Quelleigentümer kann deren Zugangsdaten nutzen; parallel aktive Läufe
derselben Quelle verhindern einen weiteren Sync.

Nach vollständigen Syncs werden zuvor importierte, nicht mehr gefundene Seiten
gemeldet. Verglichen wird nur derselbe Startbereich dieser Verbindung. Bei
abgeschnittenen Crawls, Zeitlimits oder Abruffehlern entsteht kein pauschaler
Löschvorschlag. Explizite HTTP-404/410-Antworten melden nur die konkret betroffene
früher importierte Seite. Confluence kann fehlende Berechtigungen ebenfalls als
404 melden: **Nicht mehr in Confluence gefunden** ist deshalb kein Beweis für
eine Löschung beim Quellsystem.

Im Laufdetail kann der Eigentümer **Aus Wissen entfernen** bestätigen. Das
entfernt alle noch zuordenbaren Versionen und OCR-Anhänge dieser Seite aus
Knowledge. Neuere Sync-Ergebnisse und aktive Läufe verhindern veraltete
Löschaktionen. Die Importhistorie und Freigabe-Snapshots bleiben zu
Nachvollziehbarkeitszwecken erhalten; die zurückgezogenen Jobs können nicht
erneut freigegeben werden. Wieder aufgetauchte Seiten erhalten beim nächsten
Sync einen neuen Job und benötigen eine neue Freigabe.

**Löschung ausstehend** bedeutet noch nicht gelöscht. Die Datenbank-Outbox
wiederholt fehlgeschlagene Zustellungen über den bestehenden Publication-Tick.
Erst nach bestätigter Entfernung steht **Aus Wissen entfernt**. Knowledge
entfernt Dokumenttext und Chunks und behält ausschließlich den Widerrufsbeleg
zur Job-ID, damit verspätete Freigabe-Events keine Inhalte wiederherstellen.
Details: [document.withdrawn](../contracts/events/document.withdrawn.md).

## Migration

Vor dem Start des neuen Ingest-Images `alembic upgrade head` bis einschließlich
`0021_knowledge_withdrawals` ausführen. Zuerst Knowledge aktualisieren, danach
Ingest-API, Ingest-Worker und Frontend. Alte Knowledge-Versionen verstehen weder
den C-Override noch die Löschereignisse. Ein nicht unterstützter Löschauftrag
(HTTP 204 statt 200) wird deshalb nicht als erfolgreich zugestellt markiert.

## Ablauf für Fachbereiche

1. **Wissensbereich anlegen** öffnet direkt das Formular (`/knowledge/new`). Nach dem Speichern folgt die Quellenauswahl mit dem neuen Bereich bereits ausgewählt. Als Berechtigte sind die Mitglieder des eigenen Teams vorausgewählt. Zugriff für alle Teams erfordert eine ausdrückliche Bestätigung; ohne Teamzuordnung hilft die Administration.
2. **Quelle hinzufügen**: Dateien (auch einzelne `.eml`-E-Mails) hochladen oder eine bestehende Confluence-Verbindung und Startseite auswählen. Wähle ein Verarbeitungsprofil. Nach dem Upload ersetzt eine Abschlussansicht das Formular: **Weitere Quellen hinzufügen** behält den Wissensbereich bei; **Verarbeitung ansehen** öffnet den Fortschritt. Je nach Dokumentumfang und Auslastung kann die Verarbeitung einige Minuten dauern; die Seite muss nicht offen bleiben.
3. Unter **Prüfen und freigeben** den aufbereiteten Text lesen. Reicht die Qualität nicht aus, **Mit anderem Profil neu verarbeiten** wählen. Erst die ausdrückliche Bestätigung und „Geprüften Stand freigeben“ speichern die Freigabe.
4. Den Fortschritt im Dokument verfolgen: **Freigegeben → Wird indiziert → Für KI verfügbar**. Die Anzeige aktualisiert sich automatisch und zeigt nach Abschluss Uhrzeit und Anzahl der durchsuchbaren Textabschnitte. Derselbe Status erscheint in der Dokumentliste des Wissensbereichs und unter **Verarbeitung**. Bearbeiten und erneutes Verarbeiten eines freigegebenen Auftrags werden mit HTTP 409 abgewiesen. Für geänderten Inhalt ist eine neue Dokumentversion notwendig.
5. **Chat** öffnet die bestehende Chatoberfläche mit derselben föderierten Weave-Anmeldung. Die Integration des Chats direkt in die Portaloberfläche folgt separat.

Im geöffneten Wissensbereich lädt **Alle als ZIP** sämtliche für den angemeldeten Nutzer sichtbaren, fertig verarbeiteten und nicht passwortgeschützten Markdown-Dateien. Das Download-Symbol in einer Dokumentzeile lädt nur dieses Markdown. Bei bereits freigegebenen Dokumenten wird immer der unveränderliche Freigabe-Snapshot exportiert; spätere lokale Änderungen können ihn nicht ersetzen. Doppelte Dateinamen werden im ZIP mit einem kurzen Dokumentbezug eindeutig gemacht. Der Export ändert weder Freigabe noch Indexierungsstatus.

Direktes Löschen freigegebener Aufträge bleibt gesperrt (HTTP 409). Fehlende Confluence-Seiten können über die oben beschriebene Rücknahme aus dem Wissen entfernt werden. Die vorhandenen OIDC-/Benutzer- und Verbindungseinstellungen bleiben unter **Administration** bzw. **Administration → Werkzeuge → Verbindungen** erreichbar. Eigene Confluence-Verbindungen sind außerdem direkt aus der Quellenauswahl erreichbar.


## Verarbeitung und Profilauswahl

**Verarbeitung** (`/processing`) steht direkt in der Portalnavigation. Die Seite zeigt wartende, laufende, verarbeitete und fehlgeschlagene Aufträge mit Suche und Statusfilter. Auch ältere Aufträge ohne Wissensbereich bleiben sichtbar. Aktive Aufträge werden bei sichtbarem Tab alle fünf Sekunden aktualisiert, sonst alle zwanzig Sekunden. Die Anzeige berücksichtigt die vorhandenen Eigentümer-/Teamrechte; Administratoren sehen alle Aufträge. „Verarbeitet“ bedeutet nicht „für die KI freigegeben“ oder „indexiert“.

Im normalen Quellenablauf werden nur diese Profile angeboten, sofern der Server sie liefert:

| Auswahl im Portal | Technisches Profil | Verwendung |
| --- | --- | --- |
| Standard – schnell | `ppocrv6_tiny_structurev3` | Kleine Modelle für Text, Layout und Tabellen |
| Gründlich – komplexe Dokumente | `ppocrv6_medium_structurev3` | Größere Modelle für anspruchsvolle Layouts; mehr Rechenzeit |
| Eingerichtete KI-Modelle | `vl:<connection_id>` | Nur aktivierte VL-Verbindungen aus **Administration → KI-Verbindungen** |

Ein gesetzter Serverstandard wird übernommen, wenn er zu diesen angebotenen Optionen gehört; andernfalls wird das erste verfügbare Portalprofil vorausgewählt. Die Auswahl wird als `profile_id` beim Start genau des neuen Upload-Auftrags gespeichert. Für Confluence wird sie als `ocr_profile_id` mit `ocr_attachments=true` übergeben: Das Profil verarbeitet unterstützte Anhänge, die Seiteninhalte werden direkt aus Confluence übernommen. Die vollständige technische Profilauswahl bleibt unter **Administration → Werkzeuge → Verarbeitung testen** verfügbar.

## Ein Dokument mit anderem Profil neu verarbeiten

In der Dokumentprüfung können Dokument- oder Wissensbereichseigentümer und Administratoren **Mit anderem Profil neu verarbeiten** wählen. Die Auswahl bietet dieselben verständlich benannten StructureV3-Profile und aktivierten KI-Verbindungen wie der Quellenablauf. Nach der Profilauswahl ersetzt **Neu verarbeiten** den bisherigen Entwurf. Die Abschlussansicht führt zur **Verarbeitung**; anschließend wird das neue Ergebnis erneut geprüft und ausdrücklich freigegeben.

Auch ein Qualitätsblock erlaubt eine erneute Verarbeitung. Bereits freigegebene Stände bleiben unveränderlich. Confluence-Seitentext wird direkt importiert und lässt sich nicht durch ein OCR-Profil neu aufbereiten; unterstützte Anhänge können nach abgeschlossenem Import erneut verarbeitet werden.

`GET /api/v1/portal/documents/{id}` liefert dafür `profile_id` und `can_reprocess`. `POST /api/v1/portal/documents/{id}/reprocess` erwartet `profile_id` und den `markdown_sha256` der geprüften Vorschau. Der Server prüft Eigentümer-/Adminrechte, den aktuellen Stand und die Profilverfügbarkeit, bevor er Ausgaben entfernt und genau diesen Auftrag erneut einreiht. Eine zwischenzeitliche Änderung oder Freigabe führt zu HTTP 409. Leserechte allein erlauben keine erneute Verarbeitung über den Portal-Endpunkt. Der Neustart erstellt keine Freigabe.

## Wissensbereiche administrieren

**Administration → Wissensbereiche** ist die Startansicht für Admins. Sie enthält alle Bereiche, einschließlich leerer Bereiche und Bereiche anderer Eigentümer. Neben den Berechtigten werden Eigentümer, Dokumentzahl und Bearbeitungsstände angezeigt. Admins können Namen, Beschreibung und berechtigte Teams über den bestehenden Collection-Endpunkt ändern. Eine leere Auswahl eingeschränkter Teams kann nicht gespeichert werden; der Wechsel von ausgewählten Teams zu **Alle angemeldeten Teams** verlangt eine ausdrückliche Bestätigung. Bestehende Teamnamen werden beim Bearbeiten erhalten, auch wenn sie nicht mehr in der aktuellen Teamliste stehen.

`GET /api/v1/portal/admin/collections` ist ausschließlich für Administratoren erreichbar. Zähler werden in SQL aggregiert und liefern keine Dokumentinhalte. `review_count` zählt fertige, nicht leere und nicht passwortgeschützte Dokumente ohne Freigabe, deren Import abgeschlossen ist und deren Qualitätsprüfung nicht blockiert. `released_count` zählt gespeicherte Freigaben unabhängig vom Zustellstatus. `GET /api/v1/portal/activity` liefert die Verarbeitungsliste mit der bestehenden Auftrags-Sichtbarkeit.

## Chat-Modell administrieren

Unter **Administration → Chat & LLM** pflegen Administratoren den OpenAI-kompatiblen Endpoint, Modellnamen, optionalen API-Key, Timeout und eine optionale globale Temperatur. Der Key wird verschlüsselt gespeichert und in der Oberfläche nur als „vorhanden“ angezeigt. Nach dem Speichern prüft **Verbindung testen** den echten `/chat/completions`-Pfad mit einer kleinen Anfrage. Aktivierte Änderungen gelten ab dem nächsten direkten Chat-Turn für alle Runtime-Replikate; ein Neustart ist nicht nötig. n8n-Bots verwenden weiterhin das im jeweiligen n8n-Flow konfigurierte Modell.

## Einzelne E-Mails

E-Mails werden als `.eml`-Datei über **Quelle hinzufügen → Dateien hochladen** verarbeitet. Nachrichtentext und unterstützte Anhänge werden in einem gemeinsamen Markdown-Ergebnis aufbereitet; nicht unterstützte oder fehlgeschlagene Anhänge erscheinen als übersprungene Abschnitte. Das gesamte Ergebnis bleibt bis zur manuellen Freigabe außerhalb des regulären RAG-Imports.

Die separate Mail-API `/api/v1/mail/*` und ihre Inbox-Oberfläche sind entfernt. Alte `/mail`-Lesezeichen führen zur Quellenauswahl, alte Mail-Detail-Links zur Verarbeitung. Bestehende Mail-Daten und zugehörige Aufträge werden durch diese Änderung nicht gelöscht. Einzelheiten: [E-Mail-Dateien hochladen](integrations/mail-ingestion.md).
