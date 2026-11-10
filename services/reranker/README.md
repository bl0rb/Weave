# Weave-Reranker

Eigenstaendiger Rerank-Server auf CPU fuer einen ONNX-Cross-Encoder
(Standard: `Alibaba-NLP/gte-multilingual-reranker-base` als int8-Export
`onnx-community/gte-multilingual-reranker-base`) -- die
eine Instanz, gegen die Weave-Retrievals `HttpReranker` (provider `'api'`,
`backend/app/services/reranker.py` in diesem Repo) spricht, wenn ein
Deployment echtes Reranking statt der unrerankten RRF-Reihenfolge will.

## Der Vertrag (aus Weave-Retrievals HttpReranker abgeschrieben)

`backend/app/services/reranker.py`'s `HttpReranker` wurde NUR gelesen, nicht
veraendert -- das folgende ist Feld fuer Feld daraus uebernommen:

- **Pfad**: `POST {base_url}/rerank`
- **Auth**: Header `Authorization: Bearer {api_key}`
- **Anfrage-Koerper**:
  ```json
  {
    "model": "<name>",
    "query": "<text>",
    "documents": ["<text>", "..."],
    "top_n": <int>
  }
  ```
  `HttpReranker` schickt `top_n` IMMER als `len(documents)` -- nie kleiner --
  weil das `Reranker`-Protokoll verspricht, jeden Kandidaten zurueckzugeben,
  nur neu sortiert. Diese Implementierung respektiert `top_n` trotzdem
  generisch (siehe unten), falls ein anderer Cohere/Jina-kompatibler Caller
  bewusst eine kleinere Teilmenge will.
- **Erfolgsantwort (200)**:
  ```json
  {"results": [{"index": 0, "relevance_score": 0.97}, "..."]}
  ```
  `index` referenziert die Position in `documents`, nicht neu sortiert.
  `HttpReranker` sortiert selbst nach `relevance_score` absteigend und
  verlangt GENAU einen Eintrag pro eingereichtem Dokument -- sonst wirft es
  `RerankError`. Dieser Server liefert also, obwohl der Client ohnehin
  selbst sortiert, ebenfalls absteigend sortierte Ergebnisse (Punkt 4 der
  Aufgabe) und bei Standard-`top_n` (= `len(documents)`) exakt einen
  Treffer pro Dokument, damit das Invariant von `HttpReranker` nie verletzt
  wird.
