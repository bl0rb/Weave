# Weave-Tools

## Einordnung

Weave-Tools ist die **Action-Layer** des Weave-Systems. Sie stellt zwei Werkzeuge — `list_collections` und `search` — sowohl als MCP-Server als auch als schlichte REST-Endpunkte bereit, damit ein externer Agent (z.B. ein n8n-Workflow) oder ein MCP-Client auf dieselben Wissensbestände zugreifen kann, die ein Mensch über Weave-Runtime durchsuchen könnte — **niemals mehr**. Der Kern des Repos ist nicht die Suche selbst (die liefert Weave-Retrieval), sondern die Frage, mit wessen Rechten ein Aufruf laufen darf, und dass diese Frage bei jedem einzelnen Aufruf serverseitig neu beantwortet wird.

Das Repo enthaelt vier unabhaengige Bausteine. Sie teilen sich nur dieses eine Repo, laufen aber als getrennte Prozesse/Images und haben keine Laufzeit-Abhaengigkeit zueinander:

- **`backend/`** — MCP-Server + REST-Spiegel fuer `list_collections`/`search` (siehe oben), das eigentliche Werkzeugangebot dieses Repos fuer delegierte oder Personal-Token-gebundene Aufrufer. Laeuft als zwei uvicorn-Prozesse aus demselben Image: der REST-Mirror plus `/health`, und daneben der eigene MCP-Prozess (siehe "MCP-Server anbinden").
- **`frontend/`** — eine eigenstaendige Chat-Oberflaeche fuer Menschen, die ausschliesslich gegen Weave-API spricht, nie gegen `backend/` oder direkt gegen Weave-Retrieval (siehe "Frontend (Chat-UI)"). Das Personal-API-Token bleibt dabei serverseitig in einem httpOnly-Cookie, nie im Browser-JavaScript erreichbar.
- **`embeddings/`** — ein eigenstaendiger CPU-Embedding-Server fuer das vorgegebene `intfloat/multilingual-e5-small`, OpenAI-kompatibel unter `/v1/embeddings` und ueber onnxruntime statt fastembed geladen (das dieses Modell nicht listet, siehe dessen README). Weave-Knowledge bettet damit beim Indizieren Chunks ein, Weave-Retrieval bettet damit beim Suchen Anfragen ein — beide sprechen exakt denselben Endpunkt wie gegen die echte OpenAI-API.
- **`reranker/`** — ein eigenstaendiger CPU-Rerank-Server fuer das vorgegebene `BAAI/bge-reranker-v2-m3`, Cohere/Jina-kompatibel unter `/rerank` und ueber sentence-transformers/torch statt fastembed geladen (aus demselben Verfuegbarkeits-Grund, siehe dessen README). Weave-Retrievals `HttpReranker` spricht diesen Dienst optional als letzten Suchschritt an und faellt bei einem Ausfall leise auf die unrerankte RRF-Reihenfolge zurueck statt zu scheitern.

Beide Modelldienste sind CPU-only, laden ihre Gewichte nie in das Image (nur beim ersten Start in ein gemountetes Volume) und haben eigene `.venv`/Deployments, genau wie `backend/`/`frontend/` — siehe deren eigene READMEs fuer Modellwahl-Experiment, den `input_type`-Vertrag und Betrieb, und "Frontend (Chat-UI)" weiter unten fuer die UI.

## Zwei Token-Arten

Jeder Aufruf eines Werkzeugs (MCP oder REST) traegt genau einen der beiden folgenden Bearer-Token im `Authorization`-Header. Welcher es ist, wird rein an der Form entschieden — nie an einer vom Aufrufer behaupteten "Art":

- **Personal-Token** — der langlebige API-Token eines Menschen, ausgestellt von Weave-API. Weave-Tools loest ihn live auf: `POST {WEAVE_API_BASE_URL}/internal/tokens/introspect` (mit `INTROSPECTION_SERVICE_TOKEN`) liefert `user_id`/`username`/`team`, anschliessend liefert `GET {RETRIEVAL_BASE_URL}/api/v1/collections?team=<team>` die fuer dieses Team lesbaren Collections. Zwei Netzwerk-Aufrufe, bei **jedem** Request — bewusst ungecached, siehe naechster Abschnitt.

