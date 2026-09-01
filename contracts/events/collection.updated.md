# Event: collection.updated

**Zweck:** Weave-Ingest benachrichtigt seine Konsumenten (primär Weave-Knowledge) darüber, dass sich der Registry-Eintrag einer Collection geändert hat -- insbesondere `read_teams`, also die Lese-ACL. Der Event ist ausdrücklich **nur ein Anstoss, keine Datenquelle**: er transportiert eine schlanke Momentaufnahme, aber die tatsächliche Wahrheit bleibt jederzeit `GET /api/v1/collections/registry`. Ein Konsument reagiert auf dieses Event, indem er die Registry erneut abruft -- er verlässt sich nicht auf den Payload-Inhalt als aktuellen Stand.

Ohne dieses Event erreicht eine Rechteänderung (z.B. ein Team wird aus `read_teams` entfernt) Weave-Knowledge erst beim nächsten periodischen Sync-Poll gegen die Registry -- ein Zeitfenster, in dem ein Team faktisch noch Zugriff auf Dokumente hat, die ihm gerade entzogen wurden. `collection.updated` schliesst dieses Fenster, indem es den Sync sofort anstösst statt ihn nur periodisch laufen zu lassen.

## Auslösebedingungen

`collection.updated` wird von `app/api/routes.py` ausgelöst, nicht von einem Job-/Run-Abschluss-Hook wie jedes andere Event in diesem System:

- `POST /api/v1/collections` (Anlegen einer neuen Collection)
- `PATCH /api/v1/collections/{id}` (Änderung einer bestehenden Collection -- unabhängig davon, welches Feld geändert wurde; nicht nur bei `read_teams`)

Beide Routen rufen `app/workers/webhook_tasks.dispatch_collection_event(db, collection, 'collection.updated')` direkt nach dem erfolgreichen `db.commit()` auf, in einem eigenen Try/Except (ein Fehler beim Webhook-Dispatch darf ein erfolgreiches Anlegen/Ändern niemals verhindern oder maskieren).

**Abweichender Dispatch-Mechanismus:** Jedes andere Event in diesem System (`job.finished`, `job.failed`, `import_run.finished`, `document.processed`) ist **Opt-in pro Task** -- ein Job oder Import-Run trägt selbst die ID der einen Verbindung, die benachrichtigt werden soll, und es gibt nie einen Fan-out an mehrere Verbindungen. Für `collection.updated` gibt es kein Äquivalent: eine Collection ist keine Ressource, die ein einzelner Aufrufer mit einer Verbindung konfiguriert, sondern eine globale ACL-/Registry-Ressource (genau wie `GET /collections/registry` selbst nie nach Sichtbarkeit des Aufrufers gefiltert wird). Deshalb erhält **jede aktivierte (`enabled`) Verbindung, die `collection.updated` in ihrer `events`-Liste führt -- unabhängig vom Owner** -- eine eigene Zustellung. Eine Verbindung ohne Owner (Owner wurde gelöscht) wird übersprungen, da dafür kein Pending-Cap-Konto und keine über `GET /webhooks/deliveries` einsehbare Zustellung existieren könnte.

Betrifft nur `WEBHOOKS_ENABLED=true`; sonst ist `dispatch_collection_event` ein vollständiges No-op.

## Webhook-Envelope

### Headers

```
POST https://consumer-endpoint/webhook HTTP/1.1
Content-Type: application/json
Accept: application/json
X-Weave-Ingest-Event: collection.updated
X-Weave-Ingest-Signature: sha256=<hmac-hex>
```

