# Weave-API

## Einordnung

Weave-API ist das **Bot-Gateway** und der zentrale Einstiegspunkt des Weave-Systems. Sie verwaltet Authentifizierung, Konversations-State und nutzt Weave-Runtime als Backend für Message-Processing. Die Produktoberfläche liegt in `services/chat`; optionale Integrationen können das OpenAI-kompatible Protokoll verwenden. Weave-API ist außerdem die **Identitäts-Autorität** des Gesamtsystems: sie besitzt `users`/`api_tokens` und bietet anderen Services eine Token-Introspection an, ohne diese Tabellen zu teilen.

## Zweck

- POST `/v1/chat`: Unified Chat-Endpunkt (bot_id, conversation_id, message) → answer + sources + trace
- POST `/v1/chat/stream`: dieselbe Konversation, gestreamt (`text/event-stream`) statt einer einzelnen JSON-Antwort — für eine eigene UI
- POST `/v1/chat/completions`: optionaler OpenAI-kompatibler, zustandsloser Shim (Modell = bot_id), inkl. `"stream": true`
- GET `/v1/models`: OpenAI-kompatible Modell-Liste, gespeist aus der Bot-Registry
- GET `/v1/collections`: die Collections, die der aufrufende Nutzer (über sein Team) lesen darf — proxied zu Weave-Retrievals Collections-Lese-Autorität
- POST `/internal/tokens/introspect`: service-zu-service Token-Introspection für andere Weave-Dienste (z. B. einen künftigen MCP-Dienst)
- OIDC + API-Token Authentifizierung (nach Weave-Ingest-Muster)
- Rate-Limiting pro User und Bot
- Konversations-State-Verwaltung (Users, Bots, Conversations, Messages)
- Session-Handling und Audit-Logging

## Verantwortlichkeiten

- Authentifizierung und Token-Validierung
- Konversations-Persistierung in PostgreSQL
- Request-Routing zu Weave-Runtime
- Rate-Limiting und Quota-Enforcement
- Error-Handling und HTTP-Status-Mapping
- Metriken und Request-Tracing

## Nicht-Ziele / Abgrenzung

- **Keine Indexierung**: Weave-Knowledge und Weave-Retrieval verwalten Indizes
- **Kein Bot-Management**: Bots sind in Weave-Runtime konfiguriert
- Keine Tool-Ausführung: Tools sind Aufgabe von Weave-Tools
- Keine Chat-Intelligenz: Business-Logik gehört zu Weave-Runtime

## Schnittstellen

**Input:**
- POST `/v1/chat`: { bot_id, conversation_id, message, collections? }
- POST `/v1/chat/stream`: derselbe Body wie `/v1/chat`
- POST `/v1/chat/completions`: { model (= bot_id), messages[], stream? } — OpenAI-Chat-Completions-Format, zustandslos, keine Persistierung, **kein** `collections`-Feld (siehe unten)
- GET `/v1/models`: kein Body
- GET `/v1/collections`: kein Body — die Team-Zugehörigkeit kommt aus dem Bearer-Token/der Session des Aufrufers, nie aus einem Parameter
- POST `/internal/tokens/introspect`: { token } — hinter einem eigenen Service-Token, nicht dem End-Nutzer-Bearer-Token
- GET `/v1/auth/oidc/login`, GET `/v1/auth/oidc/callback`, POST `/v1/auth/logout`: OIDC-Browser-Session (siehe "OIDC-Anmeldung einrichten" unten) — kein Body, nur wenn `OIDC_ISSUER`/`OIDC_CLIENT_ID` gesetzt sind, sonst `404`. `GET /v1/auth/oidc/login` akzeptiert zusätzlich einen optionalen `return_to`-Query-Parameter (siehe "Cross-Origin-Übergabe" unten)
- POST `/v1/auth/session/exchange`: { code } — tauscht einen Übergabe-Code aus der Cross-Origin-Übergabe gegen ein echtes Sitzungs-Token; ebenfalls nur erreichbar, wenn OIDC konfiguriert ist, sonst `404`

