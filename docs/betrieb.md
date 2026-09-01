# Weave — Betriebshandbuch

Was gesetzt werden muss, damit die sechs Dienste zusammenspielen — und welche
falschen Werte nichts kaputt machen, sondern nur dafür sorgen, dass es leise
nicht funktioniert.

Erhoben aus den `config.py` der sechs Dienste und an einem laufenden Stack
gegengeprüft. Diese Datei ist die maßgebliche Fassung.

---

## 1. Die sechs Dienste

| Dienst | Port | Datenbank | Aufgabe |
|---|---|---|---|
| Weave-Ingest | 8000 | `weave_ingest` | OCR, Markdown + Frontmatter, Quality-Gate, besitzt die Collections |
| Weave-Knowledge | 8001 | `weave_knowledge` | Chunking, Embeddings, schreibt den Chunk-Store, spiegelt die Collection-Registry |
| Weave-Retrieval | 8002 | liest `weave_knowledge` | Hybride Suche, Lese-Autorität für Collections. Einziger Dienst ohne eigene DB (ADR-0005) |
| Weave-Runtime | 8003 | keine | Intent-Router, Bots als YAML, LLM, n8n, stellt Delegations-Token aus |
| Weave-API | 8004 | `weave_api` | Gateway und Identitäts-Autorität: Nutzer, Tokens, Sitzungen, Gespräche |
| Weave-Tools | 8005 / 3001 | keine | MCP-Dienst mit rechte-gebundener Suche; Chat-Oberfläche |

Weave-Retrieval liest die Datenbank von Weave-Knowledge read-only mit, weil
pgvector-Ähnlichkeit und tsvector-Ranking als SQL *in* der Datenbank laufen
müssen. Knowledge schreibt, Retrieval liest über eine Rolle mit ausschließlich
`SELECT`. Das Schema ist dadurch ein Vertrag: `contracts/chunk-store.md`.

Dazu kommen zwei **optionale** CPU-Modelldienste (`services/embeddings` und
`services/reranker`, neben `services/tools` und `services/chat`), die Knowledge/Retrieval anstelle der
Attrappen-Provider ansprechen können:

| Dienst | Port | Datenbank | Aufgabe |
|---|---|---|---|
| Weave-Embeddings | 8006 | keine | OpenAI-kompatibles `/v1/embeddings` für `intfloat/multilingual-e5-small` (CPU, onnxruntime) |
| Weave-Reranker | 8007 | keine | Cohere/Jina-kompatibles `/rerank` für `BAAI/bge-reranker-v2-m3` (CPU, sentence-transformers) |

Beide starten wie jeder andere Dienst hier und beantworten `/health` sofort —
aber standardmäßig ruft sie niemand auf: Knowledge/Retrieval bleiben auf den
Attrappen-Providern, bis das in Abschnitt 7 beschrieben umgestellt wird.

---

## 2. Lokal starten

Die Compose-Datei zeigt auf Registry-Images, die nie veröffentlicht wurden. Das
Override baut alle sechs Dienste aus diesem Monorepo (`services/<dienst>`).

```bash
cd deploy
cp .env.example .env    # Werte füllen, siehe Abschnitt 4

docker compose -f docker-compose.weave.yml -f docker-compose.local.yml up -d --build
```

Der OCR-Worker bringt PaddleOCR mit und ist mehrere GB groß; er hängt hinter
einem Profil, damit der Rest in Minuten hochkommt:

```bash
docker compose -f docker-compose.weave.yml -f docker-compose.local.yml \
  --profile ocr up -d weave-ingest-worker
```

Beenden mit `down` (Daten bleiben in den Volumes), `down -v` löscht auch die
Datenbanken. Ist Port 3000 belegt, `FRONTEND_PORT` umsetzen — sonst bricht der
Start des ganzen Stacks ab.

---

## 3. Erstinbetriebnahme

Zwei Tokens entstehen erst, wenn der Stack läuft. Nur diese Reihenfolge löst das auf:

1. **Ersten Administrator in Weave-Ingest anlegen** über die Setup-Seite. Der
   erste Nutzer wird automatisch Administrator.
2. **Dort einen persönlichen API-Token erzeugen.** Er muss einem *Administrator*
   gehören: Weave-Knowledge holt die Collection-Registry über einen
   Admin-Endpunkt, weil sie die komplette Zugriffskarte ausgibt.
3. **Token eintragen und neu starten:** als `WEAVE_KNOWLEDGE_INGEST_API_TOKEN`
   in die `.env`, dann
   `docker compose … restart weave-knowledge weave-knowledge-worker`.
   Bis dahin läuft der Registry-Sync in ein 401.
4. **Webhook-Verbindung in Ingest anlegen**, damit verarbeitete Dokumente bei
   Knowledge ankommen. Das Secret dieser Verbindung muss identisch zu
   `WEAVE_KNOWLEDGE_WEBHOOK_SECRET` sein.
5. **Nutzer für die Chat-Oberfläche anlegen** (rein tokenbasiert, kein Passwort);
   der Token wird genau einmal angezeigt:
   ```bash
   docker compose exec weave-api python -m app.cli create-user --username matze --team legal
   ```

