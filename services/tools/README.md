# Weave-Tools

## Einordnung

Weave-Tools ist die **Tool-Surface** der Weave-Plattform: ein MCP-Server und
ein REST-Spiegel derselben zwei Tools, die beide ausschließlich im Rahmen der
Leserechte dessen antworten, der gerade aufruft. Der Dienst besitzt keine
Datenbank und speichert nichts; jede Angabe, die er ausliefert, holt er frisch
bei Weave-API und Weave-Retrieval — in genau dem Aufruf, der sie braucht
(`app/services/scope.py`, `app/services/tools.py`).

Den Dienst gibt es, damit ein externer Agent — ein n8n-Flow, ein MCP-Client —
die Wissensbasis der Plattform durchsuchen kann, ohne eine Kopie der
langlebigen Zugangsdaten eines Nutzers ausgehändigt zu bekommen und ohne einen
größeren Scope benennen zu können als den der Person, in deren Auftrag er
handelt.

## Zweck

- MCP-Tool `list_collections()`: die Collections, die **dieser Aufrufer** lesen
  darf
- MCP-Tool `search(query, collection=None, top_k=None)`: hybride Suche über
  genau diese Collections
- `GET /api/v1/tools/collections` und `POST /api/v1/tools/search`: der
  REST-Spiegel derselben zwei Tools, für Aufrufer, die kein MCP sprechen
- Prüfung kurzlebiger, von Weave-Runtime ausgestellter Delegations-Token
  (HMAC-SHA256 gegen ein geteiltes Secret)
- Auflösung von Personal-API-Token über die Token-Introspection von Weave-API
- `GET /health`: unauthentifizierter Liveness-Check

## Verantwortlichkeiten

- Den Scope eines Aufrufers bei jedem einzelnen Aufruf frisch auflösen
- Eine angefragte Collection mit diesem Scope schneiden, bevor Weave-Retrieval
  überhaupt kontaktiert wird
- Jede Ablehnung von jeder anderen Ablehnung ununterscheidbar halten
- Die Fehlkonfiguration eines Betreibers (`503`) von den fehlerhaften
  Zugangsdaten eines Aufrufers (`401`) unterscheidbar halten
- Weave-Retrievals Suchtreffer auf die eigene Antwortform der Tools abbilden

## Nicht-Ziele / Abgrenzung

- **Kein Index und keine Such-Implementierung**: Weave-Knowledge indiziert,
  Weave-Retrieval sucht
- **Keine eigene Identität**: Weave-API besitzt Nutzer und Tokens; dieser
  Dienst fragt nur nach
- **Keine Collection-Registry**: Weave-Retrieval ist die Lese-Autorität dafür,
  welches Team welche Collection lesen darf
- **Kein State**: keine Datenbank, kein Cache, keine Session — siehe "Nichts
  wird zwischengespeichert" unten
- Kein Ausstellen von Delegations-Token in Produktion: Weave-Runtime stellt
  aus, dieser Dienst prüft

## Schnittstellen

**Input:**

| Surface | Endpunkt | Auth |
|---|---|---|
| MCP (streamable HTTP) | `/mcp`, Tools `list_collections`, `search` | nur `Authorization` |
| REST | `GET /api/v1/tools/collections` | `X-Tools-Service-Token` **und** `Authorization` |
| REST | `POST /api/v1/tools/search` | `X-Tools-Service-Token` **und** `Authorization` |
| REST | `GET /health` | keine |

Body von `POST /api/v1/tools/search`: `{ query, collection?, top_k? }` —
`query` hat `min_length=1`, `top_k` ist auf `1..200` begrenzt
(`app/schemas/tools.py`). Ein `team`-, `user_id`- oder
`allowed_collections`-Feld gibt es bewusst nicht; jeder zusätzliche Schlüssel
wird von pydantics Default `extra='ignore'` verworfen, bevor der Handler läuft,
und würde ohnehin nie zur Scope-Bestimmung herangezogen.

**Output:**

- `GET /api/v1/tools/collections`: `[{ slug, name, description }, ...]`
- `POST /api/v1/tools/search`: `{ query, results: [{ text, source: { document, page, collection } }] }`
- Beide MCP-Tools liefern dieselben Formen als einfache Dicts (`model_dump()`)
- `GET /health`: `{ "status": "healthy" }`

**Abhängigkeiten:**

- Weave-API: `POST /internal/tokens/introspect` — löst ein Personal-Token zu
  einer Identität auf
- Weave-Retrieval: `GET /api/v1/collections` (Metadaten und, auf dem
  Personal-Token-Pfad, die lesbaren Slugs) und `POST /api/v1/search` (die
  hybride Suche selbst)
- Weave-Runtime: der produktive Aussteller der Delegations-Token, teilt sich
  `WEAVE_DELEGATION_SECRET` mit diesem Dienst

## Der Scope kommt immer aus der Identität

Die eine Regel, um die herum dieser Dienst gebaut ist: die Menge der
Collections, die ein Aufruf berühren darf, wird serverseitig aus den
Zugangsdaten des Aufrufers selbst abgeleitet, und Argumente können sie nur
einengen, niemals erweitern.

- Keines der beiden MCP-Tools hat einen `team`-, `user_id`- oder
  `allowed_collections`-Parameter, und der REST-Request-Body hat ebenso wenig
  ein solches Feld. Ein Test prüft dieses Fehlen an der Tool-Signatur selbst
  (`tests/test_mcp_server.py:86`).
- Beide Transporte rufen dasselbe `resolve_scope()` und danach dieselben
  `list_collections_for_scope` / `search_for_scope` auf. Es gibt keine zweite
  Implementierung der Zugriffslogik, die synchron gehalten werden müsste
  (`app/api/tools.py`, `app/mcp_server.py`, `app/services/tools.py`).
- Das optionale `collection` von `search` ist eine Einschränkung *innerhalb*
  des aufgelösten Scopes. Eine Collection außerhalb davon liefert ein leeres
  Ergebnis — keinen Fehler und keinen Hinweis darauf, dass die Collection
  existiert. Geprüft wird das im eigenen Code dieses Dienstes, bevor
  Weave-Retrieval aufgerufen wird (`app/services/tools.py:96`).
- `Scope.allowed_collections` ist immer eine konkrete Liste, nie `None`, denn
  `None` ist Weave-Retrievals eigener Sentinel-Wert für "gar keine
  Einschränkung". Eine leere Liste bedeutet "liest nichts" und wird genau so
  durchgereicht (`app/services/scope.py:185`).
- Einem Aufrufer ohne Team wird aus demselben Grund `allowed_teams: []` statt
  `None` geschickt (`app/services/tools.py:114`).
- `list_collections` holt die Collection-Metadaten von Weave-Retrieval und
  behält dann nur die Zeilen, deren Slug auch in `scope.allowed_collections`
  steht. Für einen Aufrufer mit Personal-Token ist diese Schnittmenge
  wirkungslos; für ein Delegations-Token, dessen eingebettete Liste enger ist
  als das, was der delegierende Mensch sonst lesen könnte, ist sie eine echte
  Einschränkung (`app/services/tools.py:31`).

Weave-Retrievals eigener `allowed_collections`-Filter greift darunter
weiterhin als zweite, unabhängige Grenze — er ist kein Ersatz für die Prüfung
oben.

### Nichts wird zwischengespeichert

Keiner der beiden Scope-Pfade speichert sein Ergebnis über Requests hinweg
zwischen. Ein in Weave-API widerrufenes Personal-Token oder ein in
Weave-Retrievals Registry geänderter Team-Zugriff wirkt beim allernächsten
Aufruf dieses Dienstes, statt erst dann, wenn irgendeine TTL gerade abläuft.
Ein Delegations-Token trägt aus demselben Grund seine eigene kurze TTL
(`app/services/scope.py`, Modul-Docstring).

## Zwei Ausweise am REST-Router

Der REST-Spiegel liest zwei getrennte Header, und sie beantworten zwei
verschiedene Fragen:

| Header | Frage | Konfiguriert als | Fehlerfall |
|---|---|---|---|
| `X-Tools-Service-Token` | Darf dieses Deployment die REST-API von Weave-Tools überhaupt aufrufen? | `TOOLS_API_TOKEN` | `503`, wenn nicht gesetzt; `401`, wenn falsch oder fehlend |
| `Authorization: Bearer …` | Unter wessen Rechten läuft genau dieser Aufruf? | keine Einstellung — das eigene Token des Aufrufers | `401` bei einem ungültigen Token, `503`, wenn dieses Deployment keines prüfen kann |

`require_tools_service_token` hängt am Router selbst, damit beide Routen
abgedeckt sind, und vergleicht mit `hmac.compare_digest`. Geprüft wird bewusst
auf einem eigenen Header und nie auf `Authorization`: eine einzelne Anfrage
muss beide Antworten gleichzeitig tragen können, und beides in einen einzigen
Header zu packen würde genau das unmöglich machen (`app/api/deps.py:19`).

Beide Gates sind fail-closed. Ein nicht gesetztes `TOOLS_API_TOKEN` lässt jeden
REST-Aufruf mit `503` antworten, statt einen leeren Header auf eine leere
Einstellung passen zu lassen (`app/api/deps.py:32`). `ScopeConfigurationError`
wird getrennt von `ScopeError` und vor diesem gefangen, damit die
Fehlkonfiguration eines Deployments den Aufrufer nie als "dein Token ist
falsch" erreicht (`app/api/deps.py:57`).

## Asymmetrie: der MCP-Server hat kein Deployment-Gate

`X-Tools-Service-Token` schützt allein den REST-Spiegel. Der MCP-Server
(`app/mcp_server.py`) hat nirgends eine entsprechende Prüfung — ein
Tool-Aufruf wird ausschließlich über den `Authorization`-Header
authentifiziert, den der MCP-Client mitbringt und der für genau diesen Aufruf
aus `Context.headers` gelesen wird. Der Kommentar an der Einstellung selbst
sagt das ausdrücklich (`app/core/config.py:9`), ebenso `.env.example`.

Deshalb bündelt `deploy/docker-compose.weave.yml` den MCP-Server zwar im
Image, startet ihn aber nicht. Der Service `weave-tools-backend` betreibt den
REST-Spiegel; der Kommentarblock an diesem Service hält fest, dass die MCP-App
bewusst ein eigener uvicorn-Prozess ist und dass die Frage, ob man sie
überhaupt startet, davon abhängt, ob im jeweiligen Deployment tatsächlich
irgendein MCP-Client einen braucht — eben weil die MCP-Surface jeden Aufruf
allein über den `Authorization`-Header authentifiziert, den der Aufrufer
ohnehin schon mitbringt (`deploy/docker-compose.weave.yml:884`).

Die beiden Einstiegspunkte sind außerdem aus einem mechanischen Grund getrennte
ASGI-Apps: `MCPServer.streamable_http_app()` liefert eine Starlette-App zurück,
deren Session-Manager nur von den ASGI-Lifespan-Ereignissen genau dieser App
gestartet wird. Wer sie in die FastAPI-App von `app/main.py` mountet, reicht
diesen Lifespan nicht mit weiter — dafür bräuchte es zusätzliche Verdrahtung,
die diese SDK-Version nicht mitbringt — und merkt den Fehler erst beim ersten
Tool-Aufruf statt beim Start (`app/mcp_server.py:23`). Beide Apps sind
gleichermaßen zustandslos, daher ist es gleich richtig, die eine, die andere
oder beide nebeneinander auf verschiedenen Ports zu betreiben.

## Zwei Token-Arten auf dem Authorization-Header

`resolve_scope()` entscheidet allein anhand der Form des rohen Bearer-Tokens —
nie anhand eines vom Client deklarierten Typ-Feldes, das nur eine weitere
unauthentifizierte Eingabe wäre, der man vertrauen müsste. Ein Token, das genau
einen `.` enthält, gilt als Delegations-Token; alles andere geht durch die
Introspection (`app/services/scope.py:451`).

**Delegations-Token.** Ein kurzlebiger, HMAC-signierter Ausweis, den
Weave-Runtime ausstellt, um einem externen Agenten genau die Leserechte des
gerade fragenden Menschen zu leihen. Das Wire-Format kommt bewusst ohne
Abhängigkeiten aus — kein JWT, denn es gibt genau einen Algorithmus und einen
Schlüssel:

```
token = b64url(json(payload)) + "." + b64url(hmac_sha256(secret, b64url(json(payload))))
```

`b64url` ist urlsafe-Base64 ohne Padding; `json(payload)` ist
`json.dumps(payload, sort_keys=True, separators=(',', ':'))`, sodass beide
Seiten den HMAC über byte-identische Eingaben berechnen. Die Payload trägt `v`
(heute immer `1`), `sub`, `username`, `team`, `collections`, `bot`, `iat` und
`exp`. Das Token *ist* der Scope: alles Nötige steht in der signierten Payload,
deshalb macht dieser Pfad überhaupt keinen ausgehenden Aufruf. `bot` und
`username` sind Audit-Kontext und werden nie für die Zugriffskontrolle
herangezogen; `kind` (`'delegated'` vs. `'personal'`) wird ebenso nur zur
Observability mitgeführt, denn eine Delegation gewährt genau die Rechte des
delegierenden Menschen und nie eine andere Menge.

Die Prüfung nutzt `hmac.compare_digest` für die Signatur, verlangt `v == 1` und
verlangt `exp > now` ganz ohne Toleranz für Uhrzeit-Abweichungen. Jeder
Fehlerfall — kaputtes Base64, ungültiges JSON, ein falsch typisiertes Feld,
falsche Version, falsche Signatur, abgelaufenes Token — löst dieselbe Exception
mit derselben generischen Meldung aus (`invalid or expired token`), sodass ein
abgelehntes Token kein Orakel zum Abtasten des Verifiers liefert. Das rohe
Token gelangt nie in eine Exception, in eine Log-Zeile oder in irgendetwas
daraus Abgeleitetes; zwei Tests sichern das für beide Token-Arten ab
(`tests/test_scope.py:392`, `tests/test_scope.py:404`).

`WEAVE_DELEGATION_SECRET` wird auf Leerheit geprüft, bevor auch nur ein Byte
des Tokens angesehen wird, und diese Prüfung löst `ScopeConfigurationError`
(`503`) aus, nicht `ScopeError` (`401`). Der Grund ist ein ganz bestimmter:
`hmac.new(key=b'', ...)` ist kein Fehler, sondern eine gültige, deterministische
Signatur — auf einem Deployment, dessen Secret bloß nicht gesetzt ist, könnte
also jeder, der das dokumentierte Wire-Format gelesen hat, ein Token mit
beliebigen `collections` fälschen. `_sign` verweigert als zweite
Verteidigungslinie, einen HMAC mit leerem Schlüssel zu berechnen
(`app/services/scope.py:326`, `app/services/scope.py:234`). Beide
ASGI-Einstiegspunkte loggen beim Prozessstart eine geheimnisfreie Warnung, wenn
der Wert fehlt, damit ein Deployment, das fail-closed arbeitet, das nicht
stillschweigend tut (`app/services/scope.py:247`).

`issue_delegation_token()` liegt in diesem Modul, damit es vom Wire-Format
genau eine Implementierung gibt, gegen die beide Seiten byte-kompatibel
bleiben, und damit die Testsuite gültige und manipulierte Tokens bauen kann.
Der produktive Aussteller ist Weave-Runtime
(`services/runtime/backend/app/services/delegation.py`).

