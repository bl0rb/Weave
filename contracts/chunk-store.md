# Vertrag: Chunk-Store (`documents` / `chunks`)

**Vertrag-Version:** 1
**Status:** angenommen
**Schreiber (Owner):** Weave-Knowledge — einzige Instanz, die `INSERT`/`UPDATE`/`DELETE` auf diesen Tabellen ausfuehrt (`app/workers/tasks.py`, `app/api/events.py`), und die einzige, die Alembic-Migrationen dagegen laufen laesst (`backend/alembic/`).
**Leser:** Weave-Retrieval — read-only, ueber eine eigene DB-Rolle mit reinen `SELECT`-Grants (siehe unten). Kein Alembic, kein Schreibzugriff, keine Schema-Aenderung.

## Architektur-Entscheidung

`documents`/`chunks` in der `weave_knowledge`-Datenbank sind ein **geteiltes Read-Model**: Weave-Knowledge bleibt der einzige Schreiber (ADR-0004s "Ein Postgres-Cluster, aber eigene Datenbank pro Service" wird hier bewusst durchbrochen — es gibt genau EINE Ausnahme von "keine geteilten Tabellen", dokumentiert genau hier), Weave-Retrieval liest direkt und synchron auf derselben Datenbank.

**Begruendung fuer die Ausnahme:** pgvector-KNN (`ORDER BY embedding <=> :query_vector`) und tsvector-Ranking (`ts_rank(tsv, query)`) muessen als SQL-Operationen in der Datenbank selbst laufen, nicht auf Anwendungsebene nachgebaut werden — ein HTTP-Proxy-Endpoint bei Weave-Knowledge, der pro Suchanfrage alle Kandidaten-Chunks seriell durchreicht, waere sowohl fuer Latenz als auch fuer die eigentliche Vektor-/Volltext-Suchlogik der falsche Schnitt. Der geteilte Lesezugriff ist die pragmatische Alternative zu "jeder Service dupliziert die Indexe in seiner eigenen Datenbank" (Sync-Aufwand, Konsistenzrisiko) oder "Suche laeuft synchron in Weave-Knowledge" (verletzt dessen eigene Nicht-Ziele, siehe dessen README).

**Konsequenz fuer Weave-Retrieval:**
- Besitzt **kein eigenes Alembic** und legt **niemals** Schema auf dieser Datenbank an (auch nicht additiv) — jede Schema-Aenderung ist ausschliesslich Weave-Knowledges Aufgabe.
- Verbindet sich in Produktion ueber eine **eigene, rein lesende DB-Rolle** (siehe "DB-Rolle" unten), nie ueber Weave-Knowledges Schreib-Credentials.
- Bildet dieselbe Tabellenform in `app/models/models.py` als **schlanke SQLAlchemy-Read-Models** nach (keine Alembic-Migration, keine Schreiblogik, kein `IngestEvent`) — dieses Dokument ist die verbindliche Quelle fuer deren Spalten; bei Abweichung gewinnt die tatsaechliche Definition in Weave-Knowledges `app/models/models.py` + `alembic/versions/0001_init.py`.
- In Tests/lokaler Entwicklung legt Weave-Retrieval seine eigenen Kopien dieser beiden Tabellen in einer **eigenen** SQLite-Datei an (`Base.metadata.create_all` ueber die kopierten Modelle) — das ist keine Ausnahme von "Retrieval legt nie Schema an", sondern eine isolierte Testdatenbank, die niemals gegen die echte `weave_knowledge`-Datenbank läuft.

## Tabelle `documents`

Extrahiert aus Weave-Knowledges `backend/app/models/models.py` (`class Document`) + `backend/alembic/versions/0001_init.py`.