---

## 4. Pflichtwerte

Ohne diese startet der Dienst nicht oder verweigert fail-closed die Arbeit.

| Dienst | Variable | Wofür |
|---|---|---|
| Ingest | `SECRET_KEY` | Verschlüsselt gespeicherte Zugangsdaten. Pflicht, sobald die DB nicht SQLite ist |
| Ingest | `REDIS_URL` | Celery-Broker für die OCR-Verarbeitung — logische DB `0` |
| Ingest | `CORS_ORIGINS` | Erlaubte Frontend-Herkunft, zugleich CSRF-Schutz |
| Knowledge | `WEAVE_INGEST_BASE_URL` | Woher Markdown und Registry geholt werden |
| Knowledge | `WEAVE_INGEST_API_TOKEN` | Admin-Token aus Ingest (Schritt 2) |
| Knowledge | `WEAVE_INGEST_WEBHOOK_SECRET` | Prüft eingehende Events. Ohne Wert: `503` — dieser Endpunkt schreibt in den Index |
| Retrieval | `RETRIEVAL_API_TOKEN` | Service-Auth. Ohne Wert antwortet jeder Aufruf mit `503` |
| Runtime | `RUNTIME_API_TOKEN` | Nimmt nur Aufrufe des Gateways an |
| Runtime | `WEAVE_DELEGATION_SECRET` | Signiert Delegations-Token. Ohne Wert wird keines ausgestellt |
| API | `INTROSPECTION_SERVICE_TOKEN` | Erlaubt anderen Diensten, Tokens prüfen zu lassen |
| Tools | `TOOLS_API_TOKEN` | Service-Auth der REST-Oberfläche |
| Tools | `WEAVE_DELEGATION_SECRET` | Prüft Delegations-Token. Ohne Wert: `503` |
| Tools | `INTROSPECTION_SERVICE_TOKEN` | Fragt Weave-API, wem ein Token gehört |
| Tools | `RETRIEVAL_API_TOKEN` | Sucht im Auftrag des Aufrufers |
| Weave-Embeddings *(sobald genutzt)* | `EMBEDDINGS_API_TOKEN` | Ohne Wert: `503` auf jeden `/v1/embeddings`-Aufruf, der Dienst selbst startet trotzdem |
| Weave-Reranker *(sobald genutzt)* | `RERANKER_API_TOKEN` | Ohne Wert: `503` auf jeden `/rerank`-Aufruf, der Dienst selbst startet trotzdem |

---

## 5. Geteilte Werte

Diese müssen in mehreren Diensten **zeichengleich** stehen. Häufigste
Fehlerquelle beim Aufsetzen, und die Symptome zeigen selten auf die Ursache.

| Wert | Wer | Bei Abweichung |
|---|---|---|
| `WEAVE_DELEGATION_SECRET` | Runtime (stellt aus), Tools (prüft) | Jeder n8n- und MCP-Aufruf scheitert. Fehlt er ganz, verweigern beide Seiten hart statt unsigniert zu arbeiten |
| `INTROSPECTION_SERVICE_TOKEN` | API (prüft), Tools (weist vor) | MCP kann niemanden identifizieren, jede Anfrage wird abgewiesen |
| `RETRIEVAL_API_TOKEN` | Retrieval (besitzt), Runtime, API, Tools | Retrieval antwortet 401, Aufrufer melden 502 — sieht aus wie ein Ausfall |
| `RUNTIME_API_TOKEN` | Runtime (besitzt), API | Jeder Chat endet in 502 |
| `WEAVE_INGEST_WEBHOOK_SECRET` | Ingest (Webhook-Verbindung), Knowledge | Events werden mit 401 abgewiesen — Dokumente erscheinen nie im Index |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Knowledge (indiziert), Retrieval (sucht) | **Kein Fehler.** Vektorsuche liefert kommentarlos nichts |
| `EMBEDDING_DIMENSION` | Knowledge (Spaltenbreite), Retrieval | Ändern heißt: Migration *und* vollständiger Reindex |
| `EMBEDDING_API_KEY` ↔ `EMBEDDINGS_API_TOKEN` | Knowledge/Retrieval (senden), Weave-Embeddings (prüft) | Nur relevant, wenn `EMBEDDING_BASE_URL` auf Weave-Embeddings zeigt — sonst `401` von jedem Embed-Aufruf |
| `RERANK_API_KEY` ↔ `RERANKER_API_TOKEN` | Retrieval (sendet), Weave-Reranker (prüft) | Nur relevant, wenn `RERANK_BASE_URL` auf Weave-Reranker zeigt — sonst **kein Fehler**, nur `rerank_error` im Trace (der Reranker degradiert leise, siehe Abschnitt 7) |

Ohne Klartext prüfen — Fingerabdrücke vergleichen:

```bash
for c in weave_runtime weave_tools_backend; do
  echo -n "$c: "
  docker exec $c printenv WEAVE_DELEGATION_SECRET | shasum | cut -c1-8
done
# Zwei gleiche Zeilen = gut.
```