**Personal-Token.** Das eigene, langlebige API-Token eines Nutzers aus
Weave-API. Dieser Pfad macht jedes Mal zwei ausgehende Aufrufe:
`POST /internal/tokens/introspect` gegen Weave-API (mit
`INTROSPECTION_SERVICE_TOKEN`), um zu erfahren, wem das Token gehört, und
danach `GET /api/v1/collections?team=…` gegen Weave-Retrieval, um zu erfahren,
welche Collections dieses Team lesen darf. `team=None` wird als überhaupt kein
Query-Parameter übergeben, und genau das wählt "nur öffentliche Collections".
Auf ein unbekanntes, abgelaufenes oder zu einem deaktivierten Nutzer gehörendes
Token antwortet Weave-API mit `{"active": false}` und HTTP 200, woraus dieser
Dienst dieselbe generische Ablehnung macht wie aus allem anderen. Ein nicht
erreichbares Weave-API wird mit der tatsächlichen Exception geloggt, erreicht
den Aufrufer aber trotzdem als dieselbe generische Meldung
(`app/services/scope.py:410`).

## Konfiguration

`.env.example` ist für den Betrieb dieses Dienstes allein, außerhalb des
Compose-Stacks. Innerhalb des Stacks kommt jeder Wert aus `weave.yaml` im
Repository-Root, und `python scripts/weave_config.py render` schreibt
`deploy/.env`.

| Variable | Default | Bedeutung |
|---|---|---|
| `TOOLS_API_TOKEN` | leer | Deployment-Gate für den REST-Spiegel, geprüft auf `X-Tools-Service-Token`. Nicht gesetzt heißt `503` bei jedem REST-Aufruf. |
| `WEAVE_API_BASE_URL` | `http://localhost:8004` | Weave-API, für die Token-Introspection |
| `INTROSPECTION_SERVICE_TOKEN` | leer | Was dieser Dienst dem Introspection-Endpunkt von Weave-API vorweist |
| `WEAVE_API_TIMEOUT_SECONDS` | `10` | Timeout für diesen Aufruf |
| `RETRIEVAL_BASE_URL` | `http://localhost:8002` | Weave-Retrieval, für Collections und Suche |
| `RETRIEVAL_API_TOKEN` | leer | Statischer Bearer, den Weave-Retrievals eigene Service-Token-Prüfung erwartet |
| `RETRIEVAL_TIMEOUT_SECONDS` | `10` | Timeout für diese Aufrufe |
| `WEAVE_DELEGATION_SECRET` | leer | Symmetrisches HMAC-Secret, in Weave-Runtime und hier identisch. Nicht gesetzt heißt `503` bei jeder Delegations-Token-Prüfung. |
| `DELEGATION_TOKEN_TTL_SECONDS` | `300` | Lebensdauer eines frisch ausgestellten Tokens |

Vier davon sind eigenständige Geheimnisse, und sie zu verwechseln ist der
häufigste Weg, diesen Dienst falsch zu konfigurieren: `TOOLS_API_TOKEN`
authentifiziert Aufrufer **dieses** Dienstes, `INTROSPECTION_SERVICE_TOKEN`
und `RETRIEVAL_API_TOKEN` authentifizieren diesen Dienst **als Aufrufer von**
Weave-API und Weave-Retrieval, und `WEAVE_DELEGATION_SECRET` geht überhaupt
nie über die Leitung — es wird nur lokal, auf beiden Seiten, zum Berechnen
und Prüfen eines HMAC benutzt.

## Entwicklung

Der Dienst hat kein `backend/`-Unterverzeichnis:
`app/{core,schemas,api,services}` und `tests/` liegen direkt unter
`services/tools`. Es gibt keine Datenbank und kein Alembic.

### Setup

```bash
cd services/tools
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # und echte Werte eintragen
```

`requirements.txt` ist ein schlichter `pip freeze` von `requirements.in`, kein
hash-gepinntes Lock. Der `mcp`-Pin (`mcp==2.1.1`) ist das 2.x-SDK, dessen
`MCPServer`-Klasse das ältere `FastMCP` abgelöst hat, das die meisten
vorhandenen Tutorials noch zeigen.

### Tests

```bash
cd services/tools
.venv/bin/pytest -q
```

