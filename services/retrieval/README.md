# Weave-Retrieval

## Einordnung

Weave-Retrieval ist der **Hybrid Search Service** des Weave-Systems. Sie bietet eine Unified-API für Vektor- und Volltextsuche mit Metadaten-Filtering und Reranking. Sie wird von Weave-Runtime für Knowledge-Intents aufgerufen und steht nicht im kritischen Pfad jeder Konversation.

## Zweck

- POST `/search`-Endpunkt mit Query, Filtern und Top-K-Parameter
- Parallele Vector Search (pgvector) und BM25/Fulltext-Suche
- Reciprocal Rank Fusion zur Kombination von Ranking-Signalen
- Reranker-Integration (Top 20 → Final 5 Ergebnisse)
- Metadaten-basierte Zugriffskontrolle (Team, Department, Customer, Language) VOR der Suche

## Verantwortlichkeiten

- Ablauf der Hybrid-Search-Pipeline
- Optimierung von Latenz und Genauigkeit über RRF-Gewichtung
- Zugriffskontroll-Enforcing basierend auf Nutzer-Kontext
- Response-Formatting mit Konfidenz-Scores und Metadaten
- Monitoring von Query-Performance und Reranker-Accuracy

## Nicht-Ziele / Abgrenzung

- **Kein Indexieren**: Index-Management ist Aufgabe von Weave-Knowledge
- **Kein LLM-Aufruf**: Nur Retrieval, kein Ranking durch ein Modell
- Keine Authentifizierung: Auth erfolgt upstream in Weave-API
- Keine Konversations-History: Stateless Query-Verarbeitung

## Schnittstellen

**Input:**
- POST `/search`: Query, Filter-Objekt, Top-K, optionaler Reranker-Toggle

**Output:**
- JSON-Array mit Dokumenten-Chunks: ID, Text, Score, Metadaten, Quelle

**Abhängigkeiten:**
- PostgreSQL mit pgvector und pgfts (Fulltext-Indizes)
- Optionaler Reranker-Service (z.B. Cohere, jina.ai, oder lokal)

## Suche

`POST /api/v1/search` (siehe "API" unten) durchläuft für jede Anfrage dieselben Stufen, implementiert in `app/services/search.py:search()`:

1. **Embedding**: die Query wird mit `settings.embedding_provider` (`app/services/embeddings.py`) in denselben Vektorraum eingebettet, mit dem Weave-Knowledge `chunks.embedding` befüllt hat.
2. **Metadaten-Filterung VOR dem Ranking** (`apply_filters()`): `documents.status = 'indexed'` sowie die `allowed_teams`- UND `allowed_collections`-Zugriffsgrenzen (aus `SearchRequest`, propagiert von Weave-API und Weave-Runtime, siehe ADR-0002 bzw. den Collections-Vertrag) werden unconditional angewendet — niemals überspringbar, unabhängig davon, ob `filters.team`/`filters.collection` gesetzt sind, und unabhängig voneinander (beide Grenzen müssen erfüllt sein). `filters.team`/`filters.department`/`filters.collection` filtern echte `documents`-Spalten; `filters.source`/`language`/`document_type`/`tags` filtern gegen `chunks.meta` (dorthin von Weave-Knowledges `enrichment.py` denormalisiert — `documents` selbst hat kein `source`-Feld, nur `original_filename`). Siehe "Collections" unten für die genaue `allowed_collections`-Semantik, insbesondere die Sonderregel für Dokumente ohne Collection.
3. **Parallele Kandidaten-Suche**, je bis zu `top_k` Treffer:
   - **Vektor-Suche** (`vector_search()`): Cosine-Similarity über `chunks.embedding` — auf Postgres pgvectors `<=>`-Operator mit dem HNSW-Index, auf SQLite (lokale Entwicklung/Tests) ein reiner Python-Fallback. Nur Chunks, deren `embedding_model` exakt zu `settings.embedding_model` passt, werden verglichen.
   - **Volltext-Suche** (`fulltext_search()`): auf Postgres `websearch_to_tsquery('simple', ...)` gegen die generierte `chunks.tsv`-Spalte, gerankt mit `ts_rank`; auf SQLite ein Fallback über Token-Overlap.