---

## 6. Stille Fallen

Bei allen folgenden Fehlern stürzt nichts ab und es erscheint keine
Fehlermeldung — es funktioniert nur nicht, oder schlimmer: scheinbar doch.

**`EMBEDDING_MODEL` — die Suche findet nie etwas.**
Die Vektorsuche vergleicht ausschließlich Chunks, deren gespeicherter
Modellname exakt dem konfigurierten entspricht. Weicht Retrieval von Knowledge
ab, ist das Ergebnis eine leere Liste, kein Fehler. Der Volltextpfad
funktioniert derweil weiter und verschleiert das.

**`EMBEDDING_PROVIDER` — der Index enthält Attrappen.**
Voreinstellung ist `fake`. Produktiv vergessen heißt: der Dienst indiziert
klaglos weiter, aber mit bedeutungslosen Pseudo-Vektoren. Gilt genauso für
`LLM_PROVIDER`: bleibt er auf `fake`, antwortet jeder Bot mit Platzhaltertext.

**`DATABASE_URL` — der Dienst schreibt in eine Datei im Container.**
Fehlt bei Ingest sowohl `DATABASE_URL` als auch die vollständige Kombination
aus Host, Datenbank und Benutzer, fällt der Dienst still auf SQLite zurück. Bei
Retrieval ist der SQLite-Pfad sogar die Voreinstellung. Nach dem Start einmal
`printenv DATABASE_URL` im Container lesen.

**`RETRIEVAL_BASE_URL` — Antworten aus dem falschen Bestand.**
Zeigt der Wert bei Weave-Tools auf eine falsche, aber erreichbare
Retrieval-Instanz, kommen ohne jede Warnung Treffer aus einem anderen Korpus.

**`REDIS_URL` — Aufträge verschwinden.**
Jeder Dienst hat laut ADR-0001 eine eigene logische Redis-Datenbank
(Ingest `0`, Knowledge `1`). Kopierte URLs lassen Warteschlangen kollidieren.
Bei Ingest kommt hinzu: fällt Redis aus, gibt das Rate-Limit still auf.

**`CORS_ORIGINS` — Anmelden ist unmöglich.**
Steuert nicht nur CORS, sondern die Herkunftsprüfung gegen CSRF. Fehlt die
echte Adresse, werden *alle* schreibenden Anfragen mit 403 abgewiesen, auch der
Login. Erwartet wird ein JSON-Array:
`CORS_ORIGINS='["https://app.example.com"]'`

**`PUBLIC_API_URL` — Downloads zeigen ins Leere.**
Stimmt der Wert nicht mit der von außen erreichbaren Adresse überein, schlägt
der OIDC-Login mit Redirect-Mismatch fehl und Download-Links in
Webhook-Payloads zeigen falsch. Ingest selbst merkt nichts davon.

**`OIDC_ISSUER` — SSO ist unsichtbar statt kaputt.**
OIDC gilt genau dann als aktiviert, wenn Issuer *und* Client-ID gesetzt sind.
Ist nur eines gefüllt, antworten alle Anmelderouten mit 404. Ebenso: eine leere
`OIDC_POST_LOGIN_ALLOWED_URLS` lässt jedes `return_to` still ins Leere laufen.

**`PADDLE_DEFAULT_PROFILE` — ein Tippfehler fällt nie auf.**
Ein unbekannter Profilname wird kommentarlos auf die Voreinstellung
zurückgesetzt. Nach einer Änderung die `profile`-Angabe im erzeugten
Frontmatter kontrollieren.

**`UPLOADS_DIR` / `RESULTS_DIR` — der Worker findet die Datei nicht.**
API-Container und OCR-Worker müssen dasselbe Volume an denselben Pfad mounten.
Ohne persistentes Volume sind Uploads nach einem Neustart weg.

---

## 7. Echte Provider

Voreingestellt laufen Embeddings, LLM und Reranker als Attrappen: die Kette
funktioniert vollständig, ohne Schlüssel und ohne Kosten.

