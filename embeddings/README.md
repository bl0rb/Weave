# Weave-Embeddings

OpenAI-kompatibler Embedding-Server auf CPU fuer `intfloat/multilingual-e5-small` -- ein neuer, eigenstaendiger Dienst in diesem Repo (`Weave-Tools/embeddings/`, neben `backend/`, `frontend/` und `reranker/`), mit eigenem `.venv` und eigenem Deployment. Weave-Knowledge bettet damit Chunks ein (`EMBEDDING_PROVIDER=openai`, siehe dessen `backend/app/services/embeddings.py`), Weave-Retrieval bettet damit Suchanfragen ein -- beide sprechen exakt dasselbe `/v1/embeddings` wie gegen die echte OpenAI-API.

## Modellwahl

Das Modell ist vom Nutzer fest vorgegeben: `intfloat/multilingual-e5-small`, 384 Dimensionen. Bevor irgendein Code entstand, wurde experimentell geprueft, ob und wie fastembed (die Bibliothek, die der Nachbardienst `reranker/` in diesem Repo nutzt) dieses Modell unterstuetzt:

```pycon
>>> from fastembed import TextEmbedding
>>> [m['model'] for m in TextEmbedding.list_supported_models() if 'e5' in m['model']]
['intfloat/multilingual-e5-large']
```

**Ergebnis:** fastembed==0.8.0 listet 30 Dense-Text-Modelle; `intfloat/multilingual-e5-small` ist unter keinem Namen und keinem Praefix dabei -- aus der e5-Familie kennt es nur das groessere `intfloat/multilingual-e5-large` (1024 Dimensionen, 2.24GB). Die Aufgabenstellung ist hier eindeutig: ein nicht gelistetes, fest vorgegebenes Modell wird NICHT stillschweigend gegen ein von fastembed unterstuetztes ersetzt (z.B. e5-large oder `paraphrase-multilingual-MiniLM-L12-v2`), sondern weicht fuer genau dieses eine Modell auf sentence-transformers oder optimum/ONNX aus.

**Warum ONNX direkt, nicht `optimum`:** `optimum[onnxruntime]` wurde probeweise in dieses `.venv` installiert, um zu pruefen, ob es ohne `torch` auskommt (dieser Dienst soll CPU-only und schlank bleiben). Ergebnis: `optimum-onnx==0.1.0` zieht `torch==2.13.0` (111MB Wheel) als transitive Abhaengigkeit -- selbst fuer den reinen Inferenz-Pfad eines bereits fertig exportierten ONNX-Modells, ganz ohne eigenen Export-Schritt. `sentence-transformers` hat dasselbe Problem (baut selbst auf `transformers`+`torch` auf). Ein CPU-only-Embedding-Server, der wegen einer Tokenizer-Klasse und eines Config-Objekts eine 700MB+ schwere Torch-Installation ins Image zieht, widerspricht dem eigenen Auftrag dieses Dienstes -- zumal der Nachbardienst `reranker/` in diesem Repo genau ohne Torch auskommt (fastembeds eigener Unterbau ist onnxruntime+tokenizers, kein transformers/torch).

Deshalb laedt `app/services/encoder.py` stattdessen `intfloat/multilingual-e5-small`s **eigenen**, von intfloat selbst im HF-Repo veroeffentlichten ONNX-Export (`onnx/model.onnx` + `onnx/tokenizer.json` -- keine Drittanbieter-Konvertierung) und fuehrt ihn direkt ueber `onnxruntime` aus, mit demselben Mean-Pooling-dann-L2-Normalisieren, das fastembed intern fuer jedes BERT-Familie-Modell in seiner eigenen Liste macht (siehe fastembeds `PoolingType.MEAN` und dessen eigene "now uses mean pooling instead of CLS embedding"-Warnung fuer `intfloat/multilingual-e5-large`). Architektonisch ist das exakt das, was fastembed selbst taete, haette es einen Registry-Eintrag fuer dieses eine Modell.

### Belegte Fakten (Findings)

