# `document.review_due` v1

Weave-Ingest erinnert mit diesem Webhook-Event die Wissensverantwortlichen
eines Wissensbereichs daran, dass die fachliche Prüfung eines freigegebenen
Dokuments fällig ist (z. B. n8n → E-Mail oder Teams). Einen eigenen
Mailversand hat Weave-Ingest nicht.

## Auslöser

Der Tick `document_review_tick` (`services/ingest/backend/app/workers/freshness_tasks.py`,
Logik in `app/services/document_freshness.py`, Takt
`DOCUMENT_REVIEW_TICK_SECONDS`, Standard 3600 s) betrachtet jede *aktuelle
freigegebene Version* (freigegeben, nicht zurückgezogen, keine neuere
freigegebene Version in derselben Versionskette), deren `review_due_at`
höchstens `DOCUMENT_REVIEW_LEAD_DAYS` (Standard 7) Tage in der Zukunft liegt
oder bereits überschritten ist. Für jede solche Version gibt es genau eine
aktive Prüfaufgabe der Art `review_due` (`cluster_key = review_due:<job_id>`).
Das Event wird **einmal pro neu angelegter Aufgabe** versendet, nicht bei
jedem Tick. Eine von Hand geschlossene Aufgabe wird für dasselbe Prüfdatum
nicht erneut angelegt; ein neues Prüfdatum erzeugt eine neue Aufgabe und damit
ein neues Event.

`review_due_at` entsteht bei der Freigabe (Frontmatter-Feld `review_due`, siehe
`contracts/frontmatter.schema.json`, sonst Freigabezeitpunkt +
`review_interval_days` des Wissensbereichs) oder über die Portal-Endpunkte
`PUT /api/v1/portal/documents/{job_id}/review` und
`POST /api/v1/portal/documents/{job_id}/review/confirm`.

## Empfänger

Anders als `job.finished`/`document.processed` hängt das Event an keinem
einzelnen Verarbeitungsauftrag. Es geht an **jede aktivierte
Webhook-Verbindung, die `document.review_due` abonniert hat** und deren
Eigentümer aktiv und Eigentümer oder Mitglied (Rolle ≥ member) des
Wissensbereichs ist (Admins eingeschlossen). Leser erhalten es nie. Die
bestehende Obergrenze offener Zustellungen pro Nutzer gilt; der Zugriff des
Eigentümers wird beim Versand erneut geprüft. Zustellung, Signatur, Wiederholung
und Protokoll (`webhook_deliveries`) sind dieselben wie bei allen anderen
Events: `POST` mit `Content-Type: application/json`,
`X-Weave-Ingest-Event: document.review_due` und, wenn ein Secret hinterlegt
ist, `X-Weave-Ingest-Signature: sha256=<HMAC-SHA256 über den rohen Body>`.

## Payload

```json
{
  "event": "document.review_due",
  "timestamp": "2026-11-17T08:00:00+00:00",
  "job_id": "00000000-0000-0000-0000-000000000001",
  "document_version": 3,
  "original_filename": "reiserichtlinie.pdf",
  "collection": {"id": "…", "slug": "reiseregeln", "name": "Reiseregeln"},
  "review_due_at": "2026-11-20T00:00:00+00:00",
  "overdue": false,
  "last_reviewed_at": "2025-11-20T09:12:00+00:00",
  "portal_path": "/reviews/00000000-0000-0000-0000-000000000001"
}
```

| Feld | Typ | Bedeutung |
|---|---|---|
| `event` | string | immer `document.review_due` |
| `timestamp` | string (ISO 8601) | Zeitpunkt des Versands |
| `job_id` | string | Version (Job), deren Prüfung fällig ist |
| `document_version` | integer | Versionsnummer |
| `original_filename` | string | Dateiname des Dokuments |
| `collection` | object \| null | Wissensbereich (`id`, `slug`, `name`) |
| `review_due_at` | string \| null | Prüfdatum (ISO 8601, UTC); Stand beim Versand |
| `overdue` | boolean | Prüfdatum bereits überschritten |
| `last_reviewed_at` | string \| null | letzte Prüfung bzw. Freigabe |
| `portal_path` | string | relativer Pfad der Dokumentansicht im Ingest-Portal |

Das Event enthält weder Markdown noch personenbezogene Daten. Wiederholte
Zustellungen tragen den jeweils aktuellen Stand (`review_due_at`, `overdue`).
Additive Felder brechen nichts; Konsumenten ignorieren unbekannte Felder.