| Spalte | Typ | Nullable | Default | Bedeutung |
|---|---|---|---|---|
| `id` | `uuid` (PK) | nein | `uuid4()` | Primaerschluessel dieser Tabelle. |
| `source_job_id` | `varchar(36)`, unique | nein | — | Weave-Ingests `Job.id` (UUID-String) fuer den zugrundeliegenden `document.processed`-Event. |
| `content_sha256` | `varchar(64)`, indexiert | nein | — | SHA256 des Original-Dateiinhalts; Dedup-Schluessel zusammen mit `source_job_id`. |
| `document_version` | `integer` | nein | `1` | Inkrementiert bei Wiederverarbeitung desselben Inhalts. |
| `previous_job_id` | `varchar(36)`, indexiert | ja | `NULL` | Job-ID der Vorgaengerversion, falls Re-Processing. |
| `original_filename` | `varchar(255)` | ja | `NULL` | Vom Nutzer hochgeladener Dateiname. |
| `markdown_url` | `varchar(2048)` | ja | `NULL` | Weave-Ingest-Download-URL, aus der `markdown_body` befuellt wurde. Fuer Retrieval ohne Bedeutung (interne Ingest-Pipeline-Referenz). |
| `engine` | `varchar(64)` | nein | — | Verarbeitungs-Engine, z.B. `paddleocr`, `mail-eml`, `pypdf-fallback`, `spreadsheet-fallback`, `openai_vision`. |
| `quality_grade` | `varchar(8)` | ja | `NULL` | `A` \| `B` \| `C` \| `NULL` (wie `warn` behandeln). |
| `quality_recommendation` | `varchar(16)` | ja | `NULL` | `allow` \| `warn` \| `block` \| `NULL`. |
| `frontmatter` | `json` | nein | `{}` | Volles geparstes YAML-Frontmatter-Objekt, siehe Weave-Ingests `contracts/frontmatter.schema.json`. |
| `markdown_body` | `text` | ja | `NULL` | Vollstaendiger Markdown-Text nach Fetch; `NULL` solange `status='pending'`. |
| `team` | `varchar(255)`, indexiert | ja | `NULL` | Aus `frontmatter.team` denormalisiert — Zugriffskontroll-Filter. |
| `department` | `varchar(255)`, indexiert | ja | `NULL` | Aus `frontmatter.department` denormalisiert — Zugriffskontroll-Filter. |
| `tags` | `json` (`list[str]`) | nein | `[]` | Aus `frontmatter.tags` denormalisiert. |
| `collection_slug` | `varchar(255)`, indiziert | ja | `NULL` | Aus `frontmatter.collection` denormalisiert (siehe Abschnitt "Collections" unten). **Kein FK** auf `collections.slug` — die Registry ist ein synchronisierter Spiegel und darf einem Dokument hinterherhinken. `NULL` heisst "kein Collection-Scope" (Altbestand/unscoped), nicht "unbekannte Collection". |
| `processed_at` | `timestamptz` | nein | — | Verarbeitungsabschlusszeit laut Event. |
| `indexed_at` | `timestamptz` | ja | `NULL` | Zeitpunkt des letzten erfolgreichen Index-Laufs. |
| `chunk_count` | `integer` | nein | `0` | Anzahl zugehoeriger `chunks`-Zeilen nach letztem Index-Lauf. |
| `embedding_model` | `varchar(255)` | ja | `NULL` | Modell-ID, mit der die Chunks zuletzt embedded wurden — **muss mit Weave-Retrievals eigenem `EMBEDDING_MODEL` uebereinstimmen**, sonst ist ein Vektor-Vergleich bedeutungslos. |
| `index_attempts` | `integer` | nein | `0` | Interner Retry-Zaehler von Weave-Knowledges Index-Worker. Fuer Retrieval ohne Bedeutung. |
| `status` | `varchar` (CHECK-Enum: `pending`\|`indexed`\|`blocked`\|`superseded`\|`failed`) | nein | `pending` | Nur `indexed` (oder ggf. `superseded`, je nach Suchsemantik) sollte je durchsuchbar sein — Filterentscheidung liegt bei der Suchpipeline. |
| `error` | `text` | ja | `NULL` | Letzte Fehlermeldung des Index-Laufs. |
| `created_at` | `timestamptz` | nein | `now()` | |
| `updated_at` | `timestamptz` | nein | `now()`, `onupdate=now()` | |