| Wofür | Variablen | Wo |
|---|---|---|
| Embeddings | `EMBEDDING_PROVIDER=openai`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION` | Knowledge **und** Retrieval, zeichengleich (eine einzige `x-embedding-env`-Compose-Variable, siehe `deploy/docker-compose.weave.yml`) |
| Sprachmodell | `LLM_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_DEFAULT_MODEL` | nur Runtime |
| Reranker | `RERANK_PROVIDER=api`, `RERANK_BASE_URL`, `RERANK_API_KEY`, `RERANK_MODEL` | nur Retrieval |

**`SEARCH_TOP_K` darf `RERANKER_MAX_DOCUMENTS` nicht überschreiten.** Sobald
`RERANK_PROVIDER=api` gesetzt ist, schickt Weave-Retrieval alle `top_k`
Kandidaten an den Rerank-Dienst. Liegt deren Zahl über dessen Obergrenze,
antwortet er mit 413 — und weil ein Reranker-Fehler abgefangen wird, fällt
**jede** Suche still auf die unrerankte Reihenfolge zurück. Sichtbar nur an
`rerank_error` im Trace. Wer `SEARCH_TOP_K` anhebt, muss
`RERANKER_MAX_DOCUMENTS` mitziehen.

Der Reranker **degradiert leise**: fällt er aus, liefert die Suche die
unrerankte Reihenfolge zurück statt zu scheitern. Das ist gewollt, heißt aber,
dass eine kaputte Konfiguration nur an schlechteren Ergebnissen auffällt — im
Trace steht `rerank_error`.

Ein **Modellwechsel bei Embeddings** macht den bestehenden Index unbrauchbar,
weil die alten Vektoren in einem anderen Raum liegen. Danach gehört ein
vollständiger Reindex dazu: `python -m app.cli reindex` in Weave-Knowledge.
Ändert sich die Dimension, braucht es vorher eine Migration.

Der Stack startet deshalb bewusst **immer** mit `fake`/`none` — auch nach
einem `git pull`, der diese beiden Modelldienste neu hinzufügt. Ein
automatischer Wechsel würde einen bestehenden Index beim nächsten
Container-Neustart still unbrauchbar machen (andere Vektoren, gleicher
Modellname), ohne dass irgendetwas das meldet. Umstellen ist daher immer eine
bewusste, hier dokumentierte Handlung.

### 7.1 Die vorgegebenen Modelle — was fastembed NICHT kann

Beide Modelldienste (`weave-embeddings`, `weave-reranker`, im Weave-Tools-Repo)
laden ein von Matze fest vorgegebenes Modell. Bevor dort Code entstand, wurde
experimentell geprüft, ob `fastembed` — die Bibliothek, mit der man CPU-Modelle
in diesem Stack normalerweise am schnellsten einbindet — sie unterstützt:

```pycon
>>> from fastembed import TextEmbedding
>>> [m['model'] for m in TextEmbedding.list_supported_models() if 'e5' in m['model']]
['intfloat/multilingual-e5-large']          # NICHT die vorgegebene -small-Variante

>>> from fastembed.rerank.cross_encoder import TextCrossEncoder
>>> [m['model'] for m in TextCrossEncoder.list_supported_models()]
['Xenova/ms-marco-MiniLM-L-6-v2', 'Xenova/ms-marco-MiniLM-L-12-v2',
 'BAAI/bge-reranker-base',                   # NICHT die vorgegebene -v2-m3-Variante
 'jinaai/jina-reranker-v1-tiny-en', 'jinaai/jina-reranker-v1-turbo-en',
 'jinaai/jina-reranker-v2-base-multilingual']
```

(gegengeprüft mit `fastembed==0.8.0`, unverändert gegenüber Weave-Tools' eigener
Dokumentation). Beide vorgegebenen Modelle fehlen — fastembed kennt aus der
e5-Familie nur das größere `-large`, und aus den Cross-Encodern nur das
kleinere `bge-reranker-base`. Ein nicht gelistetes, fest vorgegebenes Modell
wird deshalb **nicht** gegen ein von fastembed unterstütztes ersetzt, sondern
läuft über einen anderen Unterbau:

| Dienst | Modell | Unterbau (statt fastembed) | Dimension/Art | Gewichte | Grobe CPU-Laufzeit |
|---|---|---|---|---|---|
| weave-embeddings | `intfloat/multilingual-e5-small` | eigener ONNX-Export des Modells + `onnxruntime` | **384** | ~448 MiB (`onnx/model.onnx`) | ~1,3 ms/Satz (Batch 32) |
| weave-reranker | `BAAI/bge-reranker-v2-m3` | `sentence-transformers`/`torch` (CPU) | Cross-Encoder | ~2,3 GB (`model.safetensors`) | ~24–70 ms/Dokument (fällt mit Batchgröße) |

Volle Herleitung, Messmethode und die genaue Byte-Zahl in
`services/embeddings/README.md` ("Modellwahl") und
`services/reranker/README.md` ("Warum nicht fastembed").

### 7.2 Der `input_type`-Vertrag

`intfloat/multilingual-e5-small` erwartet ein Textpräfix, das sich je nach
Verwendung unterscheidet: `"query: "` vor einer Suchanfrage, `"passage: "` vor
einem indizierten Text. Zwei Modellnamen dafür (der sonst übliche Weg) sind
hier unbrauchbar: Weave-Knowledge speichert den Modellnamen pro Chunk, und
Weave-Retrieval sucht nur Chunks mit *exakt* diesem Namen — zwei Namen für
dasselbe Modell hießen, eine Suche fände den passenden Chunk nie. Deshalb
tragen beide Weave-Clients (schon vorhanden, nichts zu konfigurieren) ein
zusätzliches Feld `input_type` im Request an `weave-embeddings`:
Weave-Knowledge sendet beim Indizieren immer `"passage"`, Weave-Retrieval beim
Suchen immer `"query"` — der `model`-Name bleibt in beiden Fällen exakt
derselbe. Fehlt das Feld (jeder gewöhnliche OpenAI-Client), verhält sich
`weave-embeddings` wie `"passage"`. Details: `services/embeddings/README.md`.

### 7.3 Beide Modelldienste starten

```bash
cd deploy
# Je einen Token erzeugen -- siehe Abschnitt 4/5, nicht wiederverwenden:
openssl rand -hex 32   # -> EMBEDDINGS_API_TOKEN in .env
openssl rand -hex 32   # -> RERANKER_API_TOKEN in .env

