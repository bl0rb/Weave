# Event: document.processed

**Zweck:** Weave-Ingest benachrichtigt seine Konsumenten (primär Weave-Knowledge, ggf. weitere Indexierungssysteme) über ein erfolgreich verarbeitetes Dokument. Der Event wird nach dem Abschluss aller Verarbeitungsschritte (OCR, Extraktion, Markdown-Rendering, Qualitätsprüfung) emittiert und ermöglicht Downstream-Systemen, die verarbeiteten Inhalte zu konsumieren und zu indexieren.

## Auslösebedingungen

`document.processed` wird von `app/workers/tasks.py`s Job-Abschluss-Hook **zusätzlich zu** (nicht anstelle von) `job.finished` ausgelöst -- als zweiter, unabhängiger `dispatch_job_event`-Aufruf direkt nach dem für `job.finished`, mit eigenem Try/Except (ein Fehler beim einen Event kann den anderen nie verhindern oder maskieren). Es gilt dieselbe Opt-in-Logik wie für jedes andere Webhook-Event (siehe `app/workers/webhook_tasks.py`):

- Nur wenn `WEBHOOKS_ENABLED` aktiv ist
- Nur für einen Job, der selbst eine `webhook_connection_id` trägt (`job.processing_info['settings']['webhook_connection_id']`, gesetzt bei `POST /upload` oder `POST /collections/{id}/start`) -- kein Fan-out an alle Verbindungen des Owners
- Nur wenn diese Verbindung aktiv (`enabled`) ist und `document.processed` in ihrer `events`-Liste führt

**Benchmark-Jobs** (`POST /benchmarks`, `app/api/benchmarks.py`) lösen `document.processed` **nie** aus -- nicht durch eine explizite Ausschlussregel, sondern strukturell: `create_benchmark` setzt für seine Variant-Jobs nie eine `webhook_connection_id`, weshalb der Opt-in oben schon fehlschlägt. Das ist exakt dieselbe Ausschlussmechanik, auf der `job.finished` für Benchmark-Jobs schon heute beruht.

Für einen fehlgeschlagenen Job (`job.failed`) wird `document.processed` nie ausgelöst.

## Webhook-Envelope

Der Event wird als HTTP POST mit folgendem Envelope versendet:

### Headers

```
POST https://consumer-endpoint/webhook HTTP/1.1
Content-Type: application/json
Accept: application/json
X-Weave-Ingest-Event: document.processed
X-Weave-Ingest-Signature: sha256=<hmac-hex>
```