## Tabelle `chunks`

Extrahiert aus Weave-Knowledges `backend/app/models/models.py` (`class Chunk`) + `backend/alembic/versions/0001_init.py`. Dies ist die eigentliche Sucheinheit — ein `SearchResult` (siehe Weave-Retrievals `backend/app/schemas/search.py`) entspricht im Kern einer Zeile hier plus ein paar aus `documents` mitgefuehrten Feldern.

| Spalte | Typ | Nullable | Default | Bedeutung |
|---|---|---|---|---|
| `id` | `integer` (PK, autoincrement) | nein | — | Kein UUID: Chunks werden nie service-uebergreifend per ID referenziert, nur ueber ihr Dokument. |
| `document_id` | `uuid` (FK `documents.id`, `ON DELETE CASCADE`), indexiert | nein | — | |
| `chunk_index` | `integer` | nein | — | Reihenfolge innerhalb des Dokuments; `(document_id, chunk_index)` ist unique (`uq_chunks_document_id_chunk_index`). |
| `text` | `text` | nein | — | Der eigentliche Chunk-Inhalt — Basis fuer `to_tsvector`/`tsv` (siehe unten) und fuer `SearchResult.text`. |
| `heading_path` | `json` (`list[str]`) | nein | `[]` | ATX-Ueberschriften-Breadcrumb, z.B. `["Setup", "Installation"]`. |
| `page_start` | `integer` | ja | `NULL` | |
| `page_end` | `integer` | ja | `NULL` | |
| `char_count` | `integer` | nein | — | |
| `meta` | `json` | nein | `{}` | Von `documents` denormalisierte Filter-Metadaten (u.a. `team`/`department`/`collection`) — Weave-Retrievals Zugriffskontroll-Filter liest **dieses** Feld direkt, ohne Join gegen `documents`. `meta.collection` ist derselbe Slug wie `documents.collection_slug`, fehlt (kein Key) wenn das Dokument keiner Collection zugeordnet ist. |
| `embedding` | `vector(N)` auf Postgres (pgvector-Extension), `json` auf SQLite | ja | `NULL` | `N` = `EMBEDDING_DIMENSION` (aktuell `1536`, siehe Konfiguration beider Services). `NULL` bis zum ersten erfolgreichen Index-Lauf fuer diesen Chunk. |
| `embedding_model` | `varchar(255)` | ja | `NULL` | Modell-ID fuer **diesen** Chunk (kann waehrend eines Modellwechsels kurzzeitig von `documents.embedding_model` abweichen). |
| `tsv` | `tsvector`, `GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED` — **nur auf Postgres** | — | — | Kein Wert, den eine Anwendung setzt; automatisch aus `text` abgeleitet. Existiert auf SQLite ueberhaupt nicht (kein Aequivalent) — Weave-Retrievals ORM-Modell bildet diese Spalte deshalb bewusst nicht ab, sondern spricht sie (auf Postgres) nur ueber einen rohen SQL-Ausdruck an. |

Kein `IngestEvent`-Aequivalent: das ist Weave-Knowledges eigene Webhook-Idempotenz-Ledger-Tabelle, fuer einen Leser ohne Bedeutung und deshalb kein Teil dieses Vertrags.

## Tabelle `collections`

Extrahiert aus Weave-Knowledges `backend/app/models/models.py` (`class Collection`) + `backend/alembic/versions/0003_collections.py`. **Anders als `documents`/`chunks` ist dies kein Read-Model einer fremden Quelle im eigentlichen Sinn, sondern ein von Weave-Knowledge selbst synchronisiert gehaltener Spiegel der Registry, deren Herkunft (Owner der Werte, insbesondere `read_teams`) Weave-Ingest ist** — siehe "Sync-Richtung" unten.

| Spalte | Typ | Nullable | Default | Bedeutung |
|---|---|---|---|---|
| `slug` | `varchar(255)` (PK) | nein | — | Eindeutiger, lowercase-slug Identifikator der Collection — dasselbe Feld wie `documents.collection_slug` und `chunks.meta.collection`. Kein separater surrogate `id`. |
| `name` | `varchar(255)` | nein | — | Anzeigename. |
| `description` | `text` | ja | `NULL` | |
| `read_teams` | `json` (`list[str]`) | nein | `[]` | Team-Slugs, die diese Collection lesen duerfen. **Leere Liste = fuer alle lesbar** (Sentinel, nicht "fuer niemanden lesbar"). Durchgesetzt wird das ausschliesslich von Weave-Retrieval — Weave-Knowledge spiegelt den Wert nur, wertet ihn nirgends selbst aus. |
| `synced_at` | `timestamptz` | nein | — | Zeitpunkt des letzten erfolgreichen Upserts dieser Zeile durch `app/services/collection_sync.py`. |

Kein FK von `documents.collection_slug` auf `collections.slug` (siehe oben) — ein frisch erzeugtes Dokument mit brandneuer Collection kann kurzzeitig existieren, bevor der naechste Sync-Tick (oder ein einmaliger Lazy-Reload, siehe `app/api/events.py`) die Registry nachzieht. Ein fehlender Registry-Eintrag darf laut Vertrag niemals die Indizierung eines Dokuments verhindern — nur die Sichtbarkeit in der Suche ist betroffen, und die liegt bei Weave-Retrieval.

### Sync-Richtung

```
Weave-Ingest (Owner: slug, name, description, read_teams)
      |  GET /api/v1/collections/registry  (Bearer WEAVE_INGEST_API_TOKEN)
      v
Weave-Knowledge  --  app/services/collection_sync.py::sync_collections()
      |  ausgeloest durch drei Pfade:
      |  1. sofort bei Empfang eines signierten, internen
      |     `collection.updated`-Hinweises (derselbe interne Event-Endpunkt,
      |     aber keine benutzerverwaltete Webhook-Verbindung; siehe
      |     app/api/events.py::_handle_collection_updated) -- der primaere
      |     Freshness-Mechanismus, insbesondere fuer Rechteentzug
      |     (`read_teams` verkleinert);
      |  2. periodisch als Sicherheitsnetz (weave.knowledge.collection_sync_tick,
      |     Default alle COLLECTION_SYNC_TICK_SECONDS=60s, kein Celery Beat --
      |     siehe app/workers/collection_sync_tasks.py) -- faengt einen
      |     verpassten/fehlgeschlagenen Webhook-Versand ab;
      |  3. einmaliger Lazy-Reload bei unbekanntem Slug (app/api/events.py,
      |     `document.released`-Pfad).
      |
      |  **Rechteentzug wirkt sofort per Event, spaetestens nach einem Tick**:
      |  ein `collection.updated`-Hinweis startet sofort den Registry-Abruf,
      |  der eine verkleinerte `read_teams`-Liste ueblicherweise binnen
      |  Millisekunden in den lokalen Spiegel bringt; schlaegt
      |  der Sync bei Event-Empfang fehl (Weave-Ingest kurz nicht erreichbar),
      |  bleibt der Tick das Netz -- das verbleibende Zeitfenster ist dann
      |  hoechstens COLLECTION_SYNC_TICK_SECONDS. Ein `collection.updated`-Event
      |  erzeugt bewusst KEINEN `ingest_events`-Eintrag (kein job_id/
      |  content_sha256 zum Schluesseln, ein doppelter Sync ist harmlos) --
      |  siehe _handle_collection_updated's Docstring.
      v
weave_knowledge.collections  (voller Spiegel: ein upstream geloeschter
      Slug wird beim naechsten Sync auch hier entfernt, nicht nur nicht
      mehr aktualisiert)
      |  read-only SELECT (dieselbe Rolle/DB-Verbindung wie fuer
      |  documents/chunks, siehe "DB-Rolle" unten)
      v
Weave-Retrieval  --  beantwortet "welche Collections darf Team X lesen"
```