**Output:**
- POST `/v1/chat`: JSON `{ answer, sources[], trace, created_at, conversation_id }`
- POST `/v1/chat/stream`: `text/event-stream` (`trace`/`delta`/`sources`/`done`/`error`-Ereignisse), Header `X-Conversation-Id`
- POST `/v1/chat/completions`: JSON im OpenAI-Format `{ id, object, created, model, choices[], usage? }`, oder — mit `"stream": true` — `text/event-stream` aus `chat.completion.chunk`-Ereignissen + `[DONE]`
- GET `/v1/models`: JSON im OpenAI-Format `{ object: "list", data: [{ id, object, created, owned_by }, ...] }`
- GET `/v1/collections`: JSON-Array `[{ slug, name, description, public }, ...]` — Weave-Retrievals eigenes `CollectionOut`, unverändert durchgereicht
- POST `/internal/tokens/introspect`: `{ active: true, user_id, username, team, is_admin }` bei gültigem Token, sonst `{ active: false }` (immer HTTP 200)
- GET `/v1/auth/oidc/login`: `302` zum Provider, setzt ein kurzlebiges signiertes State-Cookie
- GET `/v1/auth/oidc/callback`: `302` nach `/` (oder, bei gültigem `return_to`, nach `<return_to>?code=<code>` — siehe unten), setzt das Session-Cookie (`weave_api_session`, httpOnly) in **jedem** Fall
- POST `/v1/auth/session/exchange`: `{ session_token, expires_at }` bei gültigem, noch nicht verbrauchtem und nicht abgelaufenem Code, sonst generisch `400` (ununterscheidbar, ob unbekannt/abgelaufen/bereits verbraucht)
- POST `/v1/auth/logout`: `{ "status": "ok" }`, löscht Session-Zeile und Cookie

**Abhängigkeiten:**
- Weave-Runtime: Message-Verarbeitung
- Weave-Retrieval: Collections-Lese-Autorität (GET `/v1/collections`)
- PostgreSQL: Konversations-Persistierung
- OIDC-Provider (optional, siehe unten) oder Personal-API-Token
- Weave Chat (`services/chat`) oder ein optionaler OpenAI-kompatibler Client

## Entwicklung

Das Backend liegt unter `backend/` und folgt demselben Layout wie die
anderen Weave-Services (`app/{core,models,schemas,api,services}`,
`alembic/`, `tests/`).

### Setup

```bash
cd backend
python3.12 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # und Werte anpassen
```

Ohne weitere Konfiguration läuft der Service gegen eine lokale SQLite-Datei
(`DATABASE_URL`-Default) -- für eine echte Bereitstellung eine eigene
`weave_api`-PostgreSQL-Datenbank verwenden (ADR-0004: eine Database pro
Service, keine geteilten Tabellen).

### Tests

```bash
cd /pfad/zu/Weave-API
.venv/bin/python -m pytest backend/tests -q
```

Die Testsuite läuft komplett gegen SQLite (kein Postgres nötig) -- inklusive
eines eigenen `alembic upgrade head`/`downgrade base`-Durchlaufs gegen eine
frische SQLite-Datei (`backend/tests/test_migrations.py`).

### Alembic

```bash
cd backend
../.venv/bin/alembic upgrade head      # Migrationen anwenden
../.venv/bin/alembic downgrade base    # zurückrollen
../.venv/bin/alembic revision -m "..."  # neue Migration anlegen
```

`DATABASE_URL` (Env-Var oder `.env`) bestimmt das Ziel; ohne echte Postgres-
Verbindung läuft `alembic upgrade head` gegen die konfigurierte SQLite-Datei.

### CLI

Es gibt noch keine Selbstregistrierung -- Nutzer und ihre API-Tokens werden
über eine kleine Verwaltungs-CLI angelegt (`app/cli.py`):

```bash
cd backend
../.venv/bin/python -m app.cli create-user --username alice [--team Support]
../.venv/bin/python -m app.cli create-token --username alice [--expires-days 30]
```

Beide Befehle geben das rohe Bearer-Token genau EINMAL auf stdout aus --
gespeichert wird ausschließlich `sha256(token)` (`ApiToken.token_sha256`);
ein verlorenes Token ist nicht wiederherstellbar, nur durch `create-token`
ersetzbar.

`--team` (optional, freier Text) ist die Team-Zugehörigkeit, die dieses
Gateway an jeder Stelle propagiert, an der Team-Kontext gebraucht wird:
als `user.team` in `POST /v1/chat`s Aufruf an Weave-Runtimes eigene
`allowed_teams`-Prüfung, und als `team`-Query-Parameter, den
`GET /v1/collections` an Weave-Retrievals Collections-Lese-Autorität
weiterreicht, um zu bestimmen, welche Collections dieser Nutzer lesen darf.
Ein Nutzer ohne `--team` sieht dort nur öffentliche Collections (leere
`read_teams`-Liste). Es gibt noch kein `--admin`-Flag -- ein `is_admin`
gesetzter Nutzer (siehe `POST /internal/tokens/introspect` unten) muss
aktuell direkt in der Datenbank markiert werden.