4. **Reciprocal Rank Fusion** (`rrf_fuse()`): kombiniert beide Ranglisten allein über die RANG-Position jedes Kandidaten in jeder Liste (`1 / (rrf_k + rang)`, `rrf_k` konfigurierbar über `RRF_K`, Default 60) — nicht über die Rohscores, die zwischen Cosine-Similarity und Volltext-Rang nicht vergleichbar wären. Ein Chunk, den BEIDE Signale finden, gewinnt fast immer gegen einen, den nur ein Signal findet. Die fusionierten Top `top_k` Kandidaten gehen in die nächste Stufe.
5. **Reranking** (optional, `settings.rerank_provider`, `app/services/reranker.py`): `none` (Default) reicht die RRF-Reihenfolge unverändert durch (`NoopReranker`); `fake` ist ein deterministischer, abhängigkeitsfreier Provider für Tests/Demos; `api` spricht einen Cohere/Jina-kompatiblen `POST {base_url}/rerank`-Endpunkt. **Ein Reranker-Ausfall lässt die Suche niemals scheitern**: Eine `RerankError` wird abgefangen, die Suche fällt auf die unveränderte RRF-Reihenfolge zurück (`scores.rerank = null` für jedes Ergebnis) und `trace.rerank_error = true` markiert das für Monitoring/Debugging.
6. **`final_k`-Kürzung**: erst NACH dem Reranking wird auf die finalen `final_k` Ergebnisse gekürzt (Reranker-Integration "Top `top_k` → Final `final_k` Ergebnisse"). `final_k > top_k` wird von der API bereits vor dem eigentlichen Pipeline-Lauf mit `400` abgelehnt — ein Reranker kann eine Kandidatenmenge nur verkleinern, niemals vergrößern.

Jede Antwort enthält einen `trace` mit Kandidatenzahlen pro Stufe (`vector_candidates`, `fulltext_candidates`, `fused`, `reranked`), dem `rerank_error`-Flag und `timings_ms` pro Stufe (`embed`, `vector_search`, `fulltext_search`, `fuse`, `rerank`) — für genau das Monitoring, das oben unter "Verantwortlichkeiten" gefordert ist.

## API

`POST /api/v1/search` verlangt `Authorization: Bearer <RETRIEVAL_API_TOKEN>` (service-zu-service, ADR-0002-Muster). `GET /health` ist unauthentifiziert und prüft per `SELECT 1`, ob die konfigurierte Datenbank erreichbar ist.

**Request** (`filters`, `allowed_teams`, `allowed_collections`, `top_k`, `final_k` sind alle optional — siehe `app/schemas/search.py:SearchRequest`):

```json
POST /api/v1/search
Authorization: Bearer <RETRIEVAL_API_TOKEN>
Content-Type: application/json

{
  "query": "Wie setze ich mein VPN-Passwort zurück?",
  "filters": {
    "department": "IT-Support",
    "tags": ["vpn"]
  },
  "allowed_teams": ["Kundenservice"],
  "allowed_collections": ["support-docs"],
  "top_k": 20,
  "final_k": 5
}
```

**Response** (`200`, `app/schemas/search.py:SearchResponse`) — ein Treffer mit `scores.rerank = null` bedeutet entweder `RERANK_PROVIDER=none` oder einen abgefangenen Reranker-Fehler; welcher der beiden Fälle vorliegt, verrät `trace.rerank_error`:

```json
{
  "query": "Wie setze ich mein VPN-Passwort zurück?",
  "results": [
    {
      "chunk_id": 42,
      "document_id": "b4b997e4-f2e6-4944-9943-78de9dce77ae",
      "text": "Sie können Ihr VPN-Passwort im Self-Service-Portal unter Einstellungen zurücksetzen, sofern Sie Ihre Mitarbeiter-ID kennen.",
      "heading_path": ["IT-Support", "VPN"],
      "page_start": 3,
      "page_end": 3,
      "document_version": 1,
      "source": "it-support-faq.pdf",
      "original_filename": "IT-Support-FAQ.pdf",
      "team": "Kundenservice",
      "department": "IT-Support",
      "scores": { "vector": 0.91, "fulltext": 0.83, "rrf": 0.031, "rerank": 0.97 },
      "embedding_model": "fake-embed"
    }
  ],
  "trace": {
    "vector_candidates": 12,
    "fulltext_candidates": 8,
    "fused": 15,
    "reranked": 15,
    "rerank_error": false,
    "timings_ms": { "embed": 1.2, "vector_search": 3.4, "fulltext_search": 2.1, "fuse": 0.1, "rerank": 45.6 }
  }
}
```

Fehlerfälle: `401` (fehlendes/falsches Bearer-Token), `503` (kein `RETRIEVAL_API_TOKEN` konfiguriert — niemals "Auth aus"), `422` (z.B. leere `query`), `400` (`final_k > top_k`).