Weave-Knowledge ist der **einzige Schreiber** von `collections` in dieser Datenbank (genau wie bei `documents`/`chunks`) — es schreibt hier nie etwas zurueck nach Weave-Ingest. Weave-Retrieval liest `collections` genauso read-only mit wie `documents`/`chunks` (siehe "DB-Rolle" unten — derselbe `GRANT SELECT` deckt alle drei Tabellen ab) und ist damit die Lese-Autoritaet dafuer, welche Collections ein Team lesen darf; es legt dafuer **kein eigenes Schema** an, exakt wie im Architektur-Entscheidungsabschnitt oben fuer `documents`/`chunks` beschrieben.

## Indexe

| Index | Tabelle/Spalte(n) | Typ | Nur Postgres? |
|---|---|---|---|
| `ix_documents_content_sha256` | `documents.content_sha256` | btree | nein |
| `ix_documents_previous_job_id` | `documents.previous_job_id` | btree | nein |
| `ix_documents_team` | `documents.team` | btree | nein |
| `ix_documents_department` | `documents.department` | btree | nein |
| `ix_documents_status` | `documents.status` | btree | nein |
| `ix_documents_collection_slug` | `documents.collection_slug` | btree | nein |
| `ix_chunks_document_id` | `chunks.document_id` | btree | nein |
| `ix_chunks_embedding_hnsw` | `chunks.embedding` | HNSW, `vector_cosine_ops` | **ja** — SQLite hat keine pgvector-Extension; ohne diesen Index waere jede Vektor-Aehnlichkeitssuche ein Full-Table-Scan. `vector_cosine_ops` matcht Cosine-Distanz — die einzige Distanzfunktion, die Weave-Retrievals Vektor-Suche verwenden darf (ein mit L2/Inner-Product angelegter Index wuerde von einer Cosine-Query nie genutzt). |
| `ix_chunks_tsv` | `chunks.tsv` | GIN | **ja** — SQLite hat weder `tsvector` noch diese generierte Spalte ueberhaupt. |

## DB-Rolle (Produktion)

Weave-Retrieval verbindet sich **nie** mit Weave-Knowledges Schreib-Credentials. Stattdessen legt Weave-Knowledge (oder wer immer die `weave_knowledge`-Datenbank administriert) eine eigene, rein lesende Rolle an:

```sql
-- Einmalig, von einer Person mit Admin-Rechten auf der weave_knowledge-DB:
CREATE ROLE weave_retrieval_ro WITH LOGIN PASSWORD '<siehe Secret-Manager, niemals im Repo>';
GRANT CONNECT ON DATABASE weave_knowledge TO weave_retrieval_ro;
GRANT USAGE ON SCHEMA public TO weave_retrieval_ro;
GRANT SELECT ON documents, chunks, collections TO weave_retrieval_ro;

-- Optional, damit ein zukuenftig additiv hinzugefuegtes Read-Model
-- (siehe "Versionierungsregel" unten) nicht erst einen manuellen GRANT
-- braucht, um lesbar zu werden:
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO weave_retrieval_ro;
```

`weave_retrieval_ro` bekommt **niemals** `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`/`CREATE`/`DROP` — das ist keine Haertungsmassnahme, sondern die Durchsetzung der Architektur-Entscheidung auf DB-Ebene: selbst ein Bug in Weave-Retrievals eigenem Code kann diese Tabellen so technisch nicht veraendern. `DATABASE_URL` in Weave-Retrievals Konfiguration (`backend/app/core/config.py`) traegt diese Rolle in Produktion, z.B. `postgresql+psycopg://weave_retrieval_ro:...@<host>/weave_knowledge`.

## SQLite-Dev-Fallback

Lokale Entwicklung und die Pytest-Suite beider Services laufen gegen eine lokale SQLite-Datei statt gegen Postgres (kein pgvector, kein `tsvector` verfuegbar):

