# document.withdrawn v1

Ingest sendet nach ausdruecklicher Bestaetigung des Quelleigentuemers:

```json
{"event":"document.withdrawn","job_id":"00000000-0000-0000-0000-000000000001"}
```

Transport: `POST /api/v1/events/ingest` bei Knowledge, mit demselben
`X-Weave-Ingest-Signature: sha256=<HMAC-SHA256>` wie `document.released`.
Fehlende oder falsche Signaturen erteilen niemals Loeschrechte.

Knowledge entfernt Document und zugehoerige Chunks transaktional und speichert
`withdrawn:<job_id>` im bestehenden IngestEvent-Ledger. Dieses Tombstone bleibt
erhalten, auch wenn der Inhalt noch gar nicht eingetroffen ist. Spaetere
Freigaben derselben Job-ID werden ohne Indexierung quittiert. Eine neue
Importversion mit neuer Job-ID benoetigt eine neue Freigabe.

Erfolg: HTTP 200 mit `{"status":"withdrawn","job_id":"..."}`. Wiederholte
Zustellung hat dasselbe Ergebnis. HTTP 204 bedeutet nicht unterstuetztes Event
und gilt beim Sender ausdruecklich nicht als erfolgreiche Entfernung.

Ingest speichert Auftraege in `knowledge_withdrawals`. Der Publication-Tick
stellt ausstehende Auftraege erneut zu, auch nach Broker-Ausfall oder Neustart.
Die Importhistorie, originale Uploads, Freigabe-Snapshots und bereits erzeugte
Chat-Antworten werden nicht geloescht. Im Laufdetail wird die Entfernung erst
nach erfolgreicher Zustellung als abgeschlossen angezeigt.