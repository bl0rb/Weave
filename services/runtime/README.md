# Weave-Runtime

## Einordnung

Weave-Runtime ist der **Bot-Orchestrator** des Weave-Systems. Sie empfängt Nutzer-Nachrichten von Weave-API, routet sie intent-basiert an spezialisierte LLM-Bots, und koordiniert Tool-Aufrufe sowie Retrieval. Sie ist das zentrale Gehirn für Konversations-Logik, ohne selbst Auth oder Persistierung zu verwalten.

## Zweck

- Intent-Router: Klassifizierung eingehender Queries als conversational, knowledge, document, action oder complex
- YAML-basierte Bot-Konfiguration (Model, System-Prompt, Retrieval, Tools, Permissions)
- LLM-Provider-Abstraktion (OpenAI, Anthropic, lokale Modelle)
- Response Guard: Erzwingung von Quellenangaben für Knowledge/Document-Responses
- Koordination von Weave-Retrieval und Weave-Tools Aufrufen

## Verantwortlichkeiten

- Intent-Erkennung und Bot-Selektion
- LLM-Provider-Verwaltung und Fallback-Logik
- Tool-Aufrufe und Fehlerbehandlung
- Prompt-Engineering und guardrails
- Trace-Generierung für Debugging und Audit

## Nicht-Ziele / Abgrenzung

- **Keine Nutzer-Auth**: Authentication erfolgt in Weave-API (OIDC, API-Tokens)
- **Kein Indexieren**: Knowledge wird von Weave-Knowledge gepflegt
- Keine Konversations-Persistierung: State wird von Weave-API verwaltet
- Keine Tool-Implementierung: Tools stellt Weave-Tools zur Verfügung

## Schnittstellen

**Input:**
- POST `/chat`: Message, Bot-ID, Context (User, Conversation)

**Output:**
- JSON: Answer-Text, Sources (mit Links), Execution-Trace, Warnings

**Abhängigkeiten:**
- Weave-Retrieval: Knowledge-Suche
- Weave-Tools: Action-Ausführung
- LLM-Provider: Model-Zugang

## Bot-Konfiguration

Lokale Beispiel- und Fallback-Bots sind YAML-Dateien unter `bots/` (Default-Verzeichnis, konfigurierbar über `BOTS_DIR`), validiert gegen `BotConfig` (`backend/app/schemas/bot.py`). Ist die zentrale Steuerung über `CHAT_CONFIG_BASE_URL` und `CHAT_CONFIG_SERVICE_TOKEN` eingerichtet, liest Runtime zusätzlich vor jedem Zugriff die aktivierten n8n-Bots aus Weave-Ingest. Administratoren pflegen sie dort unter **Administration → Bots**. Eine zentrale Bot-ID überschreibt eine gleichnamige lokale YAML-ID; dadurch bleibt Runtime zustandslos und eine Änderung gilt ab der nächsten Anfrage ohne Pod-Neustart. Beide Quellen werden gegen dasselbe `BotConfig`-Schema und dieselbe `N8N_ALLOWED_BASE_URLS`-SSRF-Grenze validiert. Ein Bot besteht aus:

- **`model`**: Provider (Default `fake`, für Dev/Tests ohne echten API-Key), Modellname, optionale Temperatur
- **`system_prompt`**: der Prompt, mit dem der Bot startet
- **`retrieval`**: aus/ein (Default aus), Metadaten-Filter (Team, Abteilung, Tags, Quelle, Sprache, Dokumenttyp), `top_k`/`final_k`/Rerank-Toggle — dieselben Konzepte wie Weave-Retrievals eigene `SearchFilters`/`SearchRequest` —, sowie `collections` (welche Collections dieser Bot nutzen darf, Default alle vom anfragenden Nutzer lesbaren; siehe Collections-Vertrag unten)
- **`permissions.teams`**: welche User-Teams den Bot nutzen dürfen (leere Liste = alle)
- **`guard`**: erzwingt bei aktiviertem Retrieval eine belegte Antwort (`require_sources`, Default an) statt einer vom LLM erfundenen — ohne brauchbare Quellen antwortet der Bot stattdessen mit `no_context_reply`

