# Indexierungsstatus einer Freigabe · Version 1

Knowledge bestätigt den gespeicherten Index. Ingest autorisiert den Portalnutzer und fragt ausschließlich dessen sichtbare Freigaben ab. Der bisherige Zustellstatus `DocumentRelease.status` bleibt unabhängig davon: `sent` bedeutet weiterhin nur, dass der Webhook bestätigt wurde.

## Interne Abfrage: Ingest → Knowledge

`POST /api/v1/indexing/status` erwartet ein JSON-Objekt mit `items` (1 bis 50 Einträge). Jeder Eintrag enthält:

| Feld | Typ | Bedeutung |
|---|---|---|
| `job_id` | UUID | Quellauftrag aus Ingest |
| `release_id` | UUID | Unveränderliche Freigabe dieses Auftrags |
| `markdown_sha256` | 64 Hex-Zeichen, klein | SHA-256 des freigegebenen Markdown-Snapshots, nicht der Originaldatei |

Die Antwort enthält dieselben Referenzen in derselben Reihenfolge, jeweils ergänzt um `state`, `indexed_at` (UTC oder `null`) und `chunk_count` (Integer). Inhalte, Vektoren, fremde Freigabe-IDs und interne Fehlermeldungen werden nie geliefert.

| `state` | Bedeutung im Portal |
|---|---|
| `not_received` | Noch kein Indexeintrag für diesen Auftrag |
| `mismatch` | Gespeicherte Freigabe-ID oder Snapshot-Hash weicht ab; keine Bestätigung dieser Freigabe |
| `pending` | Auftrag wartet auf Indexierung, wird bearbeitet oder erneut versucht |
| `indexed` | Genau dieser Snapshot ist aktiv indexiert; positiver Zähler stimmt mit gespeicherten Chunks und deren Embeddings überein, Abschlusszeit ist gesetzt |
| `empty` | Verarbeitung abgeschlossen, aber keine durchsuchbaren Textabschnitte entstanden |
| `incomplete` | Als indexiert markiert, aber Abschlusszeit, Chunk-Zähler oder Embeddings sind unvollständig |
| `failed` | Indexierung fehlgeschlagen |
| `blocked` | Indexierung durch Qualitätsprüfung blockiert |
| `superseded` | Durch eine neuere Version ersetzt, nicht mehr aktiv durchsuchbar |

Nur `indexed` liefert Abschlusszeit und eine positive Anzahl; alle anderen Zustände liefern `indexed_at: null` und `chunk_count: 0`. Knowledge prüft Referenz, Dokumentstatus und Chunk-Zähler in einem SQL-Snapshot. Ein älterer, ersetzter oder leerer Stand erhält keine positive Bestätigung.

### Authentifizierung

Die Abfrage verwendet den vorhandenen gemeinsamen Webhook-Schlüssel (`PORTAL_KNOWLEDGE_WEBHOOK_SECRET` in Ingest, `WEAVE_INGEST_WEBHOOK_SECRET` in Knowledge), mit eigenem Signaturkontext. Der allgemeine Knowledge-Lesetoken, der Zugriff auf den gesamten Korpus erlaubt, wird hierfür nicht benötigt.

- `X-Weave-Status-Timestamp`: Unix-Zeit in ganzen Sekunden, maximale Abweichung 60 Sekunden.
- `X-Weave-Status-Signature`: `sha256=<hex>`.
- Signierte Bytes: `b"weave.indexing-status.v1\n" + timestamp_ascii + b"\n" + raw_json_body`.
- HMAC-SHA256 mit UTF-8-kodiertem Shared Secret; konstanter Signaturvergleich.
- Fehlender Schlüssel: HTTP 503. Fehlende/ungültige/abgelaufene Signatur: HTTP 401. Zu großer Body: HTTP 413 (32 KiB). Ungültige Felder oder Batchgröße: HTTP 422.

Der eigene Kontext verhindert die Wiederverwendung einer Ereignissignatur als Statusabfrage. Die Route verändert keine Daten. Die Uhren der Dienste müssen synchron sein. Ingest verwendet ausschließlich die konfigurierte Basisadresse, folgt keinen Redirects und übernimmt keine Proxy-Umgebungsvariablen. Es gibt einen HTTP-Timeout von drei Sekunden und keinen unmittelbaren Retry.

## Portal: Browser → Ingest

`GET /api/v1/portal/indexing-status?job_id=<uuid>&job_id=<uuid>` akzeptiert 1 bis 50 IDs und benötigt die bestehende Anmeldung. Die Berechtigung entspricht den Dokument- und Verarbeitungsansichten (Eigentümer/eigenes Team oder Admin), wird bei jeder Abfrage neu geprüft und schließt Benchmark- sowie passwortgeschützte Aufträge aus. Fremde und unbekannte IDs fehlen gleichermaßen in `items`.

Ingest liest Freigabe-ID und Snapshot-Hash selbst aus seiner Datenbank. Der Browser kann weder diese Werte noch Zieladresse oder Signatur setzen. Unveröffentlichte Dokumente lösen keine Knowledge-Anfrage aus. Die Antwort hat `items: [{job_id, release, indexing}]`; `release` ist der aktuelle Zustellstatus, `indexing` enthält nur Status, Abschlusszeit und Anzahl. Fehlende Konfiguration, Timeout, HTTP-Fehler oder eine ungültige bzw. nicht passende Bestätigung werden als `indexing.state: unavailable` angezeigt. Das ist keine erfolgreiche und auch keine fehlgeschlagene Indexierung.

Beide Antworten verwenden `Cache-Control: no-store`. Der Browser fragt einen Batch je sichtbarer Dokumentliste alle fünf Sekunden während ausstehender Indexierung oder Nichterreichbarkeit ab, bei stabilen Zuständen alle 30 Sekunden. Hintergrund-Tabs pausieren und prüfen beim Wiederöffnen neu. Ein fehlgeschlagener Abruf oder entzogene Berechtigungen entfernt eine vorherige grüne Bestätigung. Die Dokumentvorschau und Freigabe-Eingaben werden dafür nicht neu geladen.

## Betrieb und Aussagegrenze

Ingest-Backend, Knowledge-API und Portal-Frontend gemeinsam aktualisieren; keine Migration, neue Geheimnisse oder Worker-Neustarts erforderlich. Ein alter Knowledge-Server ohne diese Route liefert im Portal „Indexstatus nicht verfügbar“. Bestehende Veröffentlichungen lassen sich ohne erneute Freigabe bestätigen.

„Für KI verfügbar“ bestätigt den aktiven Index für die konkrete Freigabe. Die tatsächlich abgefragten Collections und das Ergebnis eines Chats hängen weiterhin von Berechtigungen, Bot-/n8n-Konfiguration und der Suchanfrage ab. Es ist keine Zusage, dass jeder Bot jeden indexierten Inhalt verwendet.