| | |
|---|---|
| Exakter Modellname | `intfloat/multilingual-e5-small` (HF-Repo, `onnx/`-Unterordner) |
| In fastembed 0.8.0 gelistet? | **Nein** -- nur `intfloat/multilingual-e5-large` aus der e5-Familie |
| Architektur | `BertModel`, 12 Layer, `hidden_size=384`, XLM-RoBERTa-Tokenizer (Vokabular 250k) |
| Dimension | **384** (empirisch aus `last_hidden_state`-Shape und per Encode-Aufruf verifiziert) |
| Dateigroesse (`onnx/model.onnx`, fp32) | 470.268.510 Bytes (~448 MiB) |
| Grobe CPU-Laufzeit (Apple-Silicon-Laptop, 10 Threads) | Einzelner kurzer Satz: ~5ms; Batch von 32 kurzen Saetzen: ~40ms gesamt (~1.3ms/Satz) |
| Erster Modell-Load inkl. Download (warmer HF-Cache) | ~0.7s; ohne Cache dominiert die Downloadzeit (~448 MiB) |

Zum Vergleich (nur zur Einordnung, nicht Teil dieses Diensts): das ebenfalls vom Nutzer vorgegebene Rerank-Modell `BAAI/bge-reranker-v2-m3` ist in fastembeds `TextCrossEncoder.list_supported_models()` (0.8.0) **ebenfalls nicht** gelistet -- dort existiert nur das kleinere, aeltere `BAAI/bge-reranker-base`. Der Rerank-Dienst selbst ist nicht Teil dieser Aenderung (siehe das separate Verzeichnis `reranker/` in diesem Repo).

## Der `input_type`-Vertrag

e5-Modelle sind mit einem festen Text-Praefix trainiert: `"query: "` vor eine Suchanfrage, `"passage: "` vor einen indexierten Text (siehe das Modell-Readme auf HuggingFace). fastembed macht das selbst fuer keines seiner Modelle automatisch -- auch nicht fuer `intfloat/multilingual-e5-large`, das einzige e5-Modell, das es kennt: `TextEmbeddingBase.query_embed()`/`.passage_embed()` (fastembed/text/text_embedding_base.py) sind reine, modell-unabhaengige Wrapper um `.embed()`, ohne jede Praefix-Logik. Selbst haette fastembed `multilingual-e5-small` gelistet, muessten die Praefixe also von Hand ergaenzt werden.

Zwei separate Modellnamen fuer Anfrage/Passage (der uebliche Weg) ist hier verboten: Weave-Knowledge speichert den Modellnamen pro Chunk (`Chunk.embedding_model`), und Weave-Retrieval filtert Suchen exakt auf `Chunk.embedding_model == settings.embedding_model` (String-Gleichheit, siehe dessen `app/services/search.py`). Zwei Namen wuerden bedeuten: die Suche findet nie etwas.

Deshalb traegt der Request-Body ein zusaetzliches, optionales Feld:

```jsonc
{"model": "intfloat/multilingual-e5-small", "input": "...", "input_type": "query"}   // oder "passage"
```

- **Fehlt `input_type`:** verhaelt sich exakt wie `"passage"` -- ein gewoehnlicher OpenAI-Client, der dieses Feld nicht kennt, funktioniert unveraendert.
- **Der Modellname bleibt in Anfrage und Antwort exakt derselbe** -- nie umbenannt, egal welcher `input_type`.
- Die genaue Zeichenkette (`"query: "` bzw. `"passage: "`) und WO sie angeklebt wird, steht dokumentiert im Docstring von `app/services/encoder.py`.

Empirisch bestaetigt (siehe `tests/test_real_model.py`, gegen das echte ONNX-Modell): derselbe Text mit `input_type=query` und `input_type=passage` ergibt unterschiedliche, aber verwandte Vektoren (Cosine ~0.91-0.93 in Stichproben) -- nie identisch, nie voellig unverwandt.

## API

### `POST /v1/embeddings`

Feldgleich zu dem, was Weave-Knowledges `OpenAICompatibleProvider` tatsaechlich sendet/parst:

```bash
curl -s http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer $EMBEDDINGS_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "intfloat/multilingual-e5-small",
        "input": ["Wie stelle ich den Drucker ein?", "Ein zweiter Text."],
        "input_type": "passage"
      }'
```

```jsonc
{
  "object": "list",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, ...]},
    {"object": "embedding", "index": 1, "embedding": [-0.0456, ...]}
  ],
  "model": "intfloat/multilingual-e5-small",
  "usage": {"prompt_tokens": 17, "total_tokens": 17}
}
```

`input` akzeptiert einen einzelnen String oder eine Liste (Antwort hat dann entsprechend 1 bzw. N Eintraege in `data`, `index` immer gleich der Position im Request). Mehr als `EMBEDDINGS_MAX_INPUTS` Eintraege in der Liste -> `413`. Ein `model`, das nicht dem geladenen Modell entspricht -> `404`.

### `GET /v1/models`

```json
{"object": "list", "data": [{"id": "intfloat/multilingual-e5-small", "object": "model", "owned_by": "weave-tools"}]}
```

### `GET /health` (kein Auth-Header noetig)

```json
{"status": "ok", "model": "intfloat/multilingual-e5-small", "dimension": 384, "threads": 10, "warm": true}
```

**`dimension: 384` ist der Wert, den Weave-Knowledge und Weave-Retrieval als `EMBEDDING_DIMENSION` (bzw. die Spaltenbreite ihrer `vector`-Spalte) brauchen.** Vor dem ersten erfolgreichen Laden ist `dimension: null` und `warm: false`, `status: "starting"` -- die HTTP-Antwort selbst ist trotzdem immer `200` (siehe naechster Abschnitt).

## Erster Start / Startverhalten

Der erste Start eines frischen Deployments laedt das ONNX-Modell (~448 MiB) von HuggingFace herunter -- je nach Netzwerk Sekunden bis wenige Minuten. Das passiert NICHT blockierend: `app/main.py`s `lifespan` startet einen Hintergrund-Thread fuer `build_embedder()` und laesst uvicorn sofort Verbindungen annehmen. `GET /health` antwortet die ganze Zeit sofort mit `200` (`warm: false`, `status: "starting"`), `POST /v1/embeddings` antwortet in diesem Fenster mit `503`.

**Das ist bewusst so gebaut, damit ein Healthcheck keine Neustart-Schleife ausloest:** Ein Healthcheck (Docker `HEALTHCHECK`, Kubernetes-Readiness-/Liveness-Probe), der nur pruefen soll "antwortet der Prozess ueberhaupt", sieht nie einen blockierten Port -- auch nicht waehrend eines langsamen Erst-Downloads. Braucht eine Bereitstellung tatsaechliche Bereitschaft (nicht nur Liveness), pollt sie `warm`/`status` aus der `/health`-Antwort selbst, statt sich auf den HTTP-Statuscode zu verlassen. Das mitgelieferte `Dockerfile` setzt zusaetzlich eine grosszuegige `--start-period=300s` als weitere Absicherung.

Modell-Cache ueberlebt einen Container-Neustart nur, wenn `EMBEDDINGS_CACHE_DIR` (Default im Image: `/models`) als Volume gemountet ist -- das Modell wird NIE ins Image selbst gebacken.

## Modell wechseln

`EMBEDDINGS_MODEL` in `.env` (bzw. als Umgebungsvariable) auf einen anderen HF-Repo-Namen setzen und den Prozess neu starten. Funktioniert unveraendert fuer jedes Modell, das demselben Aufbau wie `intfloat/multilingual-e5-small` folgt -- ein BERT-Architektur-Encoder mit `last_hidden_state`-Output unter einem `onnx/`-Unterordner, XLM-RoBERTa/BERT-artiger `tokenizer.json`, und derselben `"query: "`/`"passage: "`-Praefixkonvention (z.B. `intfloat/multilingual-e5-base` oder `-large`, dann aber mit 768 bzw. 1024 Dimensionen -- `EMBEDDING_DIMENSION` in Weave-Knowledge/Weave-Retrieval muss entsprechend mitziehen, und bereits eingebettete Chunks bleiben unter dem alten Modellnamen liegen, siehe deren Reindex-Mechanismus). Ein strukturell anderes Modell (andere Pooling-Strategie, kein `onnx/`-Export vorhanden, kein Praefix-Schema) braucht Code-Aenderungen in `app/services/encoder.py`, nicht nur diese Variable.