docker compose -f docker-compose.weave.yml -f docker-compose.local.yml \
  up -d weave-embeddings weave-reranker
```

Auf `warm: true` warten, statt den reinen HTTP-Status zu vertrauen — beide
`/health`-Antworten sind während des ersten Modell-Downloads trotzdem `200`:

```bash
watch -n5 curl -s http://localhost:8006/health   # weave-embeddings
watch -n5 curl -s http://localhost:8007/health   # weave-reranker
```

`weave-embeddings` lädt beim allerersten Start ~448 MiB, `weave-reranker`
~2,3 GB — je nach Netz Sekunden bis mehrere Minuten. **Die Dimension aus
`/health` lesen, nicht hart eintragen:**

```bash
curl -s http://localhost:8006/health
# {"status":"ok","model":"intfloat/multilingual-e5-small","dimension":384,"threads":10,"warm":true}
```

Dieser `dimension`-Wert (hier: `384`) ist der, den `EMBEDDING_DIMENSION` gleich
bekommt — nicht blind der Wert aus dieser Anleitung, falls `EMBEDDINGS_MODEL`
inzwischen auf ein anderes e5-Modell (`-base`, `-large`) geändert wurde.

In `deploy/.env` ergänzen:

```bash
EMBEDDING_PROVIDER=openai
EMBEDDING_BASE_URL=http://weave-embeddings:8000
EMBEDDING_API_KEY=<== derselbe Wert wie EMBEDDINGS_API_TOKEN oben>
EMBEDDING_MODEL=intfloat/multilingual-e5-small
EMBEDDING_DIMENSION=384                          # aus GET /health gelesen, siehe oben

RERANK_PROVIDER=api
RERANK_BASE_URL=http://weave-reranker:8000
RERANK_API_KEY=<== derselbe Wert wie RERANKER_API_TOKEN oben>
RERANK_MODEL=BAAI/bge-reranker-v2-m3
```

Ab hier trennt sich der Weg — je nachdem, ob `weave_knowledge` schon Inhalt hat.

### 7.4 Embeddings umstellen — leere Datenbank

"Leer" heißt hier konkret: **noch nie migriert** (frisches `pgdata`-Volume,
oder eine gerade erst angelegte `weave_knowledge`-Datenbank). Das ist NICHT
dasselbe wie "migriert, aber noch keine Dokumente" — dieser Fall gehört in
7.5, auch ohne eine einzige Zeile Daten. Prüfen:

```bash
docker compose -f docker-compose.weave.yml exec postgres \
  psql -U weave -d weave_knowledge -c '\dt'
# "Did not find any relations." -> wirklich leer, weiter unten in diesem Abschnitt.
# Zeilen wie "documents", "chunks" -> Abschnitt 7.5, auch wenn beide 0 Zeilen haben.
```

Ist die Datenbank wirklich leer, genügt ein normaler (Neu-)Start — kein
Handgriff sonst nötig:

```bash
docker compose -f docker-compose.weave.yml -f docker-compose.local.yml \
  up -d weave-knowledge weave-knowledge-worker weave-retrieval
```

Warum das reicht: `entrypoint.sh` führt bei jedem Containerstart automatisch
`alembic upgrade head` aus, bevor uvicorn startet
(`RUN_ALEMBIC_ON_STARTUP=true`, Vorgabe). Für eine frische Datenbank läuft
dabei `0001_init.py` zum ersten Mal — und diese Migration liest
`settings.embedding_dimension` (also die gerade gesetzte `EMBEDDING_DIMENSION`)
in genau diesem Moment und legt `chunks.embedding` direkt als `vector(384)`
an (siehe `services/knowledge/backend/alembic/versions/0001_init.py`). Es gibt
nichts Vorhandenes zu reindizieren. Weiter mit der Gegenprobe (7.6).

### 7.5 Embeddings umstellen — befüllte Datenbank

Gilt auch, wenn `weave_knowledge` bereits migriert ist, aber noch 0 Dokumente
enthält — die Spaltenbreite steckt bereits im Schema, nicht in den Daten.

pgvector kann die Breite einer bestehenden `vector(N)`-Spalte nicht per
`ALTER ... TYPE` ändern, ohne die alten Werte zu verwerfen — es gibt keinen
sinnvollen Cast von `vector(1536)` (oder was auch immer die vorherige
Attrappen-/Provider-Dimension war) nach `vector(384)`. Alte Vektoren sind
ohnehin wertlos (anderes Modell, anderer Vektorraum), also braucht es eine
neue Migration, die die Spalte neu anlegt, statt zu casten:

```bash
cd services/knowledge/backend
# Ueber das eigene .venv dieses Repos, nicht global installiert -- ohne
# --autogenerate braucht keiner der beiden Befehle eine DB-Verbindung, nur
# den Inhalt von alembic/versions/.
.venv/bin/alembic heads          # aktuelle Revision -- als down_revision unten eintragen
.venv/bin/alembic revision -m "widen chunks.embedding to 384 dimensions"
```

Den erzeugten Dateiinhalt unter `alembic/versions/<hash>_widen_chunks_...py`
durch Folgendes ersetzen (`down_revision` auf den Wert aus `alembic heads`
setzen, `NEW_DIMENSION` auf den aus `/health` gelesenen Wert):

```python
"""widen chunks.embedding to a new model's dimension (invalidates existing vectors)"""