- **Fehlerverhalten, das `HttpReranker` erwartet**: ein 429 oder 5xx wird
  von IHM mit Backoff wiederholt (bis zu 3 Versuche gesamt), jeder andere
  4xx sofort als `RerankError` durchgereicht. Dieser Server haelt sich
  daran: 401/413/422 sind absichtlich NICHT retryable (der Request aendert
  sich beim Wiederholen nicht), 503 ("Modell laedt noch" oder "Service
  nicht konfiguriert") faellt unter die generischen 5xx-Klasse und darf
  von einem Client mit Backoff wiederholt werden.

## Modellwahl und Laufzeit

Der Dienst laedt eine ONNX-Datei und die `tokenizer.json` per
`hf_hub_download` und rechnet mit `onnxruntime` + `tokenizers` auf der CPU
-- dasselbe Muster wie `services/embeddings`, ohne torch,
transformers oder sentence-transformers. Der eine Logit pro Paar wird per
Sigmoid auf 0..1 abgebildet, wie es `CrossEncoder` vorher tat.

Standard ist `Alibaba-NLP/gte-multilingual-reranker-base` (306M Parameter,
Apache-2.0, mehrsprachig inkl. Deutsch, von TEI auf CPU unterstuetzt). Das
Original-Repo enthaelt nur safetensors und braucht `trust_remote_code`,
deshalb kommt die Datei aus dem Export `onnx-community/gte-multilingual-
reranker-base`. `RERANKER_ONNX_FILE` waehlt die Variante:

| Datei | Groesse |
|---|---:|
| `onnx/model_int8.onnx` (Standard) | ~341 MB |
| `onnx/model.onnx` (fp32) | ~1,26 GB |

Jedes andere Cross-Encoder-Repo mit einem ONNX-Graphen, der einen Logit
pro Paar liefert, und einer `tokenizer.json` im Wurzelverzeichnis
funktioniert ebenso, z. B. das bisherige Modell als
`RERANKER_MODEL=onnx-community/bge-reranker-v2-m3-ONNX` (int8 ~571 MB).

Python-Pakete: ~250 MB statt ~1,5 GB mit torch (gemessen im venv).

Gemessen in einer 4-vCPU-Xeon-Sandbox (2,1 GHz, `RERANKER_THREADS=4`,
`RERANKER_BATCH_SIZE=16`), 20 deutsche Dokumente mit ~140 Tokens je Paar,
Median aus 7 Laeufen. Absolutwerte sind auf euren Nodes anders, die
Verhaeltnisse tragen:

| Variante | 20 Dokumente | Peak-RSS |
|---|---:|---:|
| gte int8 (Standard) | ~0,86 s | ~1,5 GB |
| gte fp32 | ~2,1 s | ~2,5 GB |
| bge-reranker-v2-m3 int8 | ~1,6 s | ~1,4 GB |

Ein Cross-Encoder bewertet jedes Query/Dokument-Paar EINZELN; die Kosten
wachsen linear mit der Dokumentanzahl.

**Scores verschieben sich beim Modellwechsel.** gte vergibt unpassenden
Dokumenten deutlich hoehere Werte als bge-reranker-v2-m3 (in obigem
Testsatz im Mittel ~0,27 statt ~0,02). Wer in Bots `min_score`,
`clarify_below` oder `weak_context_below` gesetzt hat, muss diese Schwellen
neu einstellen.

**Betriebsempfehlung fuer `RERANKER_MAX_DOCUMENTS`**: Weave-Retrievals
Pipeline reicht typischerweise die Top 20 Kandidaten zum Reranking durch.
Der Default `RERANKER_MAX_DOCUMENTS=50` ist eine Obergrenze fuer
Ausreisser, kein Zielwert. Sendet eure Suche routinemaessig mehr als ~20-30
Kandidaten, zieht den Wert eher enger: die Kosten skalieren linear und
ohne Cap frisst ein einzelner Suchrequest beliebig viel CPU-Zeit.

## Kuerzung statt Ablehnung

Ein Query/Dokument-Paar laenger als 512 Tokens (`_MAX_SEQUENCE_LENGTH` in
`app/services/model.py`) wird vom Tokenizer
GEKUERZT, nicht mit einem Fehler abgelehnt -- ein leicht "blinder" Score
fuer ein zu langes Dokument ist besser als es kommentarlos aus der
Ergebnisliste des Callers verschwinden zu lassen. Siehe
`tests/test_rerank_content.py::test_long_document_is_truncated_not_rejected`
fuer den Beleg dazu (mit dem echten Modell, kein Mock).

## Betriebshinweis: Was passiert, wenn dieser Dienst ausfaellt

Weave-Retrievals `HttpReranker` faengt JEDEN eigenen Fehler
(`RerankError`) als eigene Exception ab -- die aufrufende Suchpipeline
(`app/services/search.py` in diesem Repo) entscheidet dann, ob sie darauf
still auf die unrerankte, RRF-fusionierte Reihenfolge zurueckfaellt.

**Das heisst konkret: ein kaputter oder ueberlasteter Reranker faellt NICHT
auf** -- die Suche liefert weiterhin Ergebnisse, nur ohne
Relevanz-Nachsortierung. Das ist gut fuer Verfuegbarkeit, aber gefaehrlich
fuer Beobachtbarkeit: eine `RERANKER_API_TOKEN`-Fehlkonfiguration, ein
dauerhaft 503 antwortendes (weil nie warm werdendes) Modell oder ein zu
knapp gesetztes `RERANKER_MAX_DOCUMENTS` zeigen sich NICHT als
Nutzer-sichtbarer Fehler, sondern nur als leise schlechtere
Ergebnis-Reihenfolge. Deshalb:

- Jeder Fehlerfall dieses Servers wird klar geloggt (siehe
  `app/api/rerank.py` und `app/api/deps.py`) -- 401/413/422/503 jeweils mit
  eigener, unterscheidbarer `detail`-Message.
- `GET /health` haelt sich strikt ehrlich: `"warm": false` bedeutet
  wirklich "kann noch keinen Request bedienen", nicht "startet halt noch
  kurz". Ueberwacht `warm` aktiv (z.B. per Alerting), nicht nur den reinen
  HTTP-Status von `/health` -- der ist by design IMMER 200, auch waehrend
  des Ladens.
- Wer sich auf Reranking als Qualitaetsmerkmal verlaesst, sollte dessen
  Verfuegbarkeit getrennt von der Verfuegbarkeit der Suche selbst
  ueberwachen -- ein Dashboard, das nur "Suche antwortet" misst, wird einen
  ausgefallenen Reranker nie bemerken.

## Konfiguration

Siehe `.env.example` fuer alle Variablen mit Begruendung. Kurzfassung:

| Variable | Default | Bedeutung |
|---|---|---|
| `RERANKER_MODEL` | `onnx-community/gte-multilingual-reranker-base` | HF-Repo mit ONNX-Datei und `tokenizer.json` |
| `RERANKER_ONNX_FILE` | `onnx/model_int8.onnx` | ONNX-Datei im Repo, siehe Modellwahl |
| `RERANKER_API_TOKEN` | *(leer)* | **Pflicht** -- fail-closed, siehe oben |
| `RERANKER_BATCH_SIZE` | `16` | Paare pro onnxruntime-Durchlauf |
| `RERANKER_THREADS` | `4` | onnxruntime `intra_op_num_threads` |
| `RERANKER_CACHE_DIR` | `./.cache` | HF-Hub-Cache-Verzeichnis fuer die Modellgewichte |
| `RERANKER_MAX_DOCUMENTS` | `50` | Harte Obergrenze, siehe Betriebsempfehlung oben |

## Lokal starten

```bash
cd services/reranker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # und RERANKER_API_TOKEN setzen
.venv/bin/uvicorn app.main:app --reload
```

## Tests

```bash
.venv/bin/python -m pytest                       # alles
.venv/bin/python -m pytest tests/test_rerank_api.py       # schnell, kein Modell (Mock)
.venv/bin/python -m pytest tests/test_rerank_content.py   # laedt das ECHTE Modell
RERANKER_SKIP_MODEL_TESTS=1 .venv/bin/python -m pytest    # ohne Modell-Download/-Test
```

`tests/test_rerank_api.py` deckt die Vertragsform ab: Antwortform,
absteigende Sortierung, `top_n`, `RERANKER_MAX_DOCUMENTS` -> 413, leere
Dokumentliste -> 422, alle Auth-Faelle (fehlend/falsch/nicht konfiguriert),
Modell-noch-am-Laden -> 503. `tests/test_rerank_content.py` ist der
inhaltliche Test mit dem echten Modell: ein offensichtlich passendes
deutsches Dokument (Kuendigungsfrist-Antwort) bekommt einen hoeheren Score
als ein offensichtlich unpassendes (Eiffelturm) -- plus der
Kuerzungs-Test aus dem Abschnitt oben.

## Docker

```bash
docker build -t weave-reranker .
docker run -p 8000:8000 \
  -e RERANKER_API_TOKEN=... \
  -v weave-reranker-cache:/data/model-cache \
  weave-reranker
```

Das Cache-Volume ist wichtig: ohne es laedt jeder Container-Neustart die
Modellgewichte (~341 MB int8) erneut von Hugging Face herunter.