## Konfiguration

Siehe `.env.example` fuer alle Variablen mit ausfuehrlicher Begruendung. Kurzfassung:

| Variable | Default | Bedeutung |
|---|---|---|
| `EMBEDDINGS_MODEL` | `intfloat/multilingual-e5-small` | HF-Repo-Name des Modells |
| `EMBEDDINGS_API_TOKEN` | *(leer -> 503)* | Bearer-Token fuer `/v1/embeddings` und `/v1/models` |
| `EMBEDDINGS_BATCH_SIZE` | `32` | Interne onnxruntime-Batchgroesse pro Request |
| `EMBEDDINGS_THREADS` | `0` (= automatisch) | onnxruntime Intra-Op-Threads |
| `EMBEDDINGS_CACHE_DIR` | *(leer -> HF-Default)* | Wohin Modelldateien gecacht werden (im Image: `/models`) |
| `EMBEDDINGS_MAX_INPUTS` | `256` | Max. Eintraege in `input`, sonst `413` |
| `EMBEDDINGS_NORMALIZE` | `true` | Ob Vektoren L2-normalisiert zurueckgegeben werden |

## Entwicklung

```bash
# Einmalig: virtuelle Umgebung + Abhaengigkeiten (python3.12)
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# .env anlegen
cp .env.example .env   # EMBEDDINGS_API_TOKEN setzen

# Starten
.venv/bin/python -m uvicorn app.main:app --port 8000

# Tests (schnell, gegen einen gemockten Encoder -- siehe naechster Abschnitt)
.venv/bin/python -m pytest tests -q

# Tests gegen das ECHTE ONNX-Modell (optional, laedt/laeuft das echte Modell)
RUN_REAL_MODEL_TESTS=1 .venv/bin/python -m pytest tests/test_real_model.py -v
```

### Warum ein gemockter Encoder in der Standard-Suite

`tests/conftest.py`s `FakeEmbedder` ist Weave-Knowledges eigenem `FakeEmbeddingProvider` nachgebaut (sha256-geseedete Zufallsvektoren, deterministisch, kein Netzwerk). `intfloat/multilingual-e5-small` ist bereits das kleinste sinnvolle Modell seiner Familie -- es gibt keine noch kleinere Variante, auf die man fuer Tests eigenmaechtig ausweichen duerfte, ohne die "kein anderes Modell"-Vorgabe zu verletzen. Es bei jedem Testlauf herunterzuladen und durch echtes ONNX-Inferencing zu schicken waere langsam, netzwerkabhaengig und in CI nicht hermetisch. Die Standard-Suite (`tests/test_*.py`, ausgenommen `test_real_model.py`) prueft deshalb den kompletten HTTP-Vertrag (Formfelder, Auth, Reihenfolge/Index ueber Batch-Grenzen, Limits, `input_type`-Weiterleitung) gegen den Fake; `tests/test_real_model.py` (opt-in per `RUN_REAL_MODEL_TESTS=1`) verifiziert einmalig gegen das echte Modell: Dimension 384, dass `query`- und `passage`-Praefixe tatsaechlich unterschiedliche Vektoren erzeugen, dass normalisierte Vektoren Norm 1 haben, und dass Padding das Ergebnis nicht veraendert (Mean-Pooling ueber die Attention-Maske).

**Layout** (`app/`): `core` (Settings, Auth-Dependency), `services` (`encoder.py` -- ONNX-Laden + Inferenz, `state.py` -- Prozessweiter warm/embedder-Zustand), `schemas` (Pydantic-Request-/Response-Modelle), `main.py` (FastAPI-App, `/health`, `/v1/models`, `/v1/embeddings`).
