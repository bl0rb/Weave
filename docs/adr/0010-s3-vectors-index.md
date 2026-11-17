# ADR 0010: Amazon S3 Vectors als optionaler Vektorindex neben pgvector

**Status:** angenommen

**Datum:** 2026-11-17

## Kontext

Die Vektorsuche läuft heute vollständig in Postgres: Weave-Knowledge schreibt `chunks.embedding` (pgvector, HNSW-Index), Weave-Retrieval sucht per `<=>`-Operator in derselben Datenbank (ADR-0005, `contracts/chunk-store.md`). Das ist einfach und hält Berechtigungen, Volltext und Vektoren in einer Transaktion zusammen.

Für Betreiber auf AWS gibt es mit **Amazon S3 Vectors** einen verwalteten Vektorindex (Vector Buckets mit Indexen, `PutVectors`/`DeleteVectors`/`QueryVectors`, Metadatenfilter). Er soll den HNSW-Index in Postgres bei großen Beständen entlasten können, ohne dass Weave dafür auf AWS festgelegt wird oder die Sicherheitsgarantien der Suche aufweicht. Randbedingungen:

- Postgres ist und bleibt die maßgebliche Quelle für Chunks, Embeddings, Volltext, Metadaten und Berechtigungen. Die Rechteprüfung ist Teil der Abfrage, kein nachträglicher Filter (ADR-0005, ADR-0008).
- Wer kein AWS nutzt, darf davon nichts merken: kein neuer Pflichtwert, kein neuer Netzwerkzugriff, kein Import von boto3.
- S3 Vectors liefert bei selektiven Filtern unter Umständen weniger als `topK` Treffer; Grenzen wie das maximale `topK` haben sich bereits geändert.

## Entscheidung

### 1. Optional, Standard bleibt pgvector

Neue Einstellung `VECTOR_BACKEND` (`pgvector` | `s3vectors`) in Weave-Knowledge und Weave-Retrieval, Standard `pgvector`. Jeder andere Wert und `s3vectors` ohne `S3VECTORS_BUCKET` brechen den Start ab. Mit `pgvector` ist das Verhalten unverändert; boto3 wird nur im S3-Vectors-Modul und nur bei Bedarf importiert.

Verbindungswerte mit identischen Namen in beiden Diensten: `S3VECTORS_BUCKET`, `S3VECTORS_INDEX` (Standard `weave-chunks`), `S3VECTORS_REGION`, `S3VECTORS_ENDPOINT_URL`, `S3VECTORS_ACCESS_KEY_ID`, `S3VECTORS_SECRET_ACCESS_KEY`. Leere Schlüssel bedeuten die Standard-Credential-Chain von boto3 (IAM-Rolle, IRSA, Umgebung, Instance Profile); statische Schlüssel sind nur eine Ausweichmöglichkeit. Nur Retrieval: `S3VECTORS_OVERFETCH` (Standard 4, 1–20), `S3VECTORS_MAX_TOP_K` (Standard 100), `S3VECTORS_FALLBACK_PGVECTOR` (Standard `true`).

In `weave.yaml` sind die Verbindungswerte geteilt (beide Dienste müssen denselben Index meinen). Der Schalter ist dagegen **pro Dienst** ein eigener Schlüssel (`KNOWLEDGE_VECTOR_BACKEND`, `RETRIEVAL_VECTOR_BACKEND`), den Compose-Datei und Helm-Chart im Container zu `VECTOR_BACKEND` umbenennen, weil die Umstellung eine feste Reihenfolge hat (5).

### 2. Ein abgeleiteter Spiegel, Knowledge ist der einzige Schreiber

Der Index ist eine Kopie der Chunk-Embeddings, kein zweiter Datenbestand. Datensatz: `key = str(chunks.id)`, `data.float32 = chunks.embedding`, filterbare Metadaten `collection` (oder `"__none__"`), `team`, `document_id`, `embedding_model`, nicht filterbar `chunk_index`. Index: `float32`, `dimension = EMBEDDING_DIMENSION`, `cosine`. Details: `contracts/chunk-store.md`, Abschnitt „Optionaler Vektorindex“ (additiv, Vertrag bleibt v1).

Weave-Knowledge schreibt nach jedem erfolgreichen Index-/Reindex-Commit und löscht Vektoren gelöschter Chunks erst nach dem jeweiligen Commit (beim Rückzug asynchron per Celery-Task). Alles best effort: Ein S3-Fehler wird mit Dokument-ID geloggt und lässt nie eine Indizierung oder einen Rückzug scheitern. `python -m app.cli s3vectors init` legt den Index an bzw. prüft Dimension und Metrik, `sync [--prune]` repariert jede Abweichung, `status` zeigt den Stand.

### 3. Retrieval fragt ab und prüft jeden Treffer in Postgres nach

Weave-Retrieval übersetzt `allowed_teams`/`allowed_collections` und die Filter in einen S3-Metadatenfilter mit derselben Semantik wie `apply_filters()` und fragt `top_k × S3VECTORS_OVERFETCH` Kandidaten ab (höchstens `S3VECTORS_MAX_TOP_K`). Jede zurückgegebene Chunk-ID wird anschließend mit **genau derselben Abfrageform** wie im pgvector-Pfad gegen Postgres geprüft (Join `documents`, `apply_filters()`, `status = 'indexed'`, Modell-Match). Der Metadatenfilter verkleinert nur die Kandidatenmenge, er entscheidet nie allein über eine Berechtigung. Veraltete Vektoren fallen dabei heraus; fehlende kosten nur Recall bis zum nächsten `sync`. Bei leerem Berechtigungsbereich wird S3 gar nicht gefragt.