Zwei Beispiel-Bots liegen bereits vor: `bots/general-assistant.yaml` (kein Retrieval, für jedes Team) und `bots/legal-support.yaml` (Retrieval auf `filters.department: legal` und `collections: [vertraege]` eingeschränkt, nur für die Teams `legal`/`management` — das Beispiel aus dem Weave-Konzept für einen quellenpflichtigen, collection-gebundenen Bot mit eingeschränkten Berechtigungen). Ein dritter, `bots/n8n-agent.yaml.example` (absichtlich mit dieser Endung, nicht `.yaml` — siehe die Datei selbst), zeigt `model.provider: n8n`: statt eines direkten LLM-Aufrufs delegiert ein solcher Bot seinen Turn an einen n8n-Agentenflow, der über ein kurzlebiges, signiertes Delegations-Token GENAU den Lese-Umfang des anfragenden Menschen erhält und darüber selbst gegen Weave-Tools sucht/handelt — voller Vertrag in [`contracts/n8n-flow.md`](../../contracts/n8n-flow.md), Settings `WEAVE_DELEGATION_SECRET`/`DELEGATION_TOKEN_TTL_SECONDS`/`N8N_ALLOWED_BASE_URLS`/`TOOLS_BASE_URL`.

Die zentrale Maske verwaltet Anzeigename, Webhook, Timeout, erlaubte Teams, erlaubte Collections und den Quellen-Guard. Team- und Collection-Auswahl sind reine Einschränkungen: Runtime bildet weiterhin die Schnittmenge mit den aktuellen Nutzerrechten. Die vorhandene JSON-Webhook-Anbindung ist aktiv. Die neue inkrementelle n8n-Streaming-Strecke und die Weitergabe eines optional hinterlegten Webhook-Bearer-Tokens bleiben in der Oberfläche deaktiviert, bis der konkrete externe Ziel- und Datenfluss separat freigegeben wurde.

**Collections:** die cross-service Collections-Vertrag (siehe [`contracts/internal-chat.md`](../../contracts/internal-chat.md)) fügt jedem Dokument in Weave-Knowledge optional eine Collection-Zugehörigkeit hinzu, mit einer eigenen, von der Bot-YAML unabhängigen Leserechte-Liste. `bot.retrieval.collections` (oben) grenzt ein, welche dieser Collections DIESER Bot überhaupt nutzen darf; `backend/app/services/chat.py:resolve_collection_scope` schneidet das zur Laufzeit mit den Collections, die der anfragende Nutzer laut Weave-Retrieval (`GET /api/v1/collections`) tatsächlich lesen darf — das Ergebnis geht als `allowed_collections` an Weave-Retrievals Suche, nie `null`. Bleibt davon nichts übrig, wird gar nicht erst gesucht, sondern sofort `guard.no_context_reply` mit `trace.guard.reason == 'no_collections'` beantwortet.

## API

Jede Route unter `/internal/*` verlangt `Authorization: Bearer <RUNTIME_API_TOKEN>` (service-zu-service, ADR-0002-Muster — siehe `backend/app/core/auth.py`). `GET /health` ist unauthentifiziert und prüft, ob `BOTS_DIR` lesbar ist und jede darin liegende YAML-Datei gegen `BotConfig` validiert; eine defekte Bot-Datei degradiert den Status, ohne den Prozess selbst als down zu melden.

- `GET /health` — `{status: healthy|degraded, bots: <Anzahl>, detail?}`
- `GET /internal/bots` — Liste aller Bots (`id`, `name`, `description`, `retrieval.enabled`)
- `POST /internal/chat` — ein vollständiger Chat-Turn. Vollständiger Feld-für-Feld-Vertrag (Request/Response, Fehlerbilder, Auth, Versionierungsregel) in [`contracts/internal-chat.md`](../../contracts/internal-chat.md). Kurzfassung des Ablaufs in `backend/app/services/chat.py`:
- `POST /internal/chat/stream` — derselbe Chat-Turn, als `text/event-stream` statt einer einzelnen JSON-Antwort: `trace` (einmal, vor dem ersten Textstück) → `delta` (mehrfach) → `sources` + `done`, oder statt der letzten beiden ein abschließendes `error`, falls die Generierung mittendrin fehlschlägt. Volle Ereignis-/Fehlerdefinition im selben Vertrag, Abschnitt "Streaming".

  1. `load_bot(bot_id)` — unbekannt → `404`.
  2. Permissions: `bot.permissions.teams` nicht leer und `user.team` nicht enthalten → `403`.
  3. Intent-Routing (`ROUTER_MODE=rules|llm`, siehe `backend/app/services/router.py`) über `conversational`, `knowledge`, `document`, `action`, `complex`.
  4. Je nach Intent:
     - `conversational`: LLM mit `system_prompt` + `history` + `message`, keine Quellen.
     - `knowledge` (nur falls `bot.retrieval.enabled`): zuerst `resolve_collection_scope` (Collections, die dieser Bot UND der anfragende Nutzer beide erlauben) — bleibt davon nichts übrig, direkt `guard.no_context_reply` mit `trace.guard.reason == 'no_collections'`, ohne Weave-Retrieval-Aufruf. Sonst: Weave-Retrieval-Aufruf (`allowed_teams` bevorzugt `[user.team]`, sonst `bot.permissions.teams`; `allowed_collections` das eben resolvte Ergebnis) → nummerierter Kontextblock als System-Kontext → LLM. Findet Retrieval nichts und `guard.require_sources=true`, antwortet der Bot stattdessen mit `guard.no_context_reply` (`trace.guard.reason == 'no_context'`) statt einer unbelegten LLM-Antwort. Ist Weave-Retrieval selbst nicht erreichbar (Collections-Lookup oder Suche), antwortet die Route mit `503` — nie mit einer stillen, unbelegten Antwort.
     - `document` / `action` / `complex`: in dieser Version noch nicht unterstützt — höflicher deutscher Hinweis ("kommt in einer späteren Version"), kein LLM-Aufruf.
  5. `trace` (`ChatTrace`) wird für jeden Intent vollständig gefüllt: `intent`, `confidence`, `needs_retrieval`, `needs_tool`, `retrieval.candidates`/`used`/`collections`, `model`, `router_mode`, `timings_ms`.

