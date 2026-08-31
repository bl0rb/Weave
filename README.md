# Weave-Tools

## Einordnung

Weave-Tools ist die **Action-Layer** des Weave-Systems. Sie stellt zwei Werkzeuge — `list_collections` und `search` — sowohl als MCP-Server als auch als schlichte REST-Endpunkte bereit, damit ein externer Agent (z.B. ein n8n-Workflow) oder ein MCP-Client auf dieselben Wissensbestände zugreifen kann, die ein Mensch über Weave-Runtime durchsuchen könnte — **niemals mehr**. Der Kern des Repos ist nicht die Suche selbst (die liefert Weave-Retrieval), sondern die Frage, mit wessen Rechten ein Aufruf laufen darf, und dass diese Frage bei jedem einzelnen Aufruf serverseitig neu beantwortet wird.

Dieses Repo enthaelt aktuell nur `backend/`. Eine Chat-UI (`frontend/`) ist als eigener, spaeterer Schritt geplant und bewusst noch nicht angelegt.

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

## Status

**Phase 5** — `backend/` steht: Rechte-Aufloesung (beide Token-Arten), beide Werkzeuge (MCP + REST), Tests gruen (`backend/tests`, ausschliesslich gegen gemockte Weave-API-/Weave-Retrieval-Aufrufe — keine echten Instanzen dieser Services noetig). `frontend/` (Chat-UI) folgt als eigener Schritt.