### 4. Fehlerverhalten

Ein S3-Fehler (Ausnahme, Timeout; festes Zeitbudget ca. 3 s ohne Retries, danach 30 s Pause für S3) führt mit `S3VECTORS_FALLBACK_PGVECTOR=true` zur normalen pgvector-Suche, sonst zu einem leeren Vektor-Bein; die Volltextsuche antwortet in beiden Fällen. `trace.vector_backend` zeigt `pgvector`, `s3vectors`, `pgvector-fallback` oder `s3vectors-error`.

### 5. Reihenfolge beim Umstellen

Ein leerer oder halb gefüllter Index ist für `QueryVectors` kein Fehler; der Fallback greift dann nicht, die Vektortreffer fehlen still. Deshalb gilt verbindlich: erst Weave-Knowledge (API und Worker) auf `s3vectors`, `init` und `sync` bis Exit-Code 0, erst danach Weave-Retrieval. Zurück in umgekehrter Reihenfolge. `scripts/weave_config.py check` meldet Retrieval auf `s3vectors` bei Knowledge auf `pgvector`.

### 6. Modell- oder Dimensionswechsel

Ein S3-Vectors-Index hat eine feste Dimension und Metrik. Ein Wechsel von `EMBEDDING_MODEL`/`EMBEDDING_DIMENSION` bedeutet einen neuen `S3VECTORS_INDEX` plus `init` und `sync`; ein bestehender Index wird nie umdimensioniert.

## Konsequenzen

**Positiv**

- Betreiber auf AWS können die Vektorsuche in einen verwalteten Dienst auslagern, ohne dass sich an Datenmodell, Berechtigungen oder Volltextsuche etwas ändert.
- Die Rechtegarantie bleibt dieselbe wie mit pgvector: Die Postgres-Nachprüfung ist Pflicht, S3-Metadaten sind nur eine Vorauswahl.
- Ein Ausfall von S3 Vectors macht die Suche höchstens langsamer (Fallback) oder schlechter (nur Volltext), nie falsch; Indizierung und Rückzug laufen weiter.
- Ohne die Einstellung ändert sich nichts: kein Pflichtwert, kein Netzwerkzugriff, kein boto3-Import.

**Negativ**

- Zwei Vektorindexe können auseinanderlaufen (verpasste Schreib- oder Löschvorgänge). Das kostet Recall, aber keine Korrektheit; `sync --prune` muss regelmäßig laufen (z. B. als Cronjob).
- Pro Suche kommen ein Netzwerk-Roundtrip zu AWS und die Nachprüfungsabfrage in Postgres hinzu; Latenz und Kosten (Anfragen, gespeicherte Vektoren) hängen vom AWS-Tarif ab. Embeddings bleiben zusätzlich in Postgres gespeichert.
- Over-Fetch und Nachprüfung können bei sehr selektiven Filtern weniger als `top_k` Vektortreffer liefern; `S3VECTORS_MAX_TOP_K` begrenzt den Over-Fetch.
- Die Embeddings verlassen die eigene Datenbank und liegen bei AWS; das ist eine Datenschutzentscheidung des Betreibers.
- Eine Umstellung ist ein Betriebsschritt in fester Reihenfolge (5), kein reiner Env-Var-Flip.
- Compose-Stack und Helm-Chart geben beiden Diensten dieselbe Identität (gemeinsame Schlüssel bzw. ein ServiceAccount). Retrieval bekommt damit auch die Schreibrechte von Knowledge, solange niemand getrennte Identitäten einrichtet.

## Alternativen

1. **pgvector vollständig durch S3 Vectors ersetzen:** Embeddings nur noch bei AWS, keine Doppelhaltung. Verworfen: macht Weave von AWS abhängig, nimmt Selbstbetreibern ohne AWS die Vektorsuche, und Berechtigungen, Volltext und Vektoren lägen nicht mehr in einer Transaktion. Der SQLite-Pfad für Entwicklung und Tests ginge verloren.
2. **S3-Metadaten für Berechtigungen vertrauen, ohne Nachprüfung in Postgres:** spart eine Abfrage pro Suche. Verworfen: Der Index ist best effort und kann veralten (ersetzte oder zurückgezogene Dokumente, geänderte Wissensbereiche, Freigaben). Eine Rechteentscheidung auf Basis einer möglicherweise veralteten Kopie widerspricht ADR-0005/ADR-0008.
3. **Dual-Write im Request-Pfad (synchron in derselben Transaktion bzw. im Webhook):** Index und Postgres wären enger synchron. Verworfen: Ein AWS-Ausfall oder -Timeout würde Indizierung und Rückzüge blockieren oder scheitern lassen, und ein verteilter Commit über Postgres und S3 ist ohnehin nicht atomar. Schreiben nach dem Commit, best effort, plus `sync` ist robuster.
4. **Ein gemeinsamer `VECTOR_BACKEND`-Schalter für beide Dienste:** einfacher in `weave.yaml`. Verworfen: Retrieval würde gleichzeitig mit Knowledge umgestellt und fände einen leeren Index vor (5).
