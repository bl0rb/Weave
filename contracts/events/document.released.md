# `document.released` v1

Weave-Ingest sendet dieses signierte Event, wenn für einen Job ein
unveränderlicher, freigegebener Markdown-Snapshot bereitsteht. Knowledge darf
ausschließlich dieses Event regulär indexieren.

## Payload

Das JSON entspricht `document.processed` einschließlich `event`, `job_id`,
`document_version`, `previous_job_id`, `content_sha256`, `original_filename`,
`frontmatter`, `quality`, `engine` und `processed_at` und enthält zusätzlich:

- `release_id`: UUID der Freigabe.
- `markdown_sha256`: kleingeschriebener hexadezimaler SHA-256 des UTF-8-Inhalts
  des freigegebenen Snapshots.
- `markdown_url`: `/api/v1/portal/releases/{release_id}/download`.

Der Snapshot enthält das serverseitig canonicalisierte Frontmatter und bleibt
nach der Freigabe byteweise unverändert. `frontmatter.collection` ist daher
die von Ingest canonicalisierte Collection.

## Abruf und Authentifizierung

Knowledge baut das Ziel lokal aus der vertrauenswürdigen Einstellung
`WEAVE_INGEST_BASE_URL` und der validierten `release_id`:

`GET /api/v1/portal/releases/{release_id}/download`

Der Abruf verwendet das bestehende `Authorization: Bearer <Token>`-Token,
folgt keinen Redirects und verwendet dieselbe Timeout-, Retry- und
Fehlerklassifizierung wie der bisherige Markdown-Abruf. Vor dem Chunking muss
der UTF-8-SHA-256 dem top-level `markdown_sha256` entsprechen.

## Signatur und Idempotenz

Der Request-Body wird vor jeder Event-Verarbeitung mit
`X-Weave-Ingest-Signature: sha256=<HMAC-SHA256>` und dem konfigurierten
Webhook-Secret geprüft. Fehlt das Secret, bleibt die Prüfung fail closed.

Der Deduplication-Key ist `release:<release_id>`. Eine erneute Zustellung darf
weder neu indexieren noch bestehende Daten überschreiben und wird mit
`200 {"status":"duplicate"}` beantwortet. In v1 darf ein `job_id` nur eine
Freigabe haben; eine andere Freigabe für denselben Job wird konservativ mit
HTTP 409 abgewiesen, auch bei gleichzeitiger Zustellung.

## `document.processed`

Ein signiertes `document.processed` wird mit
`200 {"status":"awaiting_release"}` quittiert. Dabei werden kein
`IngestEvent`, kein `Document` und kein Index-Task erzeugt. Unfreigegebenes
Material wird dadurch nicht veröffentlicht.