## Collections

Weave-Retrieval ist die **Lese-Autorität** des "Collections"-Vertrags (siehe die Vertrag-Definition; Owner der Registry-Werte ist Weave-Ingest, gespiegelt über Weave-Knowledge in derselben `collections`-Tabelle wie `documents`/`chunks`, siehe oben): es beantwortet, welche Collections ein Team lesen darf, und setzt die Zugriffsgrenze bei der Suche durch — schreibt selbst aber keine Zeile dieser Tabelle.

- **`GET /api/v1/collections?team=<slug>`** (`Authorization: Bearer <RETRIEVAL_API_TOKEN>`, wie `/search`) liefert alle Collections, die `team` lesen darf: jede **öffentliche** Collection (`read_teams == []`, der Vertrag-Sentinel für "für alle lesbar") plus jede, deren `read_teams` das Team explizit nennt. Ohne `team`-Parameter kommen nur die öffentlichen zurück.

  ```json
  GET /api/v1/collections?team=Kundenservice

  [
    { "slug": "support-docs", "name": "Support-Dokumentation", "description": null, "public": false },
    { "slug": "public-docs", "name": "Allgemeine Infos", "description": null, "public": true }
  ]
  ```

- **`SearchRequest.allowed_collections`** ist die zu `allowed_teams` analoge HARTE Zugriffsgrenze: `apply_filters()` (`app/services/search.py`) erzwingt sie unconditional, in SQL, vor jedem Ranking, unabhängig von `filters.collection` — ein Aufrufer, der eine fremde Collection anfragt, bekommt kein Ergebnis, nie einen Fehler und nie einen Zugriff außerhalb der Grenze. `null` bedeutet "keine Einschränkung" (nur für service-interne Aufrufer gedacht); eine leere Liste bedeutet "keine Collection erlaubt", also null Treffer. Ein typischer Aufrufer ruft vorher `GET /api/v1/collections?team=<team>` auf und reicht genau diese Slug-Liste als `allowed_collections` an `/search` weiter.

- **Altbestand ohne Collection** (`Document.collection_slug IS NULL`, z.B. alle vor Einführung dieses Features indizierten Dokumente) ist NUR sichtbar, wenn `allowed_collections` entweder `null` ist oder den Sentinel-Wert `"__none__"` (`app/services/search.py:NO_COLLECTION_SENTINEL`) enthält — das Auflisten realer Collection-Slugs allein gibt keinen impliziten Zugriff auf unscoped Altbestand, sonst würde jeder Aufrufer mit Zugriff auf irgendeine Collection automatisch jedes Alt-Dokument sehen.

- **`SearchFilters.collection`** ist eine reine Verfeinerung innerhalb dessen, was `allowed_collections` bereits erlaubt — sie kann die Grenze niemals erweitern.

## Evaluation

Retrieval-Qualität wird gegen ein handkuratiertes **Golden-Set** gemessen: Query → erwartete relevante Chunks (per `Document.source_job_id`, optional eingeengt auf einen Text-Marker-Substring), siehe `backend/eval/golden.example.yaml` und die Docstrings in `app/services/evalharness.py` für das genaue Format. `evaluate()` berechnet Recall@k (für konfigurierbare `k`), MRR und Hit@1, pro Query und als Makro-Durchschnitt.

```bash
cd backend

# Einmalig: Demo-Korpus in eine FRISCHE, lokale SQLite-Datei seeden (siehe
# unten, warum das nur mit SQLite funktioniert). Der Korpus entspricht exakt
# den Judgments in eval/golden.example.yaml, sodass dessen Recall@5 = 1.0 ist.
DATABASE_URL=sqlite:///./eval_demo.db .venv/bin/python -m app.cli seed-demo

# Golden-Set gegen diese Datenbank auswerten
DATABASE_URL=sqlite:///./eval_demo.db .venv/bin/python -m app.cli eval \
  --golden eval/golden.example.yaml --min-recall 0.8

# ... oder mit eigenen Recall@k-Cutoffs; Recall@5 ist immer im Report und
# immer die von --min-recall gegatete Metrik, unabhängig von --k:
DATABASE_URL=sqlite:///./eval_demo.db .venv/bin/python -m app.cli eval \
  --golden eval/golden.example.yaml --k 5 --k 20
```

Exit-Codes (safe für ein CI-Gate): `0` Erfolg, `1` aggregiertes Recall@5 unter `--min-recall`, `2` Golden-Set nicht ladbar.