- **Delegations-Token** — ein kurzlebiges, HMAC-signiertes Token, das Weave-Runtime ausstellt, um einem externen Agenten (n8n, einem MCP-Client) fuer die Dauer eines einzelnen Tool-Aufrufs **genau** die Leserechte des gerade fragenden Menschen zu leihen, ohne ihm eine Kopie von dessen eigenem Personal-Token zu geben. Der Scope steckt **signiert im Token selbst**, nicht in Aufruf-Parametern — Weave-Tools muss dafuer keinen weiteren Service fragen, nur die Signatur pruefen.

  ```text
  token = b64url(json(payload)) + "." + b64url(hmac_sha256(secret, b64url(json(payload))))
  ```

  `payload` traegt `v`, `sub` (User-ID), `username`, `team`, `collections` (Liste erlaubter Slugs, darf den Sentinel `__none__` enthalten), `bot` (optional), `iat`, `exp`. Nur Standardbibliothek (`hmac`/`hashlib`/`json`/`base64`), keine JWT-Abhaengigkeit — es gibt genau einen Algorithmus und genau einen Schluessel, `WEAVE_DELEGATION_SECRET`. Dieser Wert ist **zwingend erforderlich, nicht optional**, und muss in Weave-Runtime (Aussteller) und hier (Pruefer) **identisch** gesetzt sein — Gueltigkeit per `DELEGATION_TOKEN_TTL_SECONDS` (Default 300s).

  Die Pruefung (`app/services/scope.py`) unterscheidet **niemals**, woran eine Pruefung gescheitert ist — falsche Signatur, abgelaufen, manipulierte Payload, falsche Version: immer derselbe generische Fehler. Ein unterscheidbarer Fehler waere ein kostenloses Orakel fuer einen Angreifer, der das Verfahren abklopft. Eine Ausnahme davon ist bewusst KEIN Verifikationsfehler: Ist `WEAVE_DELEGATION_SECRET` selbst leer — ein Tippfehler in der Env, ein fehlender Eintrag, Drift zwischen zwei Deployments, die denselben Wert teilen muessten — antwortet die Pruefung mit **503** (Dienst fehlkonfiguriert), nicht mit dem generischen 401 fuer einen ungueltigen Token. `hmac.new` mit einem leeren Schluessel liefert sonst eine ebenso gueltige, deterministische Signatur, die jeder anhand dieses dokumentierten Wire-Formats selbst berechnen und sich damit ein Token fuer beliebige `collections` faelschen koennte — ein leeres Secret darf deshalb niemals wie ein gueltiges behandelt werden.

Beide Pfade muenden in denselben `Scope` (`user_id`, `username`, `team`, `allowed_collections`) — kein Werkzeug-Code dahinter weiss oder muss wissen, welcher der beiden Wege ihn geliefert hat.

## Grundregel Rechte

Der erlaubte Collection-Umfang wird **immer** serverseitig aus der Identitaet abgeleitet — nie aus einem Aufruf-Parameter. Ein Collection-Name, der in einem Tool-Argument, einem REST-Request-Body oder einer Modell-Ausgabe steht, ist **immer nur eine Einschraenkung innerhalb** des bereits aufgeloesten `Scope`, nie eine zusaetzliche Erlaubnis: `search(collection=...)` schneidet gegen `scope.allowed_collections`, liegt der Wunsch ausserhalb, kommt ein **leeres Ergebnis** zurueck — kein Fehler, kein Hinweis, dass die Collection ueberhaupt existiert. Aus demselben Grund akzeptiert kein Werkzeug ein `team`- oder `user`-Argument ueberhaupt: Weder die MCP-Tool-Signaturen noch die REST-Request-Schemas besitzen ein solches Feld (ein trotzdem mitgeschicktes Feld wird von Pydantic stillschweigend verworfen, bevor der Handler es je sieht).

Der `Scope` wird bei **jedem** Aufruf frisch aufgeloest, nie gecacht: Ein entzogener Personal-Token oder eine einem Team entzogene Collection muss beim naechsten Request wirken, nicht erst nach Ablauf einer TTL. Das Delegations-Token traegt seine eigene, kurze TTL aus genau demselben Grund.

## Zwei Zugriffs-Oberflaechen, eine Implementierung

`app/services/tools.py` enthaelt die gesamte Logik (`list_collections_for_scope`, `search_for_scope`) genau einmal. Beide Oberflaechen sind duenne Transport-Schichten darueber, die nur `Scope` aus dem `Authorization`-Header aufloesen und durchreichen:

- **MCP-Server** (`app/mcp_server.py`), erreichbar per streamable-HTTP-Transport unter `/mcp` — fuer jeden MCP-faehigen Client (n8n-MCP-Node, Claude, ...).
- **REST-Spiegel** (`app/api/tools.py`) — `GET /api/v1/tools/collections`, `POST /api/v1/tools/search` — fuer n8n oder jeden anderen Aufrufer, der kein MCP sprechen will. Zusaetzlich gegen `TOOLS_API_TOKEN` (eigener `X-Tools-Service-Token`-Header, **nicht** `Authorization`) als grobes Zugangs-Gate abgesichert: "darf dieser Aufrufer Weave-Tools ueberhaupt erreichen" ist eine andere Frage als "in wessen Namen laeuft dieser eine Request", deshalb zwei getrennte Header statt einem ueberladenen.