### OIDC-Anmeldung einrichten

Zusätzlich zum Personal-API-Token oben (der Weg für **Maschinen** --
Skripte und andere programmatische Anbindungen, und bleibt es
auch nach dieser Änderung) kann sich ein **Mensch im Browser** über OIDC
anmelden und bekommt dafür ein serverseitiges, httpOnly Session-Cookie
statt eines Bearer-Tokens (`app/api/auth.py`, `app/core/auth.py`s
`get_current_user` akzeptiert seitdem beides gleichwertig). Anders als
Weave-Ingests eigene, admin-verwaltete Mehr-Provider-Tabelle ist hier genau
**ein** Provider fest per Env-Var konfiguriert:

```bash
OIDC_ISSUER=https://keycloak.example.org/realms/weave
OIDC_CLIENT_ID=weave-api
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URL=https://api.weave.example.org/v1/auth/oidc/callback
OIDC_SCOPES=openid email profile   # Default, meist ausreichend
OIDC_TEAM_CLAIM=groups             # optional -- leer = team bleibt null
```

OIDC gilt als **aktiviert**, sobald `OIDC_ISSUER` UND `OIDC_CLIENT_ID`
beide gesetzt sind -- ansonsten antworten `GET /v1/auth/oidc/login`,
`GET /v1/auth/oidc/callback` und `POST /v1/auth/logout` alle mit `404`, als
gäbe es sie nicht, und das Personal-API-Token bleibt der einzige Weg.

**Beim Provider zu hinterlegende Redirect-URI:** exakt der Wert von
`OIDC_REDIRECT_URL` -- der Provider vergleicht das beim Code-Austausch
byte-genau. Für einen lokalen Dev-Server typischerweise
`http://localhost:8004/v1/auth/oidc/callback`, produktiv die öffentlich
erreichbare HTTPS-URL dieses Gateways.

**Keycloak:** einen Client vom Typ "OpenID Connect" / "confidential" mit
"Standard flow" (Authorization Code) anlegen, die Redirect-URI oben unter
"Valid redirect URIs" eintragen, Client-ID/-Secret aus dem Client in
`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` übernehmen, `OIDC_ISSUER` auf
`https://<host>/realms/<realm>` setzen (Keycloaks eigene Discovery-URL
`.well-known/openid-configuration` hängt sich automatisch daran).

**Microsoft Entra ID:** eine "App registration" mit Plattform "Web"
anlegen, die Redirect-URI oben eintragen, unter "Certificates & secrets"
ein Client-Secret erzeugen, `OIDC_ISSUER` auf
`https://login.microsoftonline.com/<tenant-id>/v2.0` setzen. Entra
v1.0-Tokens liefern `email`/`preferred_username` nicht immer verlässlich --
für den Anzeigenamen genügt hier bereits ein beliebiger `preferred_username`/
`upn`/`name`-Claim (siehe `app/api/auth.py`s `_username_base_from_claims`);
für eine Team-Zuordnung über Gruppen `OIDC_TEAM_CLAIM=groups` setzen und die
optionalen `groups`-Claims in der App-Registrierung aktivieren.

Ein Nutzer, der sich erstmals über OIDC anmeldet, wird automatisch
angelegt (Benutzername aus der Claim-Kette, Team aus `OIDC_TEAM_CLAIM`
falls konfiguriert) und über `oidc_subject` (die `sub`-Claim des
ID-Tokens) dauerhaft mit diesem Provider-Konto verknüpft -- ein in der
Datenbank deaktivierter Nutzer (`disabled=true`) wird bei jedem weiteren
Login-Versuch abgewiesen, unabhängig vom Provider.

### Cross-Origin-Übergabe (`return_to`)

Das obige Session-Cookie funktioniert nur, solange die Oberfläche, die den
Login anstößt, auf **derselben Herkunft** wie dieses Gateway läuft --
läuft sie auf einer anderen Herkunft (eigene Domain, eigener Port),
erreicht das Cookie sie nie, egal wie der Callback antwortet. Für genau
diesen Fall gibt es eine explizite, serverseitige Übergabe:

1. Die Oberfläche schickt den Nutzer auf
   `GET /v1/auth/oidc/login?return_to=<URL der Oberfläche>`. `return_to`
   wird gegen `OIDC_POST_LOGIN_ALLOWED_URLS` geprüft (Schema/Host/Port
   exakt, Pfad als Präfix mit Grenze am Trenner, Dot-Segmente abgelehnt --
   dieselbe Prüfung wie Weave-Runtimes `N8N_ALLOWED_BASE_URLS`/
   `_base_url_matches`, hier als `app/api/auth.py`s
   `_return_to_matches_base` nachgebaut). Passt `return_to` zu keinem
   Eintrag -- oder ist die Liste leer, der Default -- wird es **stillschweigend
   ignoriert**: kein Fehler, kein Redirect dorthin, einfach der bisherige
   feste Pfad `/`, byte-für-byte wie vor dieser Funktion.
2. Nach erfolgreichem Callback erzeugt das Gateway einen einmal
   verwendbaren Übergabe-Code (kryptografisch zufällig, nur als sha256 in
   `session_exchange_codes.code_hash` gespeichert, 60 Sekunden gültig) und
   leitet auf `<return_to>?code=<code>` weiter -- **zusätzlich** zum
   gewohnten Session-Cookie, das auf derselben Antwort weiterhin gesetzt
   wird (für den Fall, dass Gateway und Oberfläche doch dieselbe Herkunft
   teilen; beide Wege funktionieren nebeneinander).
3. Die Oberfläche tauscht den Code **serverseitig** (nie aus Browser-JS)
   gegen ein echtes Sitzungs-Token: `POST /v1/auth/session/exchange
   { code }` → `{ session_token, expires_at }`. Der Code wird dabei in
   derselben Transaktion sofort entwertet -- ein zweiter Versuch mit
   demselben Code scheitert, ein unbekannter oder abgelaufener Code liefert
   dieselbe generische `400`-Antwort wie ein bereits verbrauchter, nie
   unterscheidbar. Der Code erscheint in keinem Log.
4. Die Oberfläche legt `session_token` in ihr **eigenes** httpOnly-Cookie
   (genau wie heute ein Personal-API-Token) und schickt es bei
   serverseitigen Aufrufen als Cookie-Header an dieses Gateway. Es landet
   nie in Browser-JavaScript.

```bash
OIDC_POST_LOGIN_ALLOWED_URLS=["https://chat.weave.example.org"]
```

**Warnung:** Jeder Eintrag in `OIDC_POST_LOGIN_ALLOWED_URLS` bekommt bei
jedem Login dort einen frisch ausgestellten, einmal einlösbaren
Sitzungscode zugestellt -- hier gehören ausschließlich Oberflächen hinein,
denen genauso vertraut wird wie diesem Gateway selbst, niemals eine
Drittanbieter-Domain. Leer (der Default) deaktiviert die Übergabe komplett
-- `return_to` wird dann immer ignoriert.

### Start (lokal, ohne Docker)

```bash
cd backend
../.venv/bin/uvicorn app.main:app --reload --port 8004
```

`GET /health` prüft dabei per `SELECT 1` aktiv die Datenbankverbindung.
Jede `/v1/*`-Route verlangt `Authorization: Bearer <Token>` (siehe
`app/core/auth.py`) und läuft zusätzlich durch das In-Process-Rate-Limiting
aus `app/core/ratelimit.py` (`RATE_LIMIT_PER_MINUTE`, Default 30 Requests
pro Nutzer und rollierender Minute; single-instance, siehe Modul-Docstring
für die Redis-Variante bei mehreren Replicas).

### Beispiel: POST /v1/chat

```bash
# Personal-API-Token vorher per CLI erzeugen (siehe "CLI" oben)
curl -s http://localhost:8004/v1/chat \
  -H "Authorization: Bearer $WEAVE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"bot_id": "legal-support", "message": "Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?"}' | jq
```

Die Antwort enthält `conversation_id` — bei einer Folgefrage in derselben Konversation wird diese `conversation_id` im nächsten Request mitgeschickt, statt `bot_id`/`message` erneut ohne Kontext zu senden.

### Optionalen OpenAI-kompatiblen Client anbinden

`POST /v1/chat/completions` ist ein optionaler OpenAI-kompatibler Shim (`app/api/openai_compat.py`) vor demselben `Weave-Runtime`-Backend wie `POST /v1/chat`. Er bindet Protokoll-kompatible Clients an, ohne dass Weave-API dafür eine eigene Konversation anlegt: jede Anfrage trägt ihren kompletten `messages`-Verlauf selbst und wird eigenständig verarbeitet, es gibt kein `conversation_id`-Äquivalent und **nichts** wird in `conversations`/`messages` persistiert. Für Menschen und persistente Gespräche ist `services/chat` über `/v1/chat` beziehungsweise `/v1/chat/stream` der vorgesehene Weg.