## Entwicklung

```bash
# Einmalig: virtuelle Umgebung + Abhaengigkeiten
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.in

# .env anlegen (Defaults reichen fuer Dev/Tests; ein echtes RUNTIME_API_TOKEN
# wird erst fuer echte Service-Aufrufe benoetigt)
cp .env.example .env

# Tests
.venv/bin/python -m pytest backend/tests -q

# Lokaler Server (vom Repo-Root aus, damit BOTS_DIR=./bots auf bots/ zeigt)
.venv/bin/uvicorn app.main:app --reload --app-dir backend --port 8000
```

**Layout**: `backend/app/` mit `core` (Config, Service-Token-Auth), `schemas` (Pydantic-Modelle fuer Bot-Config und Chat-Request/-Response), `api` (FastAPI-Router), `services` (`botconfig.py` -- Laden/Validieren der YAML-Bots). `bots/` liegt bewusst am Repo-Root statt unter `backend/`, da es Betriebs-/Produktdaten sind (analog zu einer eigenen Konfigurationsablage), nicht Anwendungscode -- `BOTS_DIR`s Default `./bots` setzt das voraus. Bewusst **kein** `alembic/`, **kein** `models/`, **kein** `workers/`: Weave-Runtime ist zustandslos (kein SQLAlchemy, kein Celery/Redis, siehe "Nicht-Ziele").

### Beispiel

```bash
curl -s http://localhost:8000/internal/chat \
  -H "Authorization: Bearer $RUNTIME_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "bot_id": "legal-support",
        "message": "Welche Kündigungsfrist gilt laut unserem Arbeitsvertrag?",
        "history": [],
        "user": {"id": "u-1", "team": "legal"}
      }' | jq
```

## Status

**In Arbeit (Phase 4)** — Service-Skeleton (Config, Bot-YAML-Schema samt Validierung, Service-Token-Auth, `GET /health`/`GET /internal/bots`) steht, ebenso Intent-Router (`ROUTER_MODE=rules|llm`), LLM-Provider-Abstraktion (`fake`/`openai`) und die Weave-Retrieval-Anbindung als eigenständige, unit-getestete Module. `POST /internal/chat` (und dessen Streaming-Gegenstück `POST /internal/chat/stream`) ist jetzt die vollständige Pipeline (`backend/app/services/chat.py`): Bot-Lookup, Permissions, Intent-Routing, Retrieval mit nummeriertem Kontextblock, LLM-Aufruf und Response-Guard, End-to-End getestet (`backend/tests/test_chat_e2e.py`, `backend/tests/test_chat_stream.py`) mit `FakeLLM` und gemocktem Weave-Retrieval-HTTP. Vertrag in [`contracts/internal-chat.md`](../../contracts/internal-chat.md). n8n als zweiter, agentenfähiger Bot-Provider (Delegations-Token, SSRF-geprüfte Webhook-Anbindung) ist ebenfalls implementiert und getestet — Vertrag in [`contracts/n8n-flow.md`](../../contracts/n8n-flow.md); konkrete n8n-Flows werden außerhalb von Weave betrieben; die Token-Prüfung in `services/tools` ist Teil dieses Monorepos. Offen: echte LLM-Provider-Anbindung in Produktion (bislang nur `fake` in Tests/lokaler Entwicklung durchgespielt), Tool-Aufrufe für `action`/`complex` bei einem NICHT-n8n-Bot, Dokumentverarbeitung für `document` bei einem NICHT-n8n-Bot (alle drei antworten dort weiterhin mit einem Hinweis auf eine spätere Version).
