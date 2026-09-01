# Weave-Reranker

Eigenstaendiger Rerank-Server auf CPU fuer `BAAI/bge-reranker-v2-m3` -- die
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

## Warum nicht fastembed

Experimentell mit `fastembed==0.8.0` geprueft (siehe
`fastembed.rerank.cross_encoder.TextCrossEncoder.list_supported_models()`):

```
Xenova/ms-marco-MiniLM-L-6-v2
Xenova/ms-marco-MiniLM-L-12-v2
BAAI/bge-reranker-base
jinaai/jina-reranker-v1-tiny-en
jinaai/jina-reranker-v1-turbo-en
jinaai/jina-reranker-v2-base-multilingual
```

`BAAI/bge-reranker-v2-m3` ist NICHT dabei -- nur die kleinere
`bge-reranker-base` (nicht die vorgegebene Version). Deshalb laedt dieser
Service das Modell stattdessen ueber `sentence-transformers`' `CrossEncoder`
(ein duenner Wrapper um ein normales HF-`transformers`-Modell mit
Sequence-Classification-Head), CPU-only via `torch`.

(Am Rande, fuer den Embeddings-Service desselben Repos: `intfloat/
multilingual-e5-small` fehlt ebenso in `fastembed.TextEmbedding.
list_supported_models()` -- von 30 gelisteten Modellen ist nur
`intfloat/multilingual-e5-large` vorhanden, nicht die vorgegebene `-small`-
Variante. Betrifft nicht diesen Service, aber denselben
Verfuegbarkeits-Vorbehalt aus der Aufgabenstellung.)

**Konsequenz fuer die Imagegroesse**: `torch` (CPU-Wheel) + `transformers` +
`sentence-transformers` bringen zusammen rund **1,0 GB** an installierten
Paketen mit (gemessen: `torch` allein 529 MB, `transformers` 108 MB, Rest --
`scipy`, `scikit-learn`, `numpy`, `huggingface_hub`, `tokenizers`,
`safetensors`, `sentence_transformers` selbst -- rundet auf ca. 1,0 GB
Site-Packages). Ein reiner `fastembed`+`onnxruntime`-Stack waere deutlich
kleiner gewesen (onnxruntime allein liegt bei ca. 150-200 MB) -- dieser
Mehrpreis ist der direkte Preis dafuer, GENAU das vorgegebene Modell zu
nehmen statt eigenmaechtig auf `bge-reranker-base` auszuweichen, nur weil
DAS in fastembed verfuegbar waere.

Die Modellgewichte selbst (`model.safetensors`, fp32) sind **2,27 GB** --
passend zur XLM-RoBERTa-large-Klasse mit ca. 568M Parametern, die die
Aufgabenstellung nennt. Sie werden NICHT ins Image gebacken, sondern beim
ersten Start ueber den Hugging-Face-Hub-Cache heruntergeladen und in einem
Volume persistiert (siehe Dockerfile und ".env.example").

## Gemessene CPU-Laufzeit

Gemessen auf dieser Maschine (Apple M4, 10 Kerne, `RERANKER_THREADS=4`,
warmes Modell, `RERANKER_BATCH_SIZE=16`, ein realistisches
Frage/Dokument-Paar mit deutschem Text):

| Dokumente | Gesamtzeit | Pro Dokument |
|-----------|-----------:|-------------:|
| 1         | ~0,07 s    | ~70 ms       |
| 10        | ~0,29 s    | ~29 ms       |
| 20        | ~0,48 s    | ~24 ms       |

Modell-Ladezeit: ~5 s bei warmem Hugging-Face-Cache; beim allerersten Start
(kalter Download der 2,27 GB) je nach Bandbreite deutlich laenger --
`GET /health` antwortet in dieser Zeit trotzdem sofort mit `"warm": false`
(siehe "Betriebshinweis" unten), nur `POST /rerank` wartet (bis zu 20s,
danach 503).

Ein Cross-Encoder wie dieser bewertet jedes Query/Dokument-Paar EINZELN --
anders als ein Bi-Encoder-Embedding gibt es hier keinen Weg, die Kosten
wegzubatchen. Die Kosten wachsen linear mit der Dokumentanzahl (die kleine
Fixkosten-Amortisierung von N=10 zu N=20 pro Dokument ist Batching-
Overhead, kein Sprung in der Grundcharakteristik).

**Betriebsempfehlung fuer `RERANKER_MAX_DOCUMENTS`**: Weave-Retrievals
eigene Pipeline reicht laut `HttpReranker`s Doku bereits typischerweise
"Top 20" Kandidaten zum Reranking durch (RRF-Fusion -> Top 20 ->
`final_k`-Kuerzung auf z.B. 5 Ergebnisse). Bei den gemessenen ~24-30 ms pro
Dokument liegt genau dieser realistische Fall (20 Dokumente) bei knapp
einer halben Sekunde zusaetzlicher Latenz -- gut vertretbar. Der Default
`RERANKER_MAX_DOCUMENTS=50` liegt bewusst mit Sicherheitsabstand DARUEBER
(bei linearer Fortschreibung ca. 1,2 s Worst-Case bei 50 Dokumenten), nicht
darunter -- als Obergrenze fuer einen Ausreisser-Request, nicht als
Zielwert. Sendet eure Suche routinemaessig mehr als ~20-30 Kandidaten zum
Reranking, zieht `RERANKER_MAX_DOCUMENTS` eher enger statt den Wert einfach
zu erhoehen: die Kosten skalieren linear und ohne Cap frisst ein einzelner
Suchrequest beliebig viel CPU-Zeit.

## Kuerzung statt Ablehnung

Ein Query/Dokument-Paar laenger als 512 Tokens (bge-reranker-v2-m3s eigenes
Tokenizer-Limit, siehe `app/services/model.py`) wird vom Tokenizer
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
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Vorgegeben, nicht aendern |
| `RERANKER_API_TOKEN` | *(leer)* | **Pflicht** -- fail-closed, siehe oben |
| `RERANKER_BATCH_SIZE` | `16` | An `CrossEncoder.predict(batch_size=...)` |
| `RERANKER_THREADS` | `4` | An `torch.set_num_threads(...)` |
| `RERANKER_CACHE_DIR` | `./.cache` | HF-Hub-Cache-Verzeichnis fuer die Modellgewichte |
| `RERANKER_MAX_DOCUMENTS` | `50` | Harte Obergrenze, siehe Betriebsempfehlung oben |

## Lokal starten

```bash
cd reranker
python3.12 -m venv .venv
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0
.venv/bin/pip install -r requirements.in
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
2,27 GB Modellgewichte erneut von Hugging Face herunter.