- **X-Weave-Ingest-Event:** Der Event-Typ als String (`document.processed`)
- **X-Weave-Ingest-Signature:** HMAC-SHA256-Signatur über den rohen Request-Body (siehe [Signaturverfahren](#signaturverfahren))
- **Content-Type/Accept:** Standard JSON

### Request Body

Der HTTP-Body ist JSON mit folgender Struktur:

```json
{
  "event": "document.processed",
  "timestamp": "2026-08-31T14:23:45.123456+00:00",
  "job_id": "3f9c2e1a-6b7d-4e2a-9c1f-8a2b3c4d5e6f",
  "document_version": 1,
  "previous_job_id": null,
  "content_sha256": "abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234",
  "original_filename": "report.pdf",
  "markdown_url": "https://api.weave.local/api/v1/jobs/3f9c2e1a-6b7d-4e2a-9c1f-8a2b3c4d5e6f/download",
  "frontmatter": {
    "source": "3f9c2e1a-6b7d-4e2a-9c1f-8a2b3c4d5e6f.pdf",
    "original_filename": "report.pdf",
    "pages": 42,
    "profile": "PP-OCRv6 small det + rec",
    "profile_id": "ppocrv6_small",
    "mode": "single",
    "email": "uploader@example.com",
    "department": "Engineering",
    "job_id": "3f9c2e1a-6b7d-4e2a-9c1f-8a2b3c4d5e6f",
    "document_version": 1,
    "content_sha256": "abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234",
    "uploaded_by": "mathias",
    "team": "Kundenservice",
    "tags": ["section-1", "important"],
    "processed_at": "2026-08-31T14:23:45Z",
    "engine": "paddleocr"
  },
  "quality": {
    "grade": "A",
    "recommendation": "allow",
    "signals": {
      "ocr_confidence": 0.95,
      "confidence_sample_size": 340,
      "structure_quality": 0.91,
      "noise_penalty": 0.03,
      "text_quality": 0.97,
      "field_validation": {}
    }
  },
  "engine": "paddleocr",
  "processed_at": "2026-08-31T14:23:45Z"
}
```

`uploaded_by`/`team`/`previous_job_id`/`email`/`department`/`tags` sind in `frontmatter` nur vorhanden, wenn der zugrundeliegende Wert tatsächlich existiert (siehe `app/services/paddle_service.py`s `_build_rag_frontmatter`) -- anders als die Top-Level-Felder des Events selbst, die immer vorhanden und bei Fehlen explizit `null` sind.

## Payload-Felder

### `event` (string, erforderlich)
Der Event-Typ als Konstante: `"document.processed"`

### `timestamp` (string ISO-8601, erforderlich)
UTC-Zeitstempel des Event-Enqueuing (zur Unterscheidung mehrfach gelöschter Events). Format: `datetime.now(timezone.utc).isoformat()`, also `YYYY-MM-DDTHH:MM:SS.ffffff+00:00` (Offset-Form, kein literales `Z`) -- identisch zu `timestamp` bei `job.finished`/`job.failed`/`import_run.finished`.

### `job_id` (string UUID, erforderlich)
Die eindeutige Job-ID des verarbeiteten Dokuments. Primärer Schlüssel für Konsumenten-Deduplication (siehe [Idempotenz](#idempotenz)).

### `document_version` (integer, erforderlich)
Versionsnummer des Dokuments. Inkrementiert bei jeder Wiederverarbeitung desselben `job_id`-Datensatzes. Ermöglicht Konsumenten, zu erkennen, ob ein neueres Ergebnis für denselben Upload vorliegt.

### `previous_job_id` (string UUID | null, erforderlich)
Falls das Dokument eine Wiederverarbeitung eines älteren Jobs ist (z.B. mit anderen OCR-Profil-Parametern): die UUID des vorherigen Job. Für Initial-Uploads: `null`. Ermöglicht Konsumenten, Versionskettenkontext zu pflegen.

### `content_sha256` (string hex, erforderlich)
SHA256-Hash über den Original-Dateiinhalt (vor OCR/Verarbeitung). Unveränderlich für einen gegebenen Upload. Primärer Schlüssel zusammen mit `job_id` für Content-Deduplication (siehe [Idempotenz](#idempotenz)). Gleiche `content_sha256` bedeutet: gleicher Input (unabhängig vom `job_id` oder `document_version`).

### `original_filename` (string, erforderlich)
Der vom Benutzer hochgeladene Dateiname (z.B. `"Quarterly_Report_Q3_2026.pdf"`). Keine vollständige Pfad, nur der Filename.

### `markdown_url` (string URL, erforderlich)
**Gründung:** Markdown kann sehr groß sein (100+ KB pro Seite). Statt es inline zu versenden (Envelope aufgebläht, Netzwerkverkehr), wird eine API-Referenz versendet. Konsumenten können dann asynchron abrufen, retryen bei Netzwerkfehlern und Caching-Strategien anwenden.

Vollständig qualifizierte HTTPS-URL zur bestehenden Route `GET /api/v1/jobs/{job_id}/download` (`app/api/routes.py:download_markdown`), die den kompletten Job-Markdown-Text (inkl. YAML-Frontmatter) als Datei zurückgibt. Beispiel:
```
https://api.weave.local/api/v1/jobs/{job_id}/download
```

**Abweichung von einer früheren Fassung dieser Spezifikation:** Diese Route ist **keine** Pre-signed/öffentliche URL -- sie verlangt dieselbe Authentifizierung wie jeder andere Weave-Ingest-API-Aufruf (`get_current_user`: Bearer-Token oder Session-Cookie) und dieselbe Sichtbarkeitsprüfung wie jeder andere Job-Zugriff (Owner, Team-Mitgliedschaft oder Admin -- siehe `_require_visible`). Ein Konsument wie Weave-Knowledge braucht also einen gültigen Weave-Ingest-Zugang (z.B. ein persönliches API-Token eines dafür angelegten Service-Users, `Authorization: Bearer pd_...`) mit Sicht auf den jeweiligen Job, um `markdown_url` tatsächlich abrufen zu können -- ein anonymer/unauthentifizierter Abruf schlägt mit 401 fehl. Ein zukünftiger Vertrag könnte stattdessen eine echte Pre-signed-URL einführen; bis dahin ist Authentifizierung Pflicht.

Konsumenten sollten diese URL unverzüglich nach Event-Empfang abrufen, um sicherzustellen, dass der Job noch sichtbar und das Ergebnis aktuell ist.

### `frontmatter` (object, erforderlich)
Das tatsächlich aus `markdown_url`s Inhalt geparste YAML-Frontmatter-Objekt (der Block zwischen den beiden `---`-Zeilen am Dateianfang) -- byte-für-byte dasselbe, was ein Konsument sehen würde, der `markdown_url` selbst abruft und parst. Schema: **`contracts/frontmatter.schema.json`** (der bestehende, gegen die echten Konvertierungspfade getestete Vertrag, siehe `backend/tests/test_frontmatter_contract.py` -- `document.processed.schema.json` referenziert ihn per `$ref` statt ihn hier zu duplizieren).

Wichtigste Felder (siehe `contracts/frontmatter.schema.json` für die vollständige, verbindliche Liste):
- `source` (string): UUID-Dateiname des Job-Dokuments auf der Festplatte (kein UUID-*Format*-Constraint -- reine Namenskonvention)
- `original_filename` (string, optional): wie `original_filename` im Event-Top-Level
- `pages` (integer): Anzahl der Seiten im Ursprungsdokument
- `profile` (string): Menschenlesbarer Name des verwendeten OCR/VL-Profils (z.B. `"PP-OCRv6 small det + rec"`)
- `profile_id` (string): Profil-Kennung -- ein Slug wie `ppocrv6_small`/`openai_vision`/`vl:<connection_id>`, **keine UUID**
- `mode` (string, enum `single` | `collection` | `import`): Verarbeitungsmodus
- `email` (string, optional): E-Mail des Uploaders
- `department` (string, optional): Abteilung des Uploaders
- `job_id` (string): wie `job_id` im Event-Top-Level
- `document_version` (integer): wie `document_version` im Event-Top-Level
- `content_sha256` (string): wie `content_sha256` im Event-Top-Level
- `previous_job_id` (string, optional): wie `previous_job_id` im Event-Top-Level, aber **nur vorhanden wenn gesetzt** (kein `null`-Wert -- der Schlüssel fehlt bei Initial-Uploads ganz)
- `uploaded_by` (string, optional): **Benutzername** (nicht die User-ID/UUID) des Uploaders
- `team` (string, optional): **Teamname** (nicht die Team-ID/UUID) des Uploaders
- `tags` (array[string], optional): Job-Tags (z.B. hierarchische Breadcrumbs aus Confluence)
- `processed_at` (string ISO-8601, Format `YYYY-MM-DDTHH:MM:SSZ`): Verarbeitungsabschlusszeit (UTC) -- identisch mit dem Event-Top-Level-Feld `processed_at` (siehe unten)
- `engine` (string, enum `paddleocr` | `mail-eml` | `pypdf-fallback` | `spreadsheet-fallback` | `openai_vision`): Verarbeitungs-Engine -- identisch mit dem Event-Top-Level-Feld `engine`
- `used_fallback` (boolean, optional): nur vorhanden und `true`, falls eine Fallback-Engine verwendet wurde (nie `false`)

### `quality` (object, erforderlich)
Qualitäts-Assessment des verarbeiteten Dokuments -- `grade`/`recommendation`/`signals` sind der unveränderte Rückgabewert von `app/services/quality_gate.evaluate_document_quality()` (gespeichert unter `job.processing_info['execution']['quality_gate']`), nicht ein für dieses Event separat berechneter Wert:

```json
{
  "grade": "A",
  "recommendation": "allow",
  "signals": {
    "ocr_confidence": 0.95,
    "confidence_sample_size": 340,
    "structure_quality": 0.91,
    "noise_penalty": 0.03,
    "text_quality": 0.97,
    "field_validation": {}
  }
}
```

#### `quality.grade` (enum: A | B | C, oder null; erforderlich)
Qualitätsklasse:
- `A`: Exzellente Qualität, hohe Extraktion und Struktur-Präzision
- `B`: Gute Qualität, ausreichend für Indizierung
- `C`: Minderqualität, aber verwertbar mit Warnung
- `null`: Für den Job liegt kein Quality-Gate-Ergebnis vor (defensiver Randfall; in allen regulären Verarbeitungspfaden gesetzt). Konsumenten behandeln `null` wie `warn`.

#### `quality.recommendation` (enum: allow | warn | block, oder null; erforderlich)
Aktion für Downstream-Konsumenten:
- `allow`: Ohne Warnung indexieren/konsumieren
- `warn`: Indexieren, aber Benutzer warnen (mögliche OCR-Fehler)
- `block`: Nicht indexieren bis manuell überprüft
- `null`: Kein Quality-Gate-Ergebnis vorhanden — wie `warn` behandeln (indexieren, aber markieren)

#### `quality.signals` (object, erforderlich)
Rohsignale für die Qualitätsbewertung -- exakt `evaluate_document_quality()['signals']` (siehe `app/services/quality_gate.py` + `backend/tests/test_quality_gate.py`), additiv erweiterbar um weitere Engine-spezifische Metriken:
- `ocr_confidence` (number 0–1 | null): gewichtetes Vertrauen in die OCR-Texterkennung (0.5 × Mittelwert + 0.5 × Anteil hochsicherer Lesungen); `null` wenn die Engine überhaupt keine Konfidenzwerte liefert (z.B. `openai_vision`, `pypdf-fallback`)
- `confidence_sample_size` (integer ≥ 0): Anzahl der für `ocr_confidence` eingesammelten Einzel-Konfidenzwerte; `0` wenn die Engine keine liefert
- `structure_quality` (number 0–1): Struktur-Präzision aus Seitenabdeckung, Block-Reihenfolge, Tabellen- und Formel-Anteil (`0` ohne erkannte Seitenstruktur, z.B. bei Fallback-Engines)
- `noise_penalty` (number 0–1): Anteil an Wiederholung/Symbolen/Kauderwelsch im gerenderten Markdown-Text (höher = schlechter)
- `text_quality` (number 0–1): `1 - noise_penalty`
- `field_validation` (object): Zählungen je Feldvalidierungs-Kategorie aus `app/services/field_validation.py` (IBAN/Datum/ICD-Plausibilität, verwaiste Labels, ...); leeres Objekt wenn keine Feldvalidierung anschlug
- Weitere Engine-spezifische Signale möglich

**Abweichung von einer früheren Fassung dieser Spezifikation:** Ein früherer Entwurf beschrieb `text_extraction_confidence`/`layout_preservation`/`language_detection` als Signale -- diese Feldnamen existieren im echten Quality-Gate nicht und werden nie emittiert. Die obige Liste ist die tatsächliche Signal-Menge.

### `engine` (string, erforderlich)
Name der Verarbeitungs-Engine -- identisch mit dem Event-Top-Level-Feld und dem gleichnamigen Frontmatter-Feld (siehe `contracts/frontmatter.schema.json`): `paddleocr` | `mail-eml` | `pypdf-fallback` | `spreadsheet-fallback` | `openai_vision`. Ermöglicht Konsumenten und Betreibern, Verarbeitung nach Engine zu filtern/debuggen.

### `processed_at` (string ISO-8601, erforderlich)
UTC-Zeitstempel des Verarbeitungsabschlusses, identisch mit dem gleichnamigen Frontmatter-Feld. Format: `YYYY-MM-DDTHH:MM:SSZ` (Sekundengenauigkeit). Unterscheidet sich von `timestamp` (Enqueuing-Zeitpunkt des Events, Mikrosekunden-Offset-Format) dadurch, wofür er steht, nicht dadurch, dass er "genauer" wäre -- beide sind praktisch zeitgleich.

## Signaturverfahren

Falls die Webhook-Verbindung mit einem gemeinsamen Secret konfiguriert ist, wird der Header `X-Weave-Ingest-Signature` gesetzt:

```
X-Weave-Ingest-Signature: sha256=<hex>
```

**Berechnung:**
1. Request-Body als UTF-8-Bytes (das exakte JSON wie versendet, keine Normalisierung)
2. HMAC mit SHA256-Algorithmus, Secret als UTF-8-Bytes
3. Hex-Encoding des HMAC-Digest
4. Präfix `"sha256="` angehängt

**Python-Beispiel:**
```python
import hashlib
import hmac

secret = "your-webhook-secret"
body = b'{"event":"document.processed","timestamp":"2026-08-31T14:23:45Z",...}'

mac = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
header_value = f'sha256={mac}'
```

Konsumenten sollten den Header-Wert mit ihrer lokalen Berechnung vergleichen, um Authentizität zu verifizieren.

## Idempotenz

### Delivery-Garantie

Webhooks werden mit **at-least-once-Semantik** versendet:
- Typischerweise wird das Event genau einmal zugestellt
- Bei Transport-/Netzwerkfehlern wird bis zu 5 Versuche mit exponentiellem Backoff unternommen (30s, 60s, 120s, 240s)
- Transient-Fehler (5xx, Timeout, DNS-Fehler, Connection Reset) werden retryt; permanente Fehler (4xx) nicht
- Eine Consumer-Endantwort-Idempotenz ist dennoch erforderlich

### Konsumenten-Deduplication

Konsumenten sollten den Event als idempotent behandeln und ein System zur Deduplizierung implementieren:

**Deduplizierungs-Schlüssel:** `(job_id, content_sha256)`

- `job_id`: Eindeutige Identifikation eines Verarbeitungs-Auftrags
- `content_sha256`: Hash des Originalinhalts; mehrere `job_id` mit gleichem `content_sha256` (Wiederverarbeitung mit anderem Profil) sind unterschiedliche Events mit unterschiedlichem Output

**Implementierungsmuster:**
```python
dedup_key = (event['job_id'], event['content_sha256'])
if dedup_key in seen_keys:
    # Duplizierun Event -> idempotent ignorieren
    return 202
seen_keys.add(dedup_key)
# Verarbeite den Event weiter
```

Falls `previous_job_id` gesetzt ist, kann der Konsument auch ältere Versionen des gleichen Dokuments überschreiben/aktualisieren, wenn er das (z.B. in einer Suchindex) unterstützt.

## Versionierung des Kontrakts

Diese Spezifikation beschreibt **Vertrag Version 1** des `document.processed`-Events:

- **Aktuell (ab Phase 1):** v1 — basale Struktur mit job_id, frontmatter, quality, markdown_url
- **Zukünftige Versionen:** Bei inkompatiblen Änderungen wird eine neue URL-Version gepflegt (z.B. `/v2`), und neue Konsumenten können opt-in. Bestehende Konsumenten mit v1-Endpoints werden auf v1 bleiben, bis sie upgraden.

**Rückwärtskompatibilität:**
- Neue optionale Felder können in `signals` (innerhalb `quality`) oder als Top-Level-Felder hinzugefügt werden, ohne Konsumenten zu brechen (sie ignorieren unbekannte Felder)
- Struktur-Umgestaltungen oder Feld-Renames erfordern eine neue Vertrag-Version

**Discovery:**
Die `$id` im JSON-Schema (`https://weave.local/contracts/events/document.processed/v1`) ist die kanonische Referenz für die Vertrag-Version.

## Verhältnis zum bestehenden Webhook-Format

Das `document.processed`-Event folgt dem gleichen Envelope-Format wie die bestehenden `job.finished`- und `job.failed`-Events von Weave Ingest (ererbt aus PaddleDoc):
- Gleiche Header (`X-Weave-Ingest-Event`, `X-Weave-Ingest-Signature`)
- Gleiche HMAC-SHA256-Signaturmethode
- Gleiche Retry/Backoff-Logik (at-least-once, 5 Versuche, exponentielles Backoff)
- Gleiche Envelope-Struktur (top-level `event`, `timestamp`)

**Abweichungen:**
- PaddleDoc `job.finished` beinhaltet inline `markdown` (ggf. große Textblöcke). Das `document.processed`-Event nutzt stattdessen `markdown_url` als Referenz (siehe Begründung unter `markdown_url`)
- Der Payload unterscheidet sich (PaddleDoc's `job` + `error_message` vs. Weave-Ingest's `job_id`, `frontmatter`, `quality`)
- Weave-Ingest definiert `document_version`, `previous_job_id`, `quality` (nicht vorhanden in PaddleDoc)

Das Signaturformat, die Retry-Semantik und das Delivery-Modell sind identisch, um Konsumenten die Möglichkeit zu geben, generische Webhook-Handler zu schreiben, die beide Event-Typen verarbeiten können.

## Notizen: Abweichungen von der ursprünglichen Spezifikation

Diese Fassung wurde gegen die tatsächliche Implementierung (`app/services/webhooks.py:build_document_processed_payload`, `app/services/quality_gate.py`, `app/services/paddle_service.py`, `contracts/frontmatter.schema.json`) geprüft und an mehreren Stellen korrigiert:

1. **`engine`-Werte:** Der ursprüngliche Entwurf nannte `pp-structure-v3`/`fallback-pypdf`/`fallback-spreadsheet`. Die echten Werte, die Weave-Ingest je erzeugt, sind `paddleocr`, `mail-eml`, `pypdf-fallback`, `spreadsheet-fallback`, `openai_vision` (identisch mit `contracts/frontmatter.schema.json`'s `engine`-Enum; `openai_vision` ist dort als Enum-Wert geführt, obwohl der heutige Code-Pfad für alle Nicht-Fallback-Pipelines -- inkl. `openai_vision` und `paddlevl` -- tatsächlich `paddleocr` schreibt, siehe `backend/tests/test_frontmatter_contract.py`s Kommentar dazu).
2. **`markdown_url` verlangt Authentifizierung.** Der ursprüngliche Entwurf beschrieb sie als Pre-signed/öffentliche URL ohne zusätzliche Authentifizierung. Die reale Route `GET /api/v1/jobs/{job_id}/download` verlangt `get_current_user` (Bearer-Token oder Session) und dieselbe Sichtbarkeitsprüfung wie jeder andere Job-Zugriff. Ein Konsument braucht also einen echten Weave-Ingest-Zugang. Dies wurde im `markdown_url`-Abschnitt oben korrigiert, statt eine neue öffentliche Route zu bauen (out of scope für dieses Ticket) -- eine echte Pre-signed-URL bleibt ein möglicher Punkt für einen künftigen Vertrag v2.
3. **`quality.signals`-Feldnamen.** Der ursprüngliche Entwurf erfand `text_extraction_confidence`/`layout_preservation`/`language_detection` -- keines davon existiert im echten Quality-Gate. Die tatsächlichen Felder sind `ocr_confidence`, `confidence_sample_size`, `structure_quality`, `noise_penalty`, `text_quality`, `field_validation` (`app/services/quality_gate.py:evaluate_document_quality`). `quality.grade`/`quality.recommendation` waren dagegen von Anfang an korrekt (identisch mit dem Quality-Gate-Enum).
4. **`frontmatter.profile_id`/`frontmatter.uploaded_by`/`frontmatter.team` sind keine UUIDs.** Der ursprüngliche Entwurf verlangte `format: uuid`. In Wirklichkeit ist `profile_id` ein Slug (`ppocrv6_small`, `openai_vision`, `vl:<connection_id>`, ...), und `uploaded_by`/`team` sind der **Benutzername** bzw. **Teamname** (Klartext-Strings aus `app/workers/tasks.py`s `owner_username`/`team_name`), nicht die jeweilige UUID.
5. **`frontmatter.mode`-Enum war unvollständig.** `single`/`batch` war weder vollständig noch korrekt -- `batch` existiert nirgends im Code. Die echten, mit einer konfigurierten `webhook_connection_id` erreichbaren Werte sind `single` (`POST /upload`) und `collection` (`POST /collections/{id}/start`); `import`/`mail_attachment`/`import_attachment` (Confluence-Import, Mail-Anhänge) sind zwar im übrigen Code vorhanden, aber diese Pfade unterstützen aktuell keine `webhook_connection_id` und erscheinen deshalb nie in einem `document.processed`-Frontmatter. **`contracts/frontmatter.schema.json`s `mode`-Enum wurde entsprechend um `collection` ergänzt** (der bestehende Vertrag war hier bereits vor diesem Ticket lückenhaft).
6. **`document.processed.schema.json`s `frontmatter`-Feld dupliziert nicht länger eine eigene Objektdefinition.** Es referenziert per `$ref` jetzt direkt `contracts/frontmatter.schema.json` -- den bestehenden, gegen echte Konvertierungspfade getesteten Vertrag -- statt einer zweiten, potenziell abweichenden Kopie. Das behebt zugleich Punkte 1, 4 und 5 strukturell für die Zukunft: Frontmatter-Feldkorrekturen passieren nur noch an einer Stelle.
7. **`timestamp`-Format.** Der ursprüngliche Entwurf zeigte ein literales `Z`-Suffix (`...ffffffZ`). Der reale Code (`datetime.now(timezone.utc).isoformat()`, identisch zu `job.finished`/`job.failed`/`import_run.finished`) erzeugt eine Offset-Form (`...ffffff+00:00`). `processed_at` dagegen kommt aus dem Frontmatter und ist tatsächlich `Z`-terminiert (`strftime('%Y-%m-%dT%H:%M:%SZ')`) -- beide Formate sind gültiges RFC-3339/`date-time` und wurden im Text präzisiert statt vereinheitlicht.
8. **Auslösebedingungen fehlten komplett.** Der ursprüngliche Entwurf beschrieb nur die Payload, nicht wann/wofür das Event ausgelöst wird. Der neue Abschnitt „Auslösebedingungen" oben dokumentiert den Opt-in-Mechanismus (`webhook_connection_id` pro Job) und die daraus folgende Benchmark-Ausschlussregel.