from alembic import op

revision = "<neue id, wie von alembic vergeben>"
down_revision = "0003_collections"   # <- durch den echten Wert aus `alembic heads` ersetzen
branch_labels = None
depends_on = None

NEW_DIMENSION = 384  # muss exakt EMBEDDING_DIMENSION aus der .env entsprechen


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite speichert embedding als JSON -- keine Spaltenbreite anzupassen

    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    # Kein Cast zwischen unterschiedlichen vector(N)-Breiten moeglich --
    # bestehende Werte sind ohnehin aus dem falschen Modell, also NULL statt
    # eines fehlschlagenden impliziten Casts.
    op.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({NEW_DIMENSION}) USING NULL")
    op.create_index(
        "ix_chunks_embedding_hnsw", "chunks", ["embedding"],
        postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # embedding_model zuruecksetzen, damit kein Chunk/Dokument einen alten
    # Modellnamen neben einem NULL-Vektor stehen hat -- der reindex-Schritt
    # unten setzt beides ohnehin neu.
    op.execute("UPDATE chunks SET embedding_model = NULL")
    op.execute("UPDATE documents SET embedding_model = NULL")


def downgrade() -> None:
    raise NotImplementedError("kein automatisches Downgrade -- aus Backup wiederherstellen")
```

Danach, in dieser Reihenfolge:

```bash
# 1. Bei lokalem Build: Image neu bauen, damit die neue Migrationsdatei
#    mit hineinkommt. Bei einem Registry-Deployment: neues Image mit
#    dieser Datei bauen/pushen und WEAVE_KNOWLEDGE_TAG entsprechend setzen.
cd deploy
docker compose -f docker-compose.weave.yml -f docker-compose.local.yml \
  up -d --build weave-knowledge weave-knowledge-worker weave-retrieval
# `up -d` startet die Container neu (neue .env-Werte aus 7.3 UND die neue
# Migration greifen erst dadurch) -- entrypoint.sh fuehrt `alembic upgrade
# head` automatisch aus, BEVOR uvicorn wieder Verbindungen annimmt.

# 2. Migration bestaetigen:
docker compose -f docker-compose.weave.yml exec weave-knowledge alembic current
docker compose -f docker-compose.weave.yml exec postgres \
  psql -U weave -d weave_knowledge -c '\d chunks'
# erwartet: "embedding | vector(384)"

# 3. Reindex -- embedet ALLE Chunks jedes Dokuments mit status=indexed neu,
#    mit dem jetzt konfigurierten Provider (kein --only-model noetig, die
#    Migration hat embedding_model oben bereits ueberall auf NULL gesetzt):
docker compose -f docker-compose.weave.yml exec weave-knowledge \
  python -m app.cli reindex
```

`reindex` bricht bei einem einzelnen fehlgeschlagenen Dokument nicht ab
(zählt es nur als Fehler und macht weiter) und beendet sich mit Exit-Code
`!= 0`, wenn am Ende mindestens eines fehlgeschlagen ist — für ein Skript/CI
prüfbar, siehe `services/knowledge/backend/app/cli.py`.

### 7.6 Gegenprobe

```bash
curl -s -X POST http://localhost:8002/api/v1/search \
  -H "Authorization: Bearer $RETRIEVAL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query": "<ein Satz aus einem bereits indizierten Dokument>"}'