`search` liefert Textausschnitte mit Quellenangabe (`document`, `page`, `collection`) — `collection` ist nur gesetzt, wenn der Aufruf selbst eine einzelne Collection angefragt hat (Weave-Retrievals eigenes `SearchResult` traegt keine Collection pro Treffer).

## MCP-Server anbinden

Der MCP-Server ist eine **eigene**, unabhaengige ASGI-App (`app/mcp_server.py:mcp_app`) und laeuft als eigener uvicorn-Prozess neben `app/main.py`, nicht darin eingehaengt — siehe die ausfuehrliche Begruendung im Docstring dieser Datei (der Streamable-HTTP-Transport haengt an einem Lifespan-getriebenen Session-Manager, den nur ein direkt gestarteter uvicorn-Prozess zuverlaessig durchreicht).

```bash
# REST-Mirror + /health
uvicorn app.main:app --app-dir backend --port 8000

# MCP-Server (streamable-HTTP, Endpunkt /mcp)
uvicorn app.mcp_server:mcp_app --app-dir backend --port 8010
```

Ein MCP-Client verbindet sich gegen `http://<host>:8010/mcp` und schickt `Authorization: Bearer <Personal-Token-oder-Delegations-Token>` auf jedem Request — genau dieser Header entscheidet ueber den `Scope`, mit dem `list_collections`/`search` laufen.

## Entwicklung

```bash
# Einmalig: virtuelle Umgebung + Abhaengigkeiten
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.in

# .env anlegen
cp backend/.env.example backend/.env

# Tests
.venv/bin/python -m pytest backend/tests -q
```

**Layout** (`backend/app/`): `core` (Config), `schemas` (Pydantic-Request-/Response-Modelle fuer Tools und Health), `services` (`scope.py` — Rechte-Aufloesung, das Herzstueck; `tools.py` — die eigentliche Werkzeug-Logik), `api` (REST-Router + Service-Token-Dependency), `mcp_server.py` (MCP-Server), `main.py` (REST-App + `/health`).

**MCP-SDK**: `mcp==2.1.1` (offizielles Python-SDK, PyPI-Paket `mcp`) — Installation und Import wurden geprueft, inklusive eines echten Client-Session-Roundtrips ueber den Streamable-HTTP-Transport in-process (`backend/tests/test_mcp_server.py`). Das SDK installierte sich sauber; die "implementiere es notfalls selbst per JSON-RPC"-Ausweichoption war nicht noetig. Hinweis fuer spaetere Aenderungen: `mcp` 2.x hat `FastMCP` in `MCPServer` umbenannt und mehrere Submodule verschoben — die 1.x-`FastMCP`-API aus vielen aelteren Tutorials passt auf diese Version nicht mehr.

## Frontend (Chat-UI)

`frontend/` ist eine eigenstaendige Chat-Oberflaeche fuer Menschen — **kein** Client dieses Repos eigener Werkzeuge. Sie hat nichts mit `backend/` (MCP-Server + REST-Spiegel fuer `list_collections`/`search`, siehe oben) zu tun und ruft es auch nicht auf; die beiden Ordner teilen sich nur dieses eine Repo, laufen aber als voneinander unabhaengige Deployments (dieselbe Aufteilung wie in Weave-Ingest: je ein `frontend/`- und ein `backend/`-Image, getrennt gebaut und betrieben).

**Wogegen sie spricht:** ausschliesslich gegen **Weave-API** (das Bot-Gateway) — `POST /v1/chat/stream` (der Hauptweg, `text/event-stream`), `POST /v1/chat` (nicht-streamender Fallback, siehe `frontend/src/components/chat/chat-app.tsx`), `GET /v1/bots`, `GET /v1/collections`. **Nicht** gegen dieses Repos eigenes `backend/` und **nicht** direkt gegen Weave-Retrieval — jede Anfrage, jeder Endpunkt-Pfad und jede Response-Form sind Feld-fuer-Feld aus Weave-APIs (und, wo Weave-API selbst nur durchreicht, Weave-Runtimes) tatsaechlichem Code uebernommen, nicht aus README-Prosa geraten (siehe die Kommentare in `frontend/src/types/weave-api.ts`).