- `chunks.embedding` faellt auf eine plain-`JSON`-Spalte zurueck (`VectorType.load_dialect_impl`, identisch in beiden Services kopiert) — ein `list[float]` rundet korrekt, aber ohne Vektor-Index oder Aehnlichkeitssuche.
- `chunks.tsv` existiert auf SQLite gar nicht — Weave-Retrievals Volltextsuche-Pfad muss auf SQLite entweder entfallen oder (Stage 2) auf eine einfachere `LIKE`/Python-seitige Alternative ausweichen; dieser Vertrag macht dazu keine Vorgabe, weil `tsv` nie Teil des ORM-Modells ist.
- Weave-Retrievals Testsuite legt beide Tabellen ueber ihre eigene Kopie der Modelle in einer eigenen `test.db` an (`Base.metadata.create_all`) — das ist eine isolierte Testdatenbank, keine Verbindung zu Weave-Knowledges echter Datenbank, und widerspricht deshalb nicht "Retrieval legt nie Schema in `weave_knowledge` an".

## Versionierungsregel

- **Additiv (kompatibel, kein Koordinations-Meeting noetig):** eine neue nullable Spalte, ein neuer Index, eine neue Tabelle. Weave-Knowledge ergaenzt sie per Migration; Weave-Retrieval zieht sie bei Bedarf in seine eigene Kopie der Modelle nach (siehe hier), muss es aber nicht sofort, solange es die Spalte nicht braucht.
- **Breaking (erfordert Koordination BEIDER Services vor dem Deploy):** Spalte umbenennen/entfernen, Spaltentyp aendern (inkl. `EMBEDDING_DIMENSION`-Aenderung — siehe unten), Tabelle entfernen, Unique-/FK-Constraint aendern. Ablauf: Vertrag-Version in diesem Dokument erhoehen -> Weave-Knowledges Migration UND Weave-Retrievals Modell-Kopie im selben Koordinationsfenster aendern -> erst dann deployen. Ein bereits laufendes Weave-Retrieval darf niemals gegen ein Schema laufen, das eine breaking Aenderung dieses Vertrags bereits enthaelt, die es selbst noch nicht kennt.
- **`EMBEDDING_DIMENSION`-Aenderung ist immer breaking**, obwohl technisch "nur" ein Konfigurationswert: sie erfordert bei Weave-Knowledge eine neue Migration (pgvector kann eine bestehende `vector(N)`-Spalte nicht in-place umdimensionieren) plus einen vollstaendigen Reindex aller Chunks, UND bei Weave-Retrieval eine synchron angepasste `EMBEDDING_DIMENSION`/`EMBEDDING_MODEL`-Konfiguration — sonst decodiert `VectorType` die gespeicherten Vektoren mit der falschen Breite.
- Bei einer breaking Aenderung wird die **Vertrag-Version** oben (aktuell `1`) erhoeht und der Grund im Aenderungsprotokoll unten festgehalten.

## Aenderungsprotokoll

- **v1 (2026-08-31):** Initialer Vertrag, extrahiert aus Weave-Knowledges `backend/app/models/models.py` (`Document`, `Chunk`, `VectorType`) und `backend/alembic/versions/0001_init.py`. Erstellt im Rahmen des Weave-Retrieval-Service-Skeletons (Phase 3).
- **Additiv, weiterhin v1 (2026-08-31):** `collections`-Tabelle (Registry-Spiegel), `documents.collection_slug` (nullable, kein FK) und `chunks.meta.collection` hinzugefuegt — siehe "Tabelle `collections`" und "Sync-Richtung" oben. Rein additiv im Sinn der Versionierungsregel oben (neue nullable Spalte + neue Tabelle), daher kein Versionssprung: ein bereits laufendes Weave-Retrieval, das diese Felder noch nicht kennt, funktioniert unveraendert weiter, es sieht nur noch keine Collection-Filterung.