```

Erwartet: mindestens ein Treffer mit `scores.vector` als Zahl ungleich `null`
(nicht nur `scores.fulltext`) — das beweist, dass die Vektorsuche tatsächlich
Kandidaten liefert, nicht nur der Volltextpfad durchträgt (siehe Abschnitt 6,
"`EMBEDDING_MODEL` — die Suche findet nie etwas"). Reranker separat prüfen:
mit `RERANK_PROVIDER=api` sollte `scores.rerank` gesetzt sein und
`trace.rerank_error` `false`; steht es auf `true`, siehe Abschnitt 4/5 für die
`RERANK_API_KEY`/`RERANKER_API_TOKEN`-Prüfung.

---

## 8. SSO einrichten

| Variable | Bedeutung |
|---|---|
| `OIDC_ISSUER` | Basis-URL des Providers. Zusammen mit der Client-ID schaltet sie OIDC frei |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | Zugangsdaten der registrierten Anwendung |
| `OIDC_REDIRECT_URL` | Muss beim Provider zeichengleich hinterlegt sein |
| `OIDC_TEAM_CLAIM` | Aus welchem Claim das Team gelesen wird. Leer heißt: Nutzer ohne Team und damit ohne lesbare Collections |
| `OIDC_POST_LOGIN_ALLOWED_URLS` | Erlaubte Rücksprungziele |

Nach erfolgreichem Login hängt das Gateway einen **einmaligen Übergabe-Code** an
die Rücksprung-Adresse. Wer dort einen fremden Eintrag unterbringt, bekommt
diesen Code und damit eine gültige Sitzung — deshalb gehört in
`OIDC_POST_LOGIN_ALLOWED_URLS` ausschließlich, was du selbst kontrollierst. Die
Prüfung vergleicht Schema, Host und Port exakt und lehnt Backslashes,
Steuerzeichen und kodierte Pfadsprünge ab, weil Browser URLs anders lesen als
Python.

Konten werden **ausschließlich über den OIDC-Subject** verknüpft, nie über die
E-Mail-Adresse: sonst könnte sich jemand mit derselben Adresse bei einem anderen
Provider auf ein bestehendes Konto aufschalten.

---

## 9. n8n und MCP

Beide bekommen exakt die Leserechte des Menschen, der gerade fragt — über ein
kurzlebiges, signiertes Delegations-Token mit eingebettetem Collection-Umfang.

| Variable | Dienst | Bedeutung |
|---|---|---|
| `WEAVE_DELEGATION_SECRET` | Runtime, Tools | Signiert und prüft das Token. Zeichengleich, sonst geht nichts |
| `DELEGATION_TOKEN_TTL_SECONDS` | Runtime | Gültigkeit, Vorgabe 300 Sekunden |
| `TOOLS_BASE_URL` | Runtime | Wird dem Flow mitgegeben, damit er zurückfindet |
| `N8N_ALLOWED_BASE_URLS` | Runtime | Erlaubte Webhook-Adressen. Leer heißt: n8n-Bots abgeschaltet |
| `TOOLS_API_TOKEN` | Tools | Muss im Flow als eigene Zugangsdaten hinterlegt werden — ein Service-Geheimnis gehört nicht in einen Payload |

Die Regel dahinter: Der erlaubte Umfang wird immer serverseitig aus der
Identität abgeleitet. Ein Collection-Name aus einem Tool-Argument oder einer
Modell-Ausgabe kann nur einschränken, nie erlauben. Und was ein Flow als Quelle
zurückmeldet, ist eine Behauptung — Weave prüft jede gemeldete Quelle gegen den
signierten Umfang und verwirft, was nicht passt (im Trace als `dropped_sources`).

Die Webhook-Allowlist greift **beim Laden** der Bot-Datei, nicht beim ersten
Aufruf: eine Konfigurationsdatei soll den Dienst nicht in beliebige Netze rufen
lassen können.

---

## 10. Selbsttest

```bash
# 1 — Alle Dienste erreichbar
for p in 8001 8002 8003 8004 8005; do curl -s localhost:$p/health; done
curl -s localhost:8000/api/v1/health   # Ingest hat einen eigenen Pfad

# 2 — Gateway erreicht die Runtime (leere Liste = Bots werden nicht geladen)
curl -s -H "Authorization: Bearer $TOKEN" localhost:8004/v1/bots

# 3 — Was darf dieser Nutzer lesen?
curl -s -H "Authorization: Bearer $TOKEN" localhost:8004/v1/collections