Der Client erhält als Base-URL `http://<weave-api-host>:8004/v1` und als API-Key ein Personal-API-Token dieses Nutzers. `GET /v1/models` wird aus derselben Bot-Registry wie `GET /v1/bots` gespeist. Jeder Weave-Runtime-Bot erscheint als Modell; die Bot-ID ist dessen `id`.

```bash
curl -s http://localhost:8004/v1/models -H "Authorization: Bearer $WEAVE_API_TOKEN" | jq
```

```json
{"object": "list", "data": [{"id": "legal-support", "object": "model", "created": 1735689600, "owned_by": "weave"}]}
```

```bash
curl -s http://localhost:8004/v1/chat/completions \
  -H "Authorization: Bearer $WEAVE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "legal-support",
        "messages": [
          {"role": "user", "content": "Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?"}
        ]
      }' | jq
```

Antwortformat wie bei OpenAI: `choices[0].message.content` trägt die Antwort, `model` echot die angefragte Bot-ID, `usage` wird nicht mitgeschickt (Weave-Runtimes eigener Chat-Vertrag liefert keine Token-Zahlen, siehe `app/schemas/openai.py`).

**Streaming:** mit `"stream": true` liefert derselbe Endpunkt `text/event-stream`: ein Chunk pro Textfragment (`choices[0].delta.content`), das erste Chunk zusätzlich mit `delta.role: "assistant"`, zum Schluss ein Chunk mit `finish_reason: "stop"` und danach `data: [DONE]`. Ohne `"stream"` bleibt die einzelne JSON-Antwort unverändert.