Gleiche Header, gleiches Signaturverfahren wie jedes andere Event dieses Systems (siehe [Signaturverfahren](#signaturverfahren)).

### Request Body

```json
{
  "event": "collection.updated",
  "timestamp": "2026-08-31T14:23:45.123456+00:00",
  "slug": "kundenservice-2026",
  "name": "Kundenservice 2026",
  "description": "Eingehende Kundenanfragen, Q3/Q4",
  "read_teams": ["kundenservice", "qm"],
  "updated_at": "2026-08-31T14:23:44.987000+00:00"
}
```

## Payload-Felder

Bewusst schlank gehalten -- der Empfänger zieht sich ohnehin die vollständige Registry per `GET /api/v1/collections/registry`; dieser Event ist nur der Anstoss dafür, das jetzt (statt beim nächsten periodischen Poll) zu tun. Es gibt daher keine zusätzlichen Felder (kein `job_id`-Äquivalent, keine Job-/Dokumentdaten) -- exakt die vier Felder, die die Registry selbst pro Eintrag führt, plus `updated_at`.

### `event` (string, erforderlich)
Konstante: `"collection.updated"`

### `timestamp` (string ISO-8601, erforderlich)
UTC-Zeitstempel des Event-Enqueuing. Format: `datetime.now(timezone.utc).isoformat()` -- identisch zu jedem anderen Event dieses Systems.

### `slug` (string, erforderlich)
Die stabile, cross-service Identität der Collection (siehe `app/models/models.py`s `Collection`-Docstring) -- ändert sich nie, sobald Dokumente damit getaggt wurden. Das ist der Schlüssel, unter dem ein Konsument den passenden Eintrag in der Registry-Antwort wiederfindet.

### `name` (string, erforderlich)
Anzeigename der Collection.

### `description` (string | null, erforderlich)
Freitext-Beschreibung, oder `null` wenn keine gesetzt ist.

### `read_teams` (array[string], erforderlich)
Lese-ACL zum Zeitpunkt des Dispatch: Team-Slugs, die diese Collection lesen dürfen; leere Liste bedeutet "für jedes Team lesbar". **Nur ein Snapshot** -- da Zustellung at-least-once mit Retries erfolgt (siehe [Idempotenz](#idempotenz)), kann eine spät zugestellte Kopie bereits wieder veraltet sein. Konsumenten reagieren auf den Event, indem sie die Registry neu abrufen, nicht indem sie diesen Wert direkt persistieren.

### `updated_at` (string ISO-8601, erforderlich)
`collections.updated_at` der zugrundeliegenden Zeile (DB-`onupdate`-Zeitstempel) zum Zeitpunkt des Dispatch. Erlaubt einem Konsumenten, zwei Zustellungen derselben Collection zu ordnen, ersetzt aber nicht den erneuten Registry-Abruf.

## Signaturverfahren

Identisch zu jedem anderen Event dieses Systems (siehe `contracts/events/document.processed.md#signaturverfahren`): HMAC-SHA256 über den rohen Request-Body, hex-encodiert, als `X-Weave-Ingest-Signature: sha256=<hex>`, wenn die Verbindung mit einem Secret konfiguriert ist.

## Idempotenz

**Delivery-Garantie:** at-least-once, wie jedes andere Event (bis zu 5 Versuche, exponentielles Backoff 30s/60s/120s/240s -- siehe `app/workers/webhook_tasks.py`).

**Konsumenten-Deduplication:** Da der Payload selbst nie als Wahrheit behandelt wird, ist eine inhaltliche Dedup-Logik hier weniger kritisch als bei `document.processed` -- ein doppelt zugestelltes (oder aus zwei verschiedenen Änderungen stammendes) Event führt bestenfalls zu einem zusätzlichen, harmlosen Registry-Refetch. Ein Konsument **kann** trotzdem `(slug, updated_at)` als Dedup-Schlüssel nutzen, um redundante Refetches zu vermeiden, ist aber nicht darauf angewiesen, um korrekt zu bleiben -- die Registry ist immer aktuell, unabhängig davon, welche Zustellung den Refetch ausgelöst hat.

## Verhältnis zum bestehenden Webhook-Format

`collection.updated` läuft über exakt dieselbe Zustell-/Retry-/Signatur-Maschinerie wie `job.finished`/`job.failed`/`import_run.finished`/`document.processed` (`app/services/webhooks.py`, `app/workers/webhook_tasks.py`). Der einzige strukturelle Unterschied ist der Dispatch-Auslöser: ein Collection-Write in `app/api/routes.py` statt ein Job-/Run-Abschluss-Hook, und ein Fan-out an alle subscribten Verbindungen statt ein einzelnes Opt-in pro Task (siehe [Auslösebedingungen](#auslösebedingungen)).