**Token-Handhabung:** Weave-APIs Personal-API-Token wird **nie** im Browser-JavaScript gehalten und nie an den Client geschickt — kein `localStorage`, kein fuer JS lesbares Cookie. `/login` schickt das eingegebene Token an eine eigene Next.js Route-Handler-Schicht (`frontend/src/app/api/**`), die es serverseitig sofort gegen `GET /v1/bots` prueft und **nur bei Erfolg** als **httpOnly**-Cookie setzt (`frontend/src/lib/session.ts`). Jede weitere UI-Aktion geht an dieselbe Route-Handler-Schicht, die das Token aus dem Cookie liest, damit serverseitig Weave-API aufruft (`frontend/src/lib/weave-api-server.ts`) und nur die Antwort zurueckgibt — beim Streaming-Endpunkt die SSE-Bytes selbst, unveraendert durchgereicht, sonst JSON. Aus Sicht des Browsers ist jede Anfrage same-origin; es gibt kein CORS und keinen Codepfad im Client-Bundle, der das Token je referenzieren koennte. Die volle Begruendung steht als Kommentar in `frontend/src/lib/weave-api-server.ts` und `frontend/src/lib/session.ts`.

**Collection-Filter:** Weave-APIs `POST /v1/chat(/stream)`-Request-Schema (`ChatRequest.collections`, `backend/app/schemas/chat.py` dort) nimmt seit Kurzem einen optionalen Collection-Filter pro Anfrage entgegen, feldgleich an Weave-Runtime durchgereicht (siehe dessen `contracts/internal-chat.md`). Die Sidebar zeigt `GET /v1/collections` deshalb jetzt als wirksame Mehrfachauswahl: eine Auswahl schraenkt die Suche des naechsten Chat-Turns auf genau diese Collections ein, eine leere Auswahl sendet gar kein `collections`-Feld (kein Filter, exakt das bisherige Verhalten). Wichtig fuer die Erwartungshaltung — die Auswahl ist ausschliesslich eine **Einschraenkung**, niemals eine **Rechtevergabe**: sichtbar bleibt in jedem Fall nur, was der Aufrufer laut Bot-Konfiguration und Team-Zugehoerigkeit ohnehin lesen darf (serverseitig auf Weave-Runtime-Seite aufgeloest); ein in der Auswahl genannter, ausserhalb dieser Rechte-Schnittmenge liegender Slug wird dort still verworfen, nie als zusaetzliche Erlaubnis gewertet. Engt die eigene Auswahl eine an sich nicht-leere Rechte-Schnittmenge auf nichts ein, meldet Weave-Runtime das als eigenen Guard-Grund (`guard.reason = "filter_excluded_all"`, unterscheidbar vom Rechte-bedingten `"no_collections"`), den die Sidebar/der Chat-Verlauf mit dem naheliegenden Ausweg ("Auswahl aufheben") erklaert. Welche Collections ein einzelner Chat-Turn tatsaechlich durchsucht hat (nach Anwendung dieses Filters), steht weiterhin zusaetzlich im Trace der jeweiligen Antwort (`trace.retrieval.collections`/`requested_collections`).

**Start:**

```bash
cd frontend
npm install
cp .env.example .env.local   # WEAVE_API_BASE_URL setzen, Default http://localhost:8004
npm run dev                  # Entwicklung, Port 3000
npm run build && npm start   # Produktions-Build
npm test                     # Vitest: SSE-Parser, Fehler-Mapping, Route-Handler (gemockter fetch)
```

`WEAVE_API_BASE_URL` ist die einzige Konfiguration, die diese UI kennen muss — gelesen ausschliesslich serverseitig (Route Handlers), nie an den Browser ausgeliefert. Layout und Stack (Next.js/React/TypeScript/Tailwind, exakte Versionen in `frontend/package.json`) folgen bewusst Weave-Ingests `frontend/`, damit beide UIs auf demselben Stand bleiben; Gestaltung und Seiten sind eigenstaendig.

## Status

**Phase 5** — `backend/` steht: Rechte-Aufloesung (beide Token-Arten), beide Werkzeuge (MCP + REST), Tests gruen (`backend/tests`, ausschliesslich gegen gemockte Weave-API-/Weave-Retrieval-Aufrufe — keine echten Instanzen dieser Services noetig). `frontend/` (Chat-UI, siehe oben) steht ebenfalls: Login mit httpOnly-Cookie, Chat mit Streaming-Antwort/Belegen/Trace/Guard-Kennzeichnung, Vitest-Tests gruen, `npm run build` durchlaufend — ungetestet bleibt bisher nur der Lauf gegen eine echte Weave-API-Instanz (bislang ausschliesslich gegen einen lokalen Fake-Gateway und gemockten `fetch` verifiziert). `embeddings/` und `reranker/` stehen ebenfalls: beide vorgegebenen Modelle laufen (ausserhalb von fastembed, siehe deren READMEs), Tests gruen sowohl gegen einen gemockten Encoder als auch gegen das echte Modell, und beide sind als `weave-embeddings`/`weave-reranker`-Services in `Weave-Ingest/deploy/docker-compose.weave.yml` verdrahtet — Umstellung von den Attrappen-Providern auf beide echten Dienste ist dokumentiert in `Weave-Ingest/docs/betrieb.md` Abschnitt 7.