Die Suite braucht kein Netz und keine Upstream-Dienste: Weave-API und
Weave-Retrieval sind am `httpx`-Aufruf gemockt, und der MCP-Roundtrip läuft
in-process über einen ASGI-Transport. Sie deckt den Delegations-Verifier ab
(inklusive eines gefälschten, mit leerem Schlüssel signierten Tokens, einer
manipulierten Payload, eines abgelaufenen Tokens und der Zusicherung, dass
jeder Fehlerfall dieselbe Meldung erzeugt), den Introspection-Pfad, die
Scope-Schnittmenge, das `TOOLS_API_TOKEN`-Gate, die Trennung zwischen `503` und
`401` sowie einen vollständigen MCP-Client/Server-Roundtrip, der beide Tools
auflistet und eines davon aufruft.

CI führt `pytest -q services/tools/tests` auf Python 3.12 aus, sobald sich
irgendetwas unter `services/tools/**` ändert (`.github/workflows/pr-ci.yml`).

### Lokal starten

Der REST-Spiegel und der MCP-Server sind zwei getrennte uvicorn-Prozesse:

```bash
cd services/tools
.venv/bin/uvicorn app.main:app --reload --port 8005
.venv/bin/uvicorn app.mcp_server:mcp_app --port 8100   # beliebiger freier Port
```

`GET /health` macht keinen Roundtrip nach unten — dieser Dienst besitzt keine
Datenbank, und jede Angabe, die er ausliefert, wird pro Aufruf frisch geholt;
"der Prozess antwortet auf HTTP" ist damit die ganze Liveness-Frage, die er für
sich selbst sinnvoll beantworten kann. Ob seine Upstreams erreichbar sind, ist
eine eigene Prüfung für das Monitoring des Deployments.

Der MCP-Endpunkt ist `/mcp` (streamable HTTP). Der Client muss das
Delegations- oder Personal-Token des Aufrufers als `Authorization: Bearer …`
schicken; der Transport reicht diesen Header bis zu jedem einzelnen
Tool-Aufruf durch.

### REST-Beispiele

```bash
curl -s http://localhost:8005/api/v1/tools/collections \
  -H "X-Tools-Service-Token: $TOOLS_API_TOKEN" \
  -H "Authorization: Bearer $CALLER_TOKEN" | jq
```

```json
[{"slug": "handbuch", "name": "Handbuch", "description": null}]
```

```bash
curl -s http://localhost:8005/api/v1/tools/search \
  -H "X-Tools-Service-Token: $TOOLS_API_TOKEN" \
  -H "Authorization: Bearer $CALLER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query": "Kündigungsfrist", "collection": "handbuch", "top_k": 5}' | jq
```

`source.collection` wird nur gefüllt, wenn der Aufrufer eine einzelne
Collection benannt hat; bei einer nicht eingegrenzten Anfrage bleibt es `null`.
`source.document` und `source.page` sind in beiden Fällen präzise.

### Docker

```bash
docker build -f services/tools/Dockerfile -t weave-tools services/tools
```

Das Image läuft als Non-Root-Nutzer, exponiert `8000` und startet
standardmäßig den REST-Spiegel. Der MCP-Server wird aus demselben Image
gestartet, indem das Kommando überschrieben wird:

```bash
docker run ... weave-tools uvicorn app.mcp_server:mcp_app --host 0.0.0.0 --port 8000
```

In `deploy/docker-compose.weave.yml` läuft der REST-Spiegel als
`weave-tools-backend`, veröffentlicht auf `${TOOLS_PORT:-8005}`, mit einem
Healthcheck gegen `/health` und `depends_on`-Bedingungen auf ein gesundes
`weave-api` und `weave-retrieval`. Der MCP-Server wird dort nicht gestartet;
siehe den Asymmetrie-Abschnitt oben.

## Status

Beide Tools sind genau einmal implementiert, in `app/services/tools.py`, und
über zwei dünne Transporte erreichbar. Der Delegations-Token-Verifier, der
Introspection-Pfad, das REST-Service-Gate, die Scope-Schnittmenge und das
MCP-Wire-Protokoll sind von der oben beschriebenen Testsuite abgedeckt. Die
Integration gegen ein laufendes Weave-API und ein laufendes Weave-Retrieval ist
nicht Teil dieser Suite — jeder Upstream ist auf HTTP-Ebene gemockt.
