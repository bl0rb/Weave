# Event: collection.updated

**Zweck:** Weave-Ingest teilt Weave-Knowledge mit, dass sich die zentrale Collection-Registry geändert hat. Das Ereignis ist nur ein signierter Aktualisierungshinweis. Es enthält keine Berechtigungsmatrix und ist keine Datenquelle. Nach dem Empfang liest Weave-Knowledge die vollständige Registry über den admin-geschützten Endpunkt `GET /api/v1/collections/registry` neu ein.

Damit wirkt eine geänderte oder entzogene Leseberechtigung unmittelbar, ohne auf den nächsten periodischen Abgleich warten zu müssen. Der 60-Sekunden-Abgleich bleibt als Ausfallsicherung bestehen, falls die Benachrichtigung oder ihre Wiederholungen ausfallen.

## Dienstgrenze

Dieser Vertrag gehört ausschließlich zur internen Verbindung:

```text
Weave-Ingest -- signierter Hinweis --> Weave-Knowledge
Weave-Knowledge -- admin-authentifizierter Abruf --> Collection-Registry in Weave-Ingest
```

`collection.updated` ist **kein auswählbares Ereignis einer benutzerverwalteten Webhook-Verbindung** und wird nicht an n8n oder andere externe Ziele verteilt. Dadurch verlässt die systemweite `read_teams`-Matrix die interne Dienstgrenze nicht. n8n greift bei einem Chat-Turn mit einem kurzlebigen Delegations-Token über Weave-Tools auf die für den jeweiligen Menschen erlaubten Collections zu; die Gegenrichtung Ingest → n8n gehört nicht zu diesem Ablauf.

## Auslöser

Weave-Ingest stößt die Benachrichtigung nach einem erfolgreichen Commit an:

- `POST /api/v1/collections`
- `PATCH /api/v1/collections/{id}`

Ein Fehler beim Einreihen oder Zustellen darf das bereits erfolgreiche Anlegen beziehungsweise Ändern der Collection nicht zurückrollen. Die periodische Synchronisierung stellt die spätere Konvergenz sicher.

## Ziel und Authentifizierung

Ziel ist der bereits für Veröffentlichungen verwendete interne Endpunkt von Weave-Knowledge:

```http
POST /api/v1/events/ingest HTTP/1.1
Content-Type: application/json
Accept: application/json
X-Weave-Ingest-Signature: sha256=<hmac-hex>
```

Weave-Ingest baut die URL ausschließlich aus `PORTAL_KNOWLEDGE_BASE_URL` und dem festen Pfad `/api/v1/events/ingest`. Die HMAC-SHA256-Signatur wird mit `PORTAL_KNOWLEDGE_WEBHOOK_SECRET` über den rohen JSON-Body berechnet. Weave-Knowledge prüft sie gegen `WEAVE_INGEST_WEBHOOK_SECRET` in konstanter Zeit und antwortet bei fehlender Dienstkonfiguration mit `503`, niemals mit einem unsignierten Fallback.

## Request Body

```json
{
  "event": "collection.updated",
  "timestamp": "2026-09-03T12:34:56.123456+00:00",
  "slug": "kundenservice-2026"
}
```

| Feld | Typ | Bedeutung |
|---|---|---|
| `event` | string | Immer `collection.updated`. |
| `timestamp` | ISO-8601 string | Zeitpunkt, an dem Weave-Ingest den Hinweis erzeugt hat. |
| `slug` | string | Stabile Collection-ID für Diagnose und Korrelation. Weave-Knowledge verwendet sie nicht als Teilaktualisierung, sondern lädt immer die vollständige Registry. |

Absichtlich nicht enthalten sind `name`, `description`, `read_teams`, Dokumentdaten oder Zugangsdaten. Die fachliche Wahrheit stammt ausschließlich aus dem anschließenden Registry-Abruf.

## Verarbeitung in Weave-Knowledge

Nach erfolgreicher Signaturprüfung führt Weave-Knowledge einen vollständigen, idempotenten Registry-Abgleich aus:

1. `GET /api/v1/collections/registry` mit dem admin-berechtigten `WEAVE_INGEST_API_TOKEN` aufrufen.
2. Vorhandene Einträge aktualisieren und neue Einträge anlegen.
3. Lokal gespiegelte Einträge entfernen, die upstream nicht mehr vorhanden sind.
4. Erst nach einem vollständigen Erfolg committen.

Schlägt der Abruf fehl, antwortet der Event-Endpunkt trotzdem erfolgreich, damit Weave-Ingest nicht parallel zu seinem eigenen Retry-Mechanismus eine zweite unkontrollierte Wiederholungsschleife startet. Weave-Knowledge protokolliert den Fehler; der periodische Abgleich ist die letzte Ausfallsicherung.

## Wiederholung und Idempotenz

Weave-Ingest wiederholt Transportfehler und `5xx` mit demselben begrenzten Backoff wie die Dokumentveröffentlichung. `4xx` und das Erreichen von `PUBLICATION_MAX_ATTEMPTS` beenden die Versuche. Doppelte oder verspätete Hinweise sind ungefährlich, weil jeder Empfang denselben vollständigen Abgleich auslöst.

Die Benachrichtigung besitzt bewusst keinen benutzerlesbaren Delivery-Datensatz. Betriebsdiagnose erfolgt über Worker-Logs und den gespiegelten Zeitstempel `collections.synced_at` in Weave-Knowledge.

## Versionierung

Neue optionale Diagnosefelder wären additiv. Änderungen am Signaturverfahren, am Zielpfad oder an den Pflichtfeldern sind breaking und erfordern ein koordiniertes Deployment von Weave-Ingest und Weave-Knowledge.

## Änderungsprotokoll

- **v1, Sicherheitskorrektur (2026-09-03):** Die frühere Verteilung über alle benutzerverwalteten Webhooks wurde entfernt. Der Payload enthält keine ACL mehr; nur Weave-Knowledge erhält den signierten Hinweis und liest anschließend die admin-geschützte Registry neu ein.