# 4 — Eine echte Antwort samt Trace
curl -s -X POST localhost:8004/v1/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"bot_id":"general-assistant","message":"Hallo"}'
```

Der `trace` sagt, wo es klemmt: `intent: conversational` bei einer Wissensfrage
heißt, der Router hat sie nicht erkannt. `guard.reason: no_collections` heißt,
der Nutzer hat kein Team oder keine freigegebene Collection.
`retrieval.candidates: 0` bei vorhandenen Dokumenten heißt fast immer, dass die
Embedding-Einstellungen von Knowledge und Retrieval nicht zusammenpassen.

| Symptom | Wahrscheinliche Ursache |
|---|---|
| Chat antwortet `502` | `RUNTIME_API_TOKEN` weicht zwischen API und Runtime ab |
| Bot findet nie etwas | Embedding-Modell oder -Provider unterscheiden sich |
| Antworten beginnen mit `[fake-llm]` | `LLM_PROVIDER` steht noch auf `fake` |
| Dokumente erscheinen nicht im Index | Webhook-Verbindung fehlt oder Secret weicht ab (Knowledge-Log zeigt 401) |
| Collections bleiben leer | Registry-Sync scheitert — der Ingest-Token gehört keinem Administrator |
| MCP antwortet `503` | `WEAVE_DELEGATION_SECRET` oder `TOOLS_API_TOKEN` nicht gesetzt |
| Login schlägt mit `403` fehl | Frontend-Adresse fehlt in `CORS_ORIGINS` |
| SSO-Routen antworten `404` | Nur eines von Issuer und Client-ID gesetzt |

---

## 11. Deklarative Konfiguration (`weave.yaml`)

Die Abschnitte 4 und 5 oben sind von Hand erhoben — jemand hat die neun
`config.py`/`.env.example`-Dateien gelesen und in eine Tabelle übertragen.
`weave.yaml` im Repo-Wurzelverzeichnis macht diese Tabelle maschinenlesbar
und **erzeugt** daraus `deploy/.env`, statt dass sie nur Referenz für
Handarbeit bleibt: Handarbeit an der `.env`-Datei entfällt damit vollständig.

Die Datei kennt zwei Blöcke:

- **`shared`** — Werte, die mehrere Dienste **zeichengleich** brauchen: die
  sieben aus Abschnitt 5 (Delegations-Secret, Introspection-Token,
  Retrieval-Token, Runtime-Token, Webhook-Secret, sowie Embedding-Modell und
  -Dimension als eigene Zeile), die zwei bedingten Paare
  (`EMBEDDING_API_KEY`/`EMBEDDINGS_API_TOKEN`,
  `RERANK_API_KEY`/`RERANKER_API_TOKEN`), plus die Postgres-/Redis-
  Zugangsdaten, die zwar nicht in Abschnitt 5 stehen, aber strukturell
  genauso geteilt sind. Jeder Eintrag trägt einen Kommentar, wer den Wert
  braucht und was bei Abweichung passiert — dieselbe Information wie in
  Abschnitt 5, jetzt am Wert selbst statt in einer separaten Tabelle.
- **`services`** — je Dienst dessen eigene Einstellungen (Ports, Timeouts,
  Provider-Defaults, OIDC, …).

Geheimnisse stehen **nie im Klartext** in `weave.yaml`: ein Eintrag mit
`secret: true` trägt nur den Namen einer Umgebungsvariable (`env_var:`), aus
der `render`/`check` den tatsächlichen Wert zur Laufzeit lesen. Die Datei
selbst darf bedenkenlos committet werden.

```bash
# Einmalig: jedes Pflicht-Geheimnis als Umgebungsvariable setzen (direnv,
# Passwort-Manager, `export $(cat secrets.env | xargs)`, ein CI-Secret-Store
# — wie, ist egal). Ohne einen der elf nennt `render` alle fehlenden
# Variablen auf einmal und bricht ab, statt eine Teil-Datei zu schreiben:
export WEAVE_POSTGRES_PASSWORD=... WEAVE_REDIS_PASSWORD=... \
       WEAVE_RETRIEVAL_DB_PASSWORD=... WEAVE_DELEGATION_SECRET=... \
       INTROSPECTION_SERVICE_TOKEN=... RETRIEVAL_API_TOKEN=... \
       RUNTIME_API_TOKEN=... WEAVE_KNOWLEDGE_WEBHOOK_SECRET=... \
       WEAVE_INGEST_SECRET_KEY=... WEAVE_KNOWLEDGE_INGEST_API_TOKEN=... \
       TOOLS_API_TOKEN=...

python scripts/weave_config.py render
# -> schreibt deploy/.env. Jeder geteilte Wert wird genau einmal berechnet
#    und in jede betroffene Variable gespiegelt — bei zwei unterschiedlich
#    benannten Zielen (z.B. EMBEDDING_API_KEY vs. EMBEDDINGS_API_TOKEN)
#    ausdrücklich als zwei Zeilen mit demselben Wert, nie als zwei separat
#    einzutragende Werte.

python scripts/weave_config.py check
# -> prüft eine bestehende .env (Default: deploy/.env) auf genau die
#    Widersprüche aus Abschnitt 4-7:
#      - fehlende Pflichtwerte (klar benannt: welcher Dienst, welche Variable)
#      - geteilte Werte, die auseinanderlaufen (Fingerabdrücke statt
#        Klartext in der Ausgabe — sicher genug, um sie in einen Chat oder
#        ein Ticket zu kopieren)
#      - SEARCH_TOP_K > RERANKER_MAX_DOCUMENTS, sobald RERANK_PROVIDER=api
#      - eine EMBEDDING_DIMENSION, die nicht zum konfigurierten Modell passt
#    Exit-Code ungleich 0 bei jedem Befund.
```

`check` lässt sich mit wiederholtem `--service-env DIENST=PFAD` auch gegen
einzelne, von Hand gepflegte `.env`-Dateien richten — für den Betrieb ohne
docker compose (jeder Dienst als eigener `uvicorn`-Prozess mit seiner
eigenen `.env`, siehe die `.env.example`-Kopfzeilen der einzelnen Dienste),
wo Werte am ehesten unbemerkt auseinanderlaufen, weil jeder Prozess seine
eigene Datei liest statt derselben `deploy/.env`.

Tests unter `scripts/tests/` (reine Standardbibliothek + PyYAML + pytest,
keine Abhängigkeit zu einem der neun Dienste):

```bash
services/tools/.venv/bin/python -m pytest scripts/tests -q
```