**`seed-demo`** ist ein reines Dev-/CI-Fixture-Kommando, **kein** Ersatz für Weave-Knowledges Indexierung: es legt (`Base.metadata.drop_all` + `create_all`) das komplette Schema auf `DATABASE_URL` neu an und seedet einen festen Mini-Korpus — deshalb verweigert es sich strikt, sobald `DATABASE_URL` nicht mit `sqlite` beginnt, um niemals versehentlich Schema auf der echten, geteilten `weave_knowledge`-Postgres-Datenbank anzulegen (siehe "Architektur-Entscheidung" unten). Ein reales Golden-Set kuratiert man stattdessen manuell gegen tatsächlich von Weave-Knowledge indexierte Dokumente und lässt `eval` gegen die echte (read-only) `DATABASE_URL` laufen — `get_search_fn()` (`app/services/evalharness.py`) ruft dafür exakt dieselbe `app/services/search.py:search()`-Pipeline auf, die auch hinter `POST /api/v1/search` steht, mit `top_k = final_k = settings.search_top_k`, damit auch ein `k` oberhalb von `settings.search_final_k` noch aussagekräftig ist.

## Architektur-Entscheidung: geteiltes Read-Model

Der Chunk-Store (`documents`/`chunks`/`collections` in der `weave_knowledge`-Datenbank) ist ein geteiltes Read-Model: **Weave-Knowledge ist der einzige Schreiber**, Weave-Retrieval liest read-only direkt auf derselben Datenbank, ueber eine eigene DB-Rolle mit reinen `SELECT`-Grants. Begruendung: pgvector-KNN und tsvector-Ranking muessen als SQL-Operation in der Datenbank selbst laufen, nicht auf Anwendungsebene ueber einen HTTP-Proxy nachgebaut werden. Weave-Retrieval besitzt deshalb **kein eigenes Alembic** und legt **niemals** Schema auf dieser Datenbank an (ausser in seiner eigenen isolierten Test-SQLite, und im rein lokalen `seed-demo`-Fixture-Kommando oben, das sich strikt auf SQLite beschränkt). Der vollstaendige, versionierte Vertrag — Feldtabellen, Indexe, das genaue GRANT-Statement fuer die read-only Rolle, die SQLite-Dev-Fallback-Semantik und die Versionierungsregel fuer Schema-Aenderungen — steht in [`contracts/chunk-store.md`](../../contracts/chunk-store.md).

## Entwicklung

```bash
# Einmalig: virtuelle Umgebung + Abhaengigkeiten
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.in

# .env anlegen (lokale SQLite-Defaults reichen fuer Dev/Tests)
cp .env.example .env

# Tests
.venv/bin/python -m pytest backend/tests -q

# Lokaler Server
.venv/bin/uvicorn app.main:app --reload --app-dir backend --port 8000
```

**Layout** (`backend/app/`): `core` (Config, DB-Engine, Service-Token-Auth), `models` (Read-Models fuer `documents`/`chunks`, siehe oben), `schemas` (Pydantic-Request-/Response-Modelle), `api` (FastAPI-Router), `services` (Hybrid-Search-Pipeline, Reranker, Eval-Harness — siehe "Suche" und "Evaluation" oben), `cli.py` (Eval- und Demo-Seed-Kommandos). Bewusst **kein** `alembic/` (kein eigenes Schema) und **kein** `workers/` (Suche ist synchron, kein Celery).

## Status

**Hybrid-Search-Pipeline vollständig implementiert.** Parallele Vektor-/Volltextsuche, Reciprocal-Rank-Fusion, Metadaten-Zugriffskontroll-Filterung, optionales Reranking (`none`/`fake`/`api`, mit garantiertem Fallback auf RRF-Reihenfolge bei Reranker-Ausfall) und die Eval-Harness (`app.cli eval`, gegen die echte Pipeline verdrahtet, inklusive `seed-demo`-Fixture-Kommando) stehen und sind getestet (`backend/tests`, ausschliesslich gegen SQLite — die Postgres-spezifischen Codepfade für pgvector-KNN und `tsvector`-Ranking sind entsprechend dieser Suite nicht direkt ausführbar, aber nach demselben Muster wie die getesteten SQLite-Fallbacks implementiert und reviewt). Offen: ein produktiv kuratiertes Golden-Set gegen echte Weave-Knowledge-Dokumente (siehe "Evaluation" oben), sowie Postgres-Integrationstests, sobald eine echte `weave_knowledge`-Datenbank für CI verfügbar ist.