```bash
curl -s -N http://localhost:8004/v1/chat/completions \
  -H "Authorization: Bearer $WEAVE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "legal-support",
        "stream": true,
        "messages": [
          {"role": "user", "content": "Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?"}
        ]
      }'
```

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1735689600,"model":"legal-support","choices":[{"index":0,"delta":{"role":"assistant","content":"Laut"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1735689600,"model":"legal-support","choices":[{"index":0,"delta":{"content":" Vertrag..."},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1735689600,"model":"legal-support","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Bricht Weave-Runtimes eigene Generierung mitten im Stream ab (`type: "error"` auf `POST /internal/chat/stream`, siehe Weave-Runtimes `contracts/internal-chat.md`), endet dieser Stream einfach ohne `finish_reason`-Chunk und ohne `[DONE]` — es gibt in OpenAIs Chat-Completions-Stream-Format kein Fehler-Chunk, und der `200`-Status ist zu diesem Zeitpunkt bereits an den Client committed, kann also nicht mehr geändert werden (siehe `app/api/openai_compat.py:_stream_openai_chunks`).

### POST /v1/chat/stream (eigene UI)

Die streamende Variante von `POST /v1/chat` oben, für die eigene Chat-Oberfläche: derselbe Request-Body (`bot_id`, `message`, optional `conversation_id`), aber `text/event-stream` statt einer einzelnen JSON-Antwort — Weave-Runtimes eigene Stream-Ereignisse (`trace`/`delta`/`sources`/`done`/`error`, siehe Weave-Runtimes `contracts/internal-chat.md`) werden nahezu unverändert durchgereicht. Die Nutzer-Nachricht wird wie bei `POST /v1/chat` sofort persistiert; die Antwort des Bots wird aber **nur bei einem sauberen `done`-Ereignis** gespeichert — bricht der Stream vorher ab (Weave-Runtime schickt ein `error`-Ereignis, die Verbindung reißt ab, oder der Stream endet einfach ohne `done`), entsteht **keine** Assistant-Nachricht; die bereits gespeicherte Nutzer-Nachricht bleibt erhalten und der Turn kann wiederholt werden (siehe `app/api/chat.py:_stream_and_persist` für die vollständige Begründung). Ein neu angelegtes Gespräch (`conversation_id` weggelassen) teilt seine ID über den Response-Header `X-Conversation-Id` mit, da die fünf Stream-Ereignistypen selbst dafür kein Feld vorsehen.

```bash
curl -s -N http://localhost:8004/v1/chat/stream \
  -H "Authorization: Bearer $WEAVE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"bot_id": "legal-support", "message": "Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?"}'
```

```
data: {"type":"trace","trace":{"intent":"knowledge", ...}}

data: {"type":"delta","text":"Laut"}

data: {"type":"delta","text":" Vertrag..."}

data: {"type":"sources","sources":[...]}

data: {"type":"done"}
```

### GET /v1/collections

Liefert die Collections, die der aufrufende Nutzer lesen darf -- authentifiziert
und rate-limited wie jede andere `/v1/*`-Route. Weave-API beantwortet das
nicht selbst, sondern fragt Weave-Retrievals eigene Collections-Lese-
Autorität (`GET /api/v1/collections`, dort `app/api/collections.py`) nach dem
**Team des aufrufenden Nutzers** -- niemals nach einem anderen Team, dafür
gibt es keinen Parameter (`app/services/retrieval_client.py`,
`app/api/collections.py`). Ein Nutzer ohne `team` (siehe CLI oben) bekommt
nur öffentliche Collections zurück, exakt Weave-Retrievals eigene
`team=None`-Semantik.

```bash
curl -s http://localhost:8004/v1/collections \
  -H "Authorization: Bearer $WEAVE_API_TOKEN" | jq
```

```json
[
  {"slug": "legal-2026", "name": "Recht 2026", "description": null, "public": false}
]
```

Ist Weave-Retrieval nicht erreichbar (oder lehnt den Aufruf ab, z. B. wegen
eines falsch konfigurierten `RETRIEVAL_API_TOKEN` auf dieser Seite), antwortet
der Endpunkt mit `502` und einer Detail-Meldung, die den zugrundeliegenden
Fehler benennt -- konfiguriert wird das Ziel über `RETRIEVAL_BASE_URL`
(Default `http://localhost:8002`) und `RETRIEVAL_API_TOKEN` (siehe
`.env.example`).

### Token-Introspection (intern)

`POST /internal/tokens/introspect` macht Weave-API zur **Identitäts-
Autorität** des Gesamtsystems: ein anderer Dienst, der ein rohes
Personal-API-Token in der Hand hält (z. B. ein künftiger MCP-Dienst), kann
darüber die dahinterstehende Identität auflösen, ohne selbst Zugriff auf
`users`/`api_tokens` zu bekommen.

```bash
curl -s http://localhost:8004/internal/tokens/introspect \
  -H "Authorization: Bearer $INTROSPECTION_SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"token": "<irgendein-personal-api-token>"}' | jq
```

Bei einem gültigen, nicht abgelaufenen Token eines aktiven Nutzers:

```json
{"active": true, "user_id": "...", "username": "alice", "team": "Support", "is_admin": false}
```

Bei jedem anderen Fall -- unbekanntes Token, abgelaufen, deaktivierter
Nutzer -- immer `{"active": false}` mit HTTP **200**, niemals 401/404: der
Endpunkt gibt keinem Aufrufer ein Orakel darüber, ob ein Token-String
überhaupt jemals existiert hat.

**Warnung:** `INTROSPECTION_SERVICE_TOKEN` (siehe `.env.example`) ist ein
komplett eigenes Geheimnis, unabhängig von `RUNTIME_API_TOKEN` und
`RETRIEVAL_API_TOKEN` -- jene authentifizieren Weave-API als Aufrufer BEI
einem anderen Service, dieses hier authentifiziert einen anderen Dienst,
der HIER anfragt. **Wer dieses Token kennt, kann die Identität hinter
JEDEM Personal-API-Token dieser Datenbank auflösen** -- wie einen
Master-Key behandeln: nicht in Logs, nicht in Client-seitigem Code, nur an
Dienste geben, die tatsächlich Introspection brauchen. Ein Personal-API-
Token eines normalen Nutzers ist hier NICHT als Bearer-Token gültig -- der
Endpunkt prüft ausschließlich gegen `INTROSPECTION_SERVICE_TOKEN`
(`hmac.compare_digest`, siehe `app/core/auth.py`); ein leer gelassenes
`INTROSPECTION_SERVICE_TOKEN` lässt den Endpunkt hart mit `503` antworten,
niemals unauthentifiziert offen.

### Docker

```bash
docker build -f backend/Dockerfile -t weave-api backend
```

Der Compose-Service (`weave-api`, Port `${API_PORT:-8004}`) ist Teil von
Weave-Ingests `deploy/docker-compose.weave.yml`.

## Status

**In Arbeit (Phase 4)** — Service-Skeleton (Config, Models, Alembic-
Migration, Bearer-Token-Auth, In-Process-Rate-Limiting, Verwaltungs-CLI,
FastAPI-Health/Conversations/Bots-Proxy-Endpunkte, Tests, Docker/CI) steht.
`POST /v1/chat` ist implementiert: Konversation auflösen/anlegen,
User-Nachricht persistieren, History aufbauen, `Weave-Runtime`s
`/internal/chat` aufrufen (`app/services/runtime_client.py`, Feld-für-Feld
gegen Weave-Runtimes echte Pipeline geprüft — siehe dessen
`contracts/internal-chat.md`), Antwort samt `sources`/`trace` persistieren
und zurückgeben. Ist Weave-Runtime nicht erreichbar, antwortet mit `5xx`
(u. a. `503`, wenn dessen eigene Weave-Retrieval-Anbindung ausfällt), liefert
der Endpunkt `502` — die bereits persistierte User-Nachricht bleibt dabei
erhalten und der Turn kann wiederholt werden. Lehnt Weave-Runtime die
Anfrage ab (z. B. unbekannter Bot, keine Berechtigung), wird dessen
`4xx`-Status unverändert durchgereicht.

Zusätzlich gibt es jetzt `POST /v1/chat/completions`
(`app/api/openai_compat.py`) — einen zustandslosen, OpenAI-kompatiblen Shim
vor demselben Weave-Runtime-Backend, gedacht für optionale kompatible
Integrationen. Anders als `/v1/chat`
persistiert dieser Endpunkt nichts in `conversations`/`messages` — jede
Anfrage trägt ihren eigenen `messages`-Verlauf und wird unabhängig
verarbeitet.

Weave-Runtime bietet inzwischen `POST /internal/chat/stream` als SSE-
Gegenstück zu `POST /internal/chat` an (identischer Request-Body, identische
Pipeline, siehe dessen `contracts/internal-chat.md`) — Weave-API nutzt das
jetzt an zwei Stellen (`app/services/runtime_client.py:chat_stream`):

- `GET /v1/models` (`app/api/openai_compat.py`) liefert die Bot-Registry im
  OpenAI-Modellformat.
- `POST /v1/chat/completions` akzeptiert jetzt `"stream": true` und liefert
  dann `text/event-stream` im OpenAI-`chat.completion.chunk`-Format;
  ohne `"stream"` bleibt die bisherige, einzelne JSON-Antwort unverändert.
- `POST /v1/chat/stream` (`app/api/chat.py`) ist die streamende Variante von
  `POST /v1/chat` für eine eigene UI: dieselbe Konversations-Persistierung,
  aber Weave-Runtimes Stream-Ereignisse (`trace`/`delta`/`sources`/`done`/
  `error`) werden direkt durchgereicht. Die Assistant-Nachricht wird dabei
  bewusst nur bei einem sauberen `done` persistiert, nie bei einem Abbruch
  mittendrin (siehe "POST /v1/chat/stream (eigene UI)" oben für die volle
  Begründung und `app/api/chat.py:_stream_and_persist`).

Beide neuen Streaming-Pfade liegen hinter derselben Auth+Rate-Limit-Kette
wie jede andere `/v1/*`-Route.

Die Testsuite deckt alle vier Chat-Endpunkte End-to-End gegen eine gemockte
Weave-Runtime-HTTP-Schicht ab (`tests/test_chat_e2e.py` für die
nicht-streamenden, `tests/test_streaming_api.py` für `GET /v1/models` und
beide Streaming-Pfade — beide gemockt auf Transport-Ebene via
`httpx.MockTransport` statt am `runtime_client`-Objekt selbst, der reale
`httpx.Client` inkl. Auth-Header/Base-URL/Timeout läuft dabei tatsächlich
mit) sowie weiterhin gegen `runtime_client.chat()` selbst
(`tests/test_chat_api.py`). Der Integrationstest gegen eine tatsächlich
laufende Weave-Runtime-Instanz steht noch aus.

Weave-API ist jetzt außerdem die **Identitäts-Autorität** des Systems
(siehe "Token-Introspection (intern)" und "GET /v1/collections" oben):

- `POST /internal/tokens/introspect` (`app/api/internal.py`) löst ein rohes
  Personal-API-Token zu seiner Identität auf (`user_id`, `username`, `team`,
  `is_admin`), geschützt durch ein eigenes `INTROSPECTION_SERVICE_TOKEN`
  (`app/core/auth.py:require_introspection_service_token`, dasselbe
  503-bei-nicht-konfiguriert/401-bei-falsch-Muster wie Weave-Retrievals
  `require_service_token`) -- niemals mit einem End-Nutzer-Bearer-Token
  erreichbar. `User.is_admin` (neue Spalte, Migration `0002_add_users_
  is_admin`) hat noch keine CLI-Verwaltung; bis dahin direkt in der
  Datenbank gesetzt.
- `GET /v1/collections` (`app/api/collections.py`) proxied zu Weave-
  Retrievals Collections-Lese-Autorität (`app/services/retrieval_client.py`)
  mit dem Team des aufrufenden Nutzers -- nie mit einem anderen, dafür gibt
  es keinen Parameter. Nicht erreichbares Weave-Retrieval -> `502`.

Beide Surfaces sind komplett gegen gemockte HTTP-Schichten getestet
(`tests/test_introspection_api.py`, `tests/test_collections_api.py`),
inklusive der 503/401-Pfade der jeweiligen Service-Token-Prüfung.

`ChatRequest` (`app/schemas/chat.py`) trägt jetzt ein optionales
`collections: list[str] | None`-Feld -- ein reiner Einschränkungs-Filter
pro Anfrage auf `POST /v1/chat`/`POST /v1/chat/stream`, unverändert an
Weave-Runtimes eigenes `/internal/chat`(`/stream`) durchgereicht
(`app/services/runtime_client.py`); `None` (Default, weggelassen) bleibt
byte-für-byte das bisherige Verhalten, `[]` ("nichts") ist ein davon
unterscheidbarer, expliziter Wert. Ob und wie stark dieser Filter den
tatsächlichen Suchraum einschränkt, entscheidet ausschließlich
Weave-Runtimes eigene Rechte-Kette (Bot-Collections ∩ vom Nutzer lesbare
Collections) -- Weave-API selbst interpretiert diesen Filter nie, reicht
ihn nur durch. Der OpenAI-kompatible Shim (`POST /v1/chat/completions`,
`app/api/openai_compat.py`) bekommt dieses Feld bewusst NICHT, da OpenAIs
Chat-Completions-Format dafür kein Gegenstück kennt und ein
selbsterfundenes Extra-Feld dort die Kompatibilität bräche.

Diese Anmeldung läuft jetzt zusätzlich zum Personal-API-Token: OIDC-Login
für menschliche Browser-Sessions (ADR-0002, `app/api/auth.py`,
`app/services/oidc.py`) ist implementiert, nach Weave-Ingests eigenem
Muster (authlib für State/PKCE/Nonce, joserfc für die ID-Token-Prüfung
gegen die JWKS des Providers) aber auf EINEN statisch per Env-Var
konfigurierten Provider vereinfacht (siehe "OIDC-Anmeldung einrichten"
oben) statt Weave-Ingests admin-verwalteter Mehr-Provider-Tabelle.
`app/core/auth.py`s `get_current_user` akzeptiert seitdem gleichwertig
entweder ein Bearer-Token ODER ein Session-Cookie -- Rate-Limiting
(`app/core/ratelimit.py`) greift für beide identisch, da es nur den
aufgelösten `User` sieht, nie welcher Weg dorthin geführt hat. Ist
`OIDC_ISSUER`/`OIDC_CLIENT_ID` nicht gesetzt, antworten alle vier
OIDC-Endpunkte (inklusive `POST /v1/auth/session/exchange`) mit `404`, als
gäbe es sie nicht. Komplett gegen einen gemockten Provider getestet
(`tests/test_oidc_auth.py`, echte RSA-signierte ID-Tokens gegen eine
gemockte Discovery/Token/JWKS-HTTP-Schicht via `httpx.MockTransport` --
kein Netzzugriff): erfolgreicher Login samt Nutzer-/Session-Anlage,
falscher State, falsche Nonce, abgelaufenes/falsch signiertes ID-Token,
deaktivierter Nutzer, Session-Cookie-Auth, abgelaufene Session, Logout,
OIDC-nicht-konfiguriert sowie dass das Post-Login-Ziel sich nicht auf eine
fremde Domain lenken lässt.

Die Cross-Origin-Übergabe (`return_to`, `POST /v1/auth/session/exchange`,
"Cross-Origin-Übergabe" oben) ist ebenda mitgetestet: erfolgreiche Übergabe
samt Cookie-UND-Code nebeneinander, getauschtes Token authentifiziert
echte Endpunkte, Code genau einmal einlösbar, abgelaufener und unbekannter
Code liefern dieselbe generische Antwort, `return_to` ohne konfigurierte
Allowlist bzw. außerhalb davon wird ignoriert, sowie Umgehungsversuche
gegen die Allowlist (angehängte Domain, anderer Port, `userinfo@`-
Schmuggel, Dot-Segmente) -- und dass der Übergabe-Code in keinem Log
auftaucht. Migration `0004_session_exchange_codes` (die neue
`session_exchange_codes`-Tabelle) hat ihren eigenen Auf-/Abwärts-Roundtrip-
Test in `tests/test_migrations.py`.
