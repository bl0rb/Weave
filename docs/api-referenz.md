# Weave API-Referenz

Weave besteht aus mehreren Diensten, die jeweils eine eigene HTTP-API anbieten. Diese Referenz beschreibt die Schnittstellen, die für Fachbereiche und Integratoren gedacht sind: das **Chat-Gateway (Weave-API)**, die **Werkzeug-Oberfläche für Agenten (Weave-Tools)** und die **Verwaltung von Wissensbereichen und Dokumenten (Weave-Ingest)**.

Nicht Teil dieser Referenz sind die Administrationsoberflächen (z. B. `/api/v1/auth/admin/*` in Weave-Ingest) sowie die rein internen, dienst-zu-dienst genutzten Endpunkte (`/internal/*`, `/api/v1/internal/*`). Diese existieren, sind aber für den Betrieb der Plattform reserviert, nicht für Integrationen.

## Basis-URLs

Jede Umgebung (Entwicklung, Staging, Produktion) hat eigene Hostnamen. Die folgenden Platzhalter werden in dieser Referenz durchgängig verwendet:

| Dienst | Platzhalter-Basis-URL | Zweck |
|---|---|---|
| Weave-API (Chat-Gateway) | `https://weave.example.com` | Chat, Konversationen, Bot-Liste, OpenAI-kompatibler Zugang |
| Weave-Ingest | `https://ingest.example.com` | Wissensbereiche anlegen/pflegen, Dokumente hochladen und freigeben, Bots (Fachseite) |
| Weave-Tools | `https://tools.example.com` (REST) bzw. eigener Host/Port für MCP (z. B. `https://tools.example.com:8008`) | Suche und Collection-Liste für Agenten (MCP oder REST) |

In einer lokalen Docker-Compose-Umgebung (`deploy/docker-compose.weave.yml`) laufen die Dienste standardmäßig auf `localhost` mit den Ports `8004` (Weave-API), `8000` (Weave-Ingest), `8005` (Weave-Tools REST) und `8008` (Weave-Tools MCP). In einer echten Bereitstellung fragen Sie Ihren Plattformbetreiber nach den tatsächlichen Hostnamen.

Alle Antworten sind JSON, sofern nicht anders angegeben (Streaming-Endpunkte liefern `text/event-stream`).

## Was geht per API

| Ich möchte … | Dienst | Endpunkt |
|---|---|---|
| Mit einem Bot chatten (einmalig oder gestreamt) | Weave-API | `POST /v1/chat`, `POST /v1/chat/stream` |
| Über einen OpenAI-kompatiblen Client chatten | Weave-API | `GET /v1/models`, `POST /v1/chat/completions` |
| Verfügbare Bots auflisten / Details ansehen | Weave-API | `GET /v1/bots`, `GET /v1/bots/{bot_id}` |
| Wissensbereiche sehen, die ich lesen darf | Weave-API | `GET /v1/collections` |
| Eigene Konversationen einsehen/löschen | Weave-API | `GET /v1/conversations`, `DELETE /v1/conversations/{id}` |
| Eigenes Profil / UI-Sprache lesen und setzen | Weave-API | `GET /v1/me`, `PUT /v1/me/locale` |
| Als Agent/Skript in meinem eigenen Scope suchen | Weave-Tools | MCP `search`, `POST /api/v1/tools/search` |
| Als Agent/Skript meine lesbaren Collections auflisten | Weave-Tools | MCP `list_collections`, `GET /api/v1/tools/collections` |
| Einen Wissensbereich anlegen, pflegen, freigeben, löschen | Weave-Ingest | `POST/GET/PATCH/DELETE /api/v1/collections` |
| Dokumente in einen Wissensbereich hochladen und verarbeiten | Weave-Ingest | `POST /api/v1/collections/{id}/upload`, `POST /api/v1/collections/{id}/start` |
| Den Status eines hochgeladenen Dokuments prüfen | Weave-Ingest | `GET /api/v1/jobs/{id}` |
| Ein geprüftes Dokument in den Wissensindex freigeben | Weave-Ingest | `POST /api/v1/portal/documents/{id}/release` |
| Ein Dokument (ggf. inkl. Rückzug aus dem Index) löschen | Weave-Ingest | `DELETE /api/v1/jobs/{id}?withdraw=true` |
| Eigene Bots pflegen (Inhalt, Freigaben) | Weave-Ingest | `GET /api/v1/bots`, `PATCH /api/v1/bots/{id}` |
| Ein persönliches API-Token verwalten | Weave-Ingest | `GET/POST /api/v1/auth/tokens`, `DELETE /api/v1/auth/tokens/{id}` |

> **Hinweis:** Ein Bot beantwortet eine Frage immer nur aus den Wissensbereichen, die **sowohl** dem Bot zugeordnet **als auch** von der fragenden Person selbst lesbar sind. Eine Bot-Freigabe verleiht kein Leserecht auf einen Wissensbereich — sie schränkt nur ein, nie umgekehrt (siehe `docs/adr/0008-besitz-freigaben-metadaten.md` und `contracts/internal-chat.md`).

---

## Authentifizierung

### Weave-API (Chat-Gateway)

Zwei Zugangswege, die beide zur selben `Authorization`-Prüfung führen:

1. **Personal-API-Token (für Maschinen/Skripte, empfohlen für Integrationen).** Es gibt **keinen** Selbstbedienungs-Endpunkt dafür — ein Token wird von einer Person mit Zugriff auf den Weave-API-Server über die mitgelieferte Verwaltungs-CLI ausgestellt:

   ```bash
   python -m app.cli create-token --username alice [--expires-days 30]
   ```

   Das rohe Token wird genau einmal auf der Konsole ausgegeben (nur `sha256(token)` wird gespeichert) und muss danach in jedem Request im Header mitgeschickt werden:

   ```
   Authorization: Bearer <token>
   ```

2. **Browser-Session (für Menschen in einer eigenen Chat-Oberfläche).** Nur nutzbar, wenn der Betreiber OIDC konfiguriert hat (`GET /v1/auth/oidc/login` → Provider-Login → `GET /v1/auth/oidc/callback` setzt ein httpOnly-Session-Cookie `weave_api_session`). Für eine eigene, auf einer anderen Domain laufende UI gibt es einen Cross-Origin-Übergabemechanismus (`return_to` + `POST /v1/auth/session/exchange`). Für serverseitige Integrationen ist der Personal-API-Token-Weg der richtige.

> **Wichtig:** Ein Bearer-Token hat auf jeder Route Vorrang vor einem Session-Cookie. Fehlt der `Authorization`-Header oder ist er nicht `Bearer …`-förmig, wird stattdessen das Session-Cookie geprüft.

### Weave-Ingest

Auch hier zwei Zugangswege, beide über denselben `Authorization`-Header bzw. ein Session-Cookie (`weave_ingest_session`) geprüft:

1. **Anmelden:** `POST /api/v1/auth/login` mit `{ "identifier": "<Benutzername oder E-Mail>", "password": "…" }` (oder ein konfigurierter SSO/OIDC-Anbieter, `GET /api/v1/auth/oidc/{slug}/authorize`). Erfolgreiche Anmeldung setzt das Session-Cookie.
2. **Persönliches API-Token erzeugen** (nur mit einer Browser-Session möglich, siehe unten): `POST /api/v1/auth/tokens`. Das Token beginnt mit `pd_` und wird ebenfalls nur einmal in der Antwort ausgegeben. Für alle weiteren Aufrufe:

   ```
   Authorization: Bearer pd_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

> **Wichtig:** `POST /api/v1/auth/tokens`, `GET /api/v1/auth/tokens` und `DELETE /api/v1/auth/tokens/{token_id}` lehnen einen Aufruf mit `Authorization: Bearer …` ab (`403`, „API tokens cannot manage tokens; use a browser session“). Ein gestohlenes Token darf sich nicht selbst verlängern oder andere Tokens löschen — die Token-Verwaltung braucht also immer eine eingeloggte Browser-Session (Cookie), niemals ein bestehendes Bearer-Token.

Ein Bearer-Request braucht keinen `Origin`-Header (die CSRF-Prüfung greift nur bei einem Session-Cookie).

### Weave-Tools

Die REST-Oberfläche verlangt **zwei** unabhängige Header gleichzeitig:

| Header | Bedeutung |
|---|---|
| `X-Tools-Service-Token: <TOOLS_API_TOKEN>` | Deployment-weites Secret: „darf dieser Aufrufer Weave-Tools überhaupt erreichen“ (z. B. ein n8n-HTTP-Node-Credential) |
| `Authorization: Bearer <token>` | Wessen Leserechte gelten für diesen Aufruf |

Der MCP-Server (`/mcp`) verlangt **nur** den `Authorization`-Header — es gibt dort kein `X-Tools-Service-Token`.

Für `Authorization` akzeptiert Weave-Tools drei Arten von Tokens, rein an ihrer Form unterschieden:

| Token-Form | Herkunft | Beispiel |
|---|---|---|
| Persönliches Weave-API-Token | Weave-API-CLI (`app.cli create-token`) | ein undurchsichtiger String ohne Punkt |
| Delegations-Token | von Weave-Runtime kurzlebig ausgestellt, wenn ein Bot/Agentenfluss im Auftrag einer Person handelt | enthält genau einen Punkt (`payload.signatur`) |
| Technische Identität | von einer Administration in Weave-Ingest angelegt (`/api/v1/auth/admin/technical-identities`, nicht Teil dieser Integrator-Referenz) | beginnt mit `wti_` |

> **Hinweis:** Eine technische Identität ist ein eigenständiges, langlebiges Zugangsrecht für eine Integration oder einen externen MCP-Client, unabhängig von einer Person. Ein Administrator legt sie in Weave-Ingest an und ordnet ihr direkt die lesbaren Wissensbereiche zu; das Token wird — wie bei den persönlichen Tokens — nur einmal angezeigt. Integratoren erhalten dieses Token von ihrem Weave-Betreiber, es gibt keine Selbstbedienung dafür.

> **Wichtig:** Egal welche Token-Art: Ein `collection`-Parameter bei `search` engt die Suche nur **innerhalb** der bereits zustehenden Rechte ein. Eine Collection außerhalb der eigenen Rechte liefert ein leeres Ergebnis — nie einen Fehler und nie einen Hinweis darauf, dass sie existiert.

---

## Weave-API — Chat-Gateway

Alle folgenden Endpunkte liegen unter der Basis-URL von Weave-API und verlangen einen gültigen Bearer-Token bzw. eine Session (siehe oben). Jeder Endpunkt ist zusätzlich pro Nutzer rate-limitiert (siehe „Fehlercodes & Rate-Limits“).

### Eigenes Profil

#### `GET /v1/me`

Liefert Benutzername und UI-Sprachpräferenz des Aufrufers.

**curl**

```bash
curl -s https://weave.example.com/v1/me \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

**Python**

```python
import requests

resp = requests.get(
    "https://weave.example.com/v1/me",
    headers={"Authorization": f"Bearer {WEAVE_TOKEN}"},
)
resp.raise_for_status()
print(resp.json())
```

**Antwort (Beispiel)**

```json
{ "username": "alice", "locale": "de" }
```

#### `PUT /v1/me/locale`

Setzt (oder löscht mit `null`) die eigene UI-Sprache.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `locale` | `"de"` \| `"en"` \| `null` | ja | neue Präferenz, `null` löscht sie |

```bash
curl -s -X PUT https://weave.example.com/v1/me/locale \
  -H "Authorization: Bearer $WEAVE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"locale": "en"}'
```

> **Hinweis:** Ist der Aufrufer über Weave-Ingest föderiert angemeldet, wird der Wert zuerst dort geschrieben; ist Weave-Ingest nicht erreichbar, antwortet dieser Endpunkt mit `503` und ändert nichts lokal (kein Auseinanderlaufen der beiden Systeme).

### Bots

#### `GET /v1/bots`

Listet die Bot-Registrierung, wie Weave-Runtime sie führt (reiner Durchreich-Proxy, Weave-API verwaltet selbst keine Bot-Konfiguration).

```bash
curl -s https://weave.example.com/v1/bots \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

```python
resp = requests.get(
    "https://weave.example.com/v1/bots",
    headers={"Authorization": f"Bearer {WEAVE_TOKEN}"},
)
print(resp.json())
```

**Antwort (Beispiel, gekürzt)**

```json
[
  {
    "id": "legal-support",
    "name": "Rechtsberatung intern",
    "description": "Beantwortet Fragen zu internen Richtlinien.",
    "retrieval": { "enabled": true },
    "kind": "llm",
    "teams": ["Recht"],
    "public": false,
    "collections": ["vertragsvorlagen"]
  }
]
```

#### `GET /v1/bots/{bot_id}`

Liefert die vollständige Konfiguration eines einzelnen Bots (nicht nur die Kurzform aus der Liste oben).

```bash
curl -s https://weave.example.com/v1/bots/legal-support \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

> **Wichtig:** Anders als `GET /v1/bots` liefert dieser Endpunkt die komplette Bot-Konfiguration, einschließlich `system_prompt` und Modelleinstellungen — nicht nur die Kurzfassung. Das gilt für jeden authentifizierten Aufrufer, unabhängig davon, ob er den Bot überhaupt nutzen darf; die Freigabeprüfung (Team/Person/öffentlich) greift erst beim eigentlichen Chat-Aufruf (`POST /v1/chat`), nicht beim Lesen der Konfiguration. Ein hinterlegtes Webhook-Secret (bei n8n-Bots) wird dabei nie im Klartext ausgeliefert.

### Wissensbereiche

#### `GET /v1/collections`

Die Wissensbereiche, die der Aufrufer über seine Team-Zugehörigkeit und ggf. persönliche Freigaben lesen darf. Kein Body, keine Parameter — die Team-Zugehörigkeit kommt ausschließlich aus dem eigenen Zugangsdaten des Aufrufers, nie aus einem Query-Parameter.

```bash
curl -s https://weave.example.com/v1/collections \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

**Antwort (Beispiel)**

```json
[
  { "slug": "vertragsvorlagen", "name": "Vertragsvorlagen", "description": "…", "public": false }
]
```

### Chat

#### `POST /v1/chat`

Ein einzelner Chat-Turn: Nachricht rein, Antwort inkl. Quellen und Debug-Trace raus. Persistiert automatisch in einer Konversation.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `bot_id` | `string` | ja | ID des Bots (aus `GET /v1/bots`) |
| `message` | `string`, 1–8000 Zeichen | ja | die aktuelle Nachricht |
| `conversation_id` | `uuid \| null` | nein | fehlt/`null` startet eine neue Konversation; sonst muss sie zum selben `bot_id` gehören |
| `collections` | `list[string] \| null` | nein | zusätzlicher Filter *innerhalb* der ohnehin lesbaren Wissensbereiche des Bots — engt nur ein, vergibt nie neue Rechte |

```bash
curl -s -X POST https://weave.example.com/v1/chat \
  -H "Authorization: Bearer $WEAVE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "bot_id": "legal-support",
        "message": "Wie lange ist die Kündigungsfrist?"
      }'
```

```python
resp = requests.post(
    "https://weave.example.com/v1/chat",
    headers={"Authorization": f"Bearer {WEAVE_TOKEN}"},
    json={"bot_id": "legal-support", "message": "Wie lange ist die Kündigungsfrist?"},
)
resp.raise_for_status()
data = resp.json()
print(data["answer"])
```

**Antwort (Beispiel, gekürzt)**

```json
{
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "answer": "Die ordentliche Kündigungsfrist beträgt laut Vorlage …",
  "sources": [
    { "source": "confluence", "original_filename": "Vertragsvorlage.pdf", "document_id": "doc-123", "chunk_id": 4, "score": 0.82 }
  ],
  "trace": { "intent": "knowledge", "confidence": 0.83, "needs_retrieval": true, "model": "gpt-4o", "router_mode": "rules" }
}
```

> **Hinweis:** Ohne `conversation_id` wird eine neue Konversation angelegt; deren ID steht in der Antwort und muss für den nächsten Turn mitgeschickt werden, sonst startet jede Anfrage eine neue Konversation.

#### `POST /v1/chat/stream`

Derselbe Turn wie oben, aber als `text/event-stream` statt einer einzelnen JSON-Antwort. Response-Header `X-Conversation-Id` trägt die (ggf. neu angelegte) Konversations-ID.

**curl**

```bash
curl -N -X POST https://weave.example.com/v1/chat/stream \
  -H "Authorization: Bearer $WEAVE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"bot_id": "legal-support", "message": "Wie lange ist die Kündigungsfrist?"}'
```

**Python**

```python
import json
import requests

with requests.post(
    "https://weave.example.com/v1/chat/stream",
    headers={"Authorization": f"Bearer {WEAVE_TOKEN}"},
    json={"bot_id": "legal-support", "message": "Wie lange ist die Kündigungsfrist?"},
    stream=True,
) as resp:
    resp.raise_for_status()
    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or raw_line.startswith(":"):
            continue  # SSE-Kommentarzeile, z. B. ": keepalive"
        if raw_line.startswith("data: "):
            event = json.loads(raw_line[len("data: "):])
            print(event["type"], event)
```

**SSE-Ereignistypen** (`data: <json>\n\n`, ein Ereignis pro Zeile):

| `type` | Wann | Payload |
|---|---|---|
| `trace` | genau einmal, als erstes Ereignis | `{ "trace": { … } }` |
| `delta` | 0..n mal | `{ "text": "…" }` — ein Textfragment der Antwort |
| `status` | nur bei Bots mit Agentenmodus, 0..n mal | `{ "stage": "researching", "agent_id": "...", "message": "…" }` |
| `sources` | genau einmal, nach dem letzten `delta` | `{ "sources": [ … ] }` |
| `done` | genau einmal, Abschluss ohne Fehler | keine Payload |
| `error` | höchstens einmal, **statt** `sources`+`done` | `{ "detail": "…" }` |

Zusätzlich sendet der Stream `: keepalive` als reine SSE-Kommentarzeile (kein `data:`, kein `type`), wenn ein Bot lange auf eine externe Verarbeitung wartet — diese Zeilen sind kein Ereignis und müssen ignoriert werden.

> **Wichtig:** Ein `200 OK` allein beweist nicht, dass der Turn vollständig gelungen ist. Werten Sie den Stream bis `done` (Erfolg) oder `error` (Abbruch mitten im Turn) aus — nach beidem folgt garantiert kein weiteres Ereignis mehr.

### Konversationen

#### `GET /v1/conversations`

Die eigene Chat-Historie, neueste zuerst, ohne Nachrichteninhalte.

```bash
curl -s https://weave.example.com/v1/conversations \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

**Antwort (Beispiel)**

```json
{
  "items": [
    { "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "bot_id": "legal-support", "title": "Kündigungsfrist", "created_at": "2026-09-20T10:00:00Z", "updated_at": "2026-09-20T10:05:00Z" }
  ]
}
```

#### `GET /v1/conversations/{id}`

Eine einzelne Konversation inklusive aller Nachrichten (Quellen/Trace je Nachricht).

```bash
curl -s https://weave.example.com/v1/conversations/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

#### `DELETE /v1/conversations/{id}` und `DELETE /v1/conversations`

Löscht eine einzelne bzw. alle eigenen Konversationen (inkl. Nachrichten), Antwort `204 No Content`.

```bash
curl -s -X DELETE https://weave.example.com/v1/conversations/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

> **Hinweis:** Eine fremde `conversation_id` liefert immer `404`, nie `403` — so lässt sich nicht per Statuscode unterscheiden, ob eine ID gar nicht existiert oder nur einer anderen Person gehört.

### OpenAI-kompatibler Zugang

Für Werkzeuge, die bereits „OpenAI Chat Completions“ sprechen (z. B. Open WebUI, LangChain-OpenAI-Client). `model` entspricht dabei `bot_id`.

#### `GET /v1/models`

```bash
curl -s https://weave.example.com/v1/models \
  -H "Authorization: Bearer $WEAVE_TOKEN"
```

**Antwort (Beispiel)**

```json
{
  "object": "list",
  "data": [
    { "id": "legal-support", "object": "model", "created": 1758700000, "owned_by": "weave" }
  ]
}
```

#### `POST /v1/chat/completions`

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `model` | `string` | ja | Bot-ID |
| `messages` | `list[{role, content}]` | ja, min. 1 Eintrag | `role` ∈ `system`/`user`/`assistant`; `system` wird verworfen (Systemprompt gehört dem Bot) |
| `stream` | `bool` | nein, Default `false` | `true` liefert `text/event-stream` |

```bash
curl -s -X POST https://weave.example.com/v1/chat/completions \
  -H "Authorization: Bearer $WEAVE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "legal-support",
        "messages": [{"role": "user", "content": "Wie lange ist die Kündigungsfrist?"}]
      }'
```

```python
resp = requests.post(
    "https://weave.example.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {WEAVE_TOKEN}"},
    json={
        "model": "legal-support",
        "messages": [{"role": "user", "content": "Wie lange ist die Kündigungsfrist?"}],
    },
)
print(resp.json()["choices"][0]["message"]["content"])
```

**Antwort (Beispiel, gekürzt)**

```json
{
  "id": "chatcmpl-9f2c…",
  "object": "chat.completion",
  "created": 1758700000,
  "model": "legal-support",
  "choices": [
    { "index": 0, "message": { "role": "assistant", "content": "Die ordentliche Kündigungsfrist …" }, "finish_reason": "stop" }
  ]
}
```

Mit `"stream": true` liefert derselbe Endpunkt `chat.completion.chunk`-SSE-Ereignisse, terminiert durch die Zeile `data: [DONE]`:

```python
with requests.post(
    "https://weave.example.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {WEAVE_TOKEN}"},
    json={
        "model": "legal-support",
        "messages": [{"role": "user", "content": "Wie lange ist die Kündigungsfrist?"}],
        "stream": True,
    },
    stream=True,
) as resp:
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line and raw_line.startswith("data: ") and raw_line != "data: [DONE]":
            print(json.loads(raw_line[len("data: "):]))
```

> **Hinweis:** Dieser Endpunkt ist bewusst zustandslos — anders als `POST /v1/chat` gibt es kein `conversation_id`-Feld und nichts wird serverseitig gespeichert. Der Client muss bei jedem Aufruf die komplette bisherige `messages`-Liste erneut mitschicken. Ein `collections`-Filter wie bei `POST /v1/chat` steht hier nicht zur Verfügung (kein Feld in der OpenAI-Anfrageform); wer das braucht, nutzt `POST /v1/chat`.

---

## Weave-Tools — Werkzeug-Oberfläche für Agenten

Weave-Tools hat keine eigene Datenbank: Jede Antwort wird live bei Weave-API/Weave-Retrieval erfragt, entlang der Leserechte, die im `Authorization`-Header stecken (siehe Authentifizierung oben). Es gibt genau zwei Werkzeuge, sowohl als MCP-Tools als auch als REST-Spiegel.

### MCP-Server

Endpunkt: `POST {MCP-Basis-URL}/mcp` (Streamable-HTTP-Transport). Tools: `list_collections()` und `search(query, collection=None, top_k=None)`. Beide lesen die Rechte ausschließlich aus dem `Authorization`-Header des jeweiligen Aufrufs — es gibt keinen `team`/`user_id`-Parameter, den ein Aufrufer setzen könnte.

Beispiel mit einem MCP-Python-Client:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    "https://tools.example.com:8008/mcp",
    headers={"Authorization": f"Bearer {WEAVE_TOOLS_TOKEN}"},
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("search", {"query": "Kündigungsfrist", "top_k": 5})
        print(result)
```

### REST-Spiegel

#### `GET /api/v1/tools/collections`

Die Collections, die dieser Aufrufer lesen darf.

```bash
curl -s https://tools.example.com/api/v1/tools/collections \
  -H "X-Tools-Service-Token: $TOOLS_SERVICE_TOKEN" \
  -H "Authorization: Bearer $WEAVE_TOOLS_TOKEN"
```

```python
resp = requests.get(
    "https://tools.example.com/api/v1/tools/collections",
    headers={
        "X-Tools-Service-Token": TOOLS_SERVICE_TOKEN,
        "Authorization": f"Bearer {WEAVE_TOOLS_TOKEN}",
    },
)
print(resp.json())
```

**Antwort (Beispiel)**

```json
[
  { "slug": "vertragsvorlagen", "name": "Vertragsvorlagen", "description": "…" }
]
```

#### `POST /api/v1/tools/search`

Hybride Suche über die eigenen lesbaren Collections.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `query` | `string`, min. 1 Zeichen | ja | Suchtext |
| `collection` | `string \| null` | nein | grenzt auf eine Collection ein — nur wirksam innerhalb der eigenen Rechte |
| `top_k` | `int`, 1–200 | nein | Trefferzahl, Default dienstseitig |

```bash
curl -s -X POST https://tools.example.com/api/v1/tools/search \
  -H "X-Tools-Service-Token: $TOOLS_SERVICE_TOKEN" \
  -H "Authorization: Bearer $WEAVE_TOOLS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Kündigungsfrist", "top_k": 5}'
```

```python
resp = requests.post(
    "https://tools.example.com/api/v1/tools/search",
    headers={
        "X-Tools-Service-Token": TOOLS_SERVICE_TOKEN,
        "Authorization": f"Bearer {WEAVE_TOOLS_TOKEN}",
        "Content-Type": "application/json",
    },
    json={"query": "Kündigungsfrist", "top_k": 5},
)
print(resp.json())
```

**Antwort (Beispiel, gekürzt)**

```json
{
  "query": "Kündigungsfrist",
  "results": [
    {
      "text": "Die ordentliche Kündigungsfrist beträgt …",
      "source": { "document": "Vertragsvorlage", "document_id": "doc-123", "chunk_id": 4, "page": "3", "collection": "vertragsvorlagen" }
    }
  ]
}
```

> **Tipp:** `document_id`/`chunk_id` sind stabile Kennungen — nützlich, um Treffer aus mehreren Suchen zu deduplizieren oder zu referenzieren.

---

## Weave-Ingest — Wissensbereiche verwalten & Dokumente einspielen

Alle Endpunkte hier verlangen entweder eine Browser-Session (Cookie) oder ein persönliches API-Token (`Authorization: Bearer pd_…`), siehe „Authentifizierung“ oben.

### Persönliche API-Tokens

#### `POST /api/v1/auth/tokens`

Erzeugt ein neues Token für den eingeloggten Benutzer (**nur mit Session-Cookie**, siehe Hinweis oben). Maximal 50 Tokens pro Benutzer.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `name` | `string`, 1–100 Zeichen | ja | Bezeichnung, z. B. „CI-Pipeline“ |
| `expires_in_days` | `int`, 1–3650 \| `null` | nein | Ablauf; `null`/fehlend = kein Ablauf |

```bash
curl -s -X POST https://ingest.example.com/api/v1/auth/tokens \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"name": "CI-Pipeline", "expires_in_days": 90}'
```

**Antwort (Beispiel)**

```json
{
  "id": "tok_8f2a…",
  "name": "CI-Pipeline",
  "token": "pd_9sQ3…",
  "token_prefix": "pd_9sQ3xy",
  "created_at": "2026-09-27T09:00:00Z",
  "expires_at": "2026-12-26T09:00:00Z"
}
```

> **Wichtig:** Das Feld `token` erscheint nur in dieser einen Antwort. `GET /api/v1/auth/tokens` zeigt danach nur noch `token_prefix` — ein verlorenes Token muss durch ein neues ersetzt werden.

#### `GET /api/v1/auth/tokens` / `DELETE /api/v1/auth/tokens/{token_id}`

Auflisten bzw. Löschen eigener Tokens (ebenfalls nur mit Session-Cookie).

```bash
curl -s -X DELETE https://ingest.example.com/api/v1/auth/tokens/tok_8f2a... \
  -b cookies.txt
```

### Wissensbereiche (Collections)

Rollen an einem Wissensbereich: **owner** (Metadaten ändern, Freigaben verwalten, löschen), **member** (Dokumente hochladen/pflegen/zurückziehen) und **reader** (im Chat/Suche verwenden) — siehe ADR 0008.

#### `GET /api/v1/collections`

Alle Wissensbereiche, die der Aufrufer lesen darf (öffentliche, eigene Freigaben, als Admin: alle).

```bash
curl -s https://ingest.example.com/api/v1/collections \
  -H "Authorization: Bearer $INGEST_TOKEN"
```

#### `POST /api/v1/collections`

Legt einen neuen Wissensbereich an. Der Ersteller wird automatisch Owner.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `description` | `string` | **ja** (Zweck) | Kurzbeschreibung des Zwecks — leer/fehlend → `422 "description (purpose) is required"` |
| `name` | `string` | nein | Anzeigename; leer → fällt auf den Slug zurück |
| `slug` | `string` \| `null` | nein | eigener Kurzname (`a-z0-9-`); fehlt → wird generiert |
| `visibility` | `"public"` \| `"restricted"` \| `null` | nein | Default: `restricted`, sobald `grants` gesetzt sind, sonst `public` |
| `responsible_team_id` | `string` \| `null` | nein | rein informativ, kein Recht |
| `grants` | `list[{user_id? , team_id?, role}]` | nein | zusätzliche Freigaben; `role` ∈ `owner`/`member`/`reader` (Teams nie `owner`) |
| `department`, `email`, `folder`, `subfolder`, `password` | `string` | nein | Verarbeitungsvorgaben bzw. optionaler Dokumentenschutz |

```bash
curl -s -X POST https://ingest.example.com/api/v1/collections \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Vertragsvorlagen",
        "description": "Vorlagen und Musterverträge für den Vertrieb",
        "responsible_team_id": "team_vertrieb",
        "grants": [{"team_id": "team_vertrieb", "role": "member"}]
      }'
```

```python
resp = requests.post(
    "https://ingest.example.com/api/v1/collections",
    headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
    json={
        "name": "Vertragsvorlagen",
        "description": "Vorlagen und Musterverträge für den Vertrieb",
        "responsible_team_id": "team_vertrieb",
        "grants": [{"team_id": "team_vertrieb", "role": "member"}],
    },
)
resp.raise_for_status()
collection = resp.json()
print(collection["collection_id"], collection["slug"])
```

**Antwort (Beispiel, gekürzt)**

```json
{
  "collection_id": "coll_7a1c…",
  "slug": "vertragsvorlagen",
  "name": "Vertragsvorlagen",
  "description": "Vorlagen und Musterverträge für den Vertrieb",
  "visibility": "restricted",
  "role": "owner",
  "can_manage": true,
  "can_upload": true,
  "grants": [
    { "user_id": "usr_1", "name": "alice", "role": "owner", "team": "Vertrieb" },
    { "team_id": "team_vertrieb", "name": "Vertrieb", "role": "member" }
  ],
  "job_ids": []
}
```

> **Hinweis:** `description` ist fachlich der „Zweck“ des Wissensbereichs (siehe ADR 0008) — technisch heißt das Feld weiterhin `description`, nicht `purpose`.

#### `GET /api/v1/collections/{id}` / `PATCH /api/v1/collections/{id}`

`PATCH` ändert nur die mitgeschickten Felder (Rest bleibt unverändert); `grants` ersetzt die komplette Freigabeliste und muss mindestens einen Owner enthalten.

```bash
curl -s -X PATCH https://ingest.example.com/api/v1/collections/coll_7a1c... \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "Aktualisierte Vertragsvorlagen 2026"}'
```

#### `DELETE /api/v1/collections/{id}`

| Parameter | Typ | Bedeutung |
|---|---|---|
| `with_content` | `bool`, Default `false` | ohne dieses Flag nur leere Wissensbereiche löschbar |
| `confirm_name` | `string` \| `null` | muss bei `with_content=true` exakt dem Namen entsprechen |

```bash
curl -s -X DELETE "https://ingest.example.com/api/v1/collections/coll_7a1c...?with_content=true&confirm_name=Vertragsvorlagen" \
  -H "Authorization: Bearer $INGEST_TOKEN"
```

> **Wichtig:** Bereits freigegebene (released) Dokumente werden beim Löschen mit `with_content=true` automatisch aus dem Wissensindex zurückgezogen. Laufende Confluence-Importe, passwortgeschützte Dokumente sowie ein Bot oder eine aktive technische Identität, die diesen Slug noch nutzen, verhindern das Löschen — sonst würde ein späterer, gleichnamiger Wissensbereich deren Zugriff erben.

### Dokumente hochladen und verarbeiten

#### `POST /api/v1/collections/{id}/upload`

`multipart/form-data`-Upload einer einzelnen Datei in einen Wissensbereich. Erfordert mindestens die Rolle `member`.

```bash
curl -s -X POST https://ingest.example.com/api/v1/collections/coll_7a1c.../upload \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -F "file=@Vertragsvorlage.pdf" \
  -F "tags=vertrieb,vorlage"
```

```python
with open("Vertragsvorlage.pdf", "rb") as f:
    resp = requests.post(
        "https://ingest.example.com/api/v1/collections/coll_7a1c.../upload",
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        files={"file": f},
        data={"tags": "vertrieb,vorlage"},
    )
resp.raise_for_status()
print(resp.json())
```

**Antwort**

```json
{ "job_id": "job_4f9b…", "status": "PENDING" }
```

#### `POST /api/v1/collections/{id}/start`

Startet die Verarbeitung aller hochgeladenen, noch nicht gestarteten Dokumente eines Wissensbereichs mit einem gewählten Verarbeitungsprofil.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `profile_id` | `string`, min. 1 Zeichen | ja | Verarbeitungsprofil |
| `webhook_connection_id` | `string` \| `null` | nein | optionale Benachrichtigung bei Abschluss |

```bash
curl -s -X POST https://ingest.example.com/api/v1/collections/coll_7a1c.../start \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile_id": "standard"}'
```

**Antwort**

```json
{ "collection_id": "coll_7a1c…", "profile_id": "standard", "started_jobs": 3 }
```

> **Hinweis:** Ohne mindestens ein hochgeladenes Dokument antwortet dieser Endpunkt mit `409 "No files uploaded to collection"`.

### Job- und Dokumentstatus

#### `GET /api/v1/jobs/{id}`

Status eines einzelnen Verarbeitungsjobs/Dokuments.

```bash
curl -s https://ingest.example.com/api/v1/jobs/job_4f9b... \
  -H "Authorization: Bearer $INGEST_TOKEN"
```

**Antwort (Beispiel, gekürzt)**

```json
{
  "id": "job_4f9b…",
  "original_filename": "Vertragsvorlage.pdf",
  "status": "FINISHED",
  "document_version": 1,
  "created_at": "2026-09-27T09:05:00Z",
  "updated_at": "2026-09-27T09:06:30Z",
  "owner": { "id": "usr_1", "username": "alice" },
  "tags": ["vertrieb", "vorlage"]
}
```

`status` ist eines von `PENDING`, `RUNNING`, `FINISHED`, `FAILED`.

> **Tipp:** Für Integrationen, die auf Fertigstellung warten, empfiehlt sich Polling auf diesen Endpunkt (kein Webhook-Push ist Teil dieser Integrator-Referenz, mit Ausnahme des optionalen `webhook_connection_id` bei `/start`).

### Dokument freigeben (in den Wissensindex)

#### `POST /api/v1/portal/documents/{job_id}/release`

Gibt ein fertig verarbeitetes, geprüftes Dokument für die Wissenssuche/Chat frei.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `markdown_sha256` | `string` (64 Hex-Zeichen) | ja | Prüfsumme des zuletzt angesehenen Markdown-Stands (verhindert Freigabe eines veralteten Stands) |
| `accept_quality_warning` | `bool`, Default `false` | nein | muss `true` sein, wenn die Qualitätsprüfung „C“ ergeben hat |

```bash
curl -s -X POST https://ingest.example.com/api/v1/portal/documents/job_4f9b.../release \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"markdown_sha256": "3b1f2a...(64 Hex-Zeichen)"}'
```

**Antwort**

```json
{ "id": "rel_1a2b…", "created_at": "2026-09-27T09:10:00Z", "status": "pending", "released_by": "alice" }
```

`status` läuft asynchron über `pending` → `sent`/`failed`.

> **Wichtig:** Der Job muss `FINISHED` sein, ein zugehöriger Wissensbereich muss bekannt sein, und der Aufrufer braucht mindestens die Rolle `member` auf diesem Wissensbereich. Weicht `markdown_sha256` vom aktuellen Stand ab, antwortet der Endpunkt mit `409` — vorher also den aktuellen Markdown-Stand erneut abrufen.

### Dokument löschen (inkl. Rückzug aus dem Index)

#### `DELETE /api/v1/jobs/{id}`

| Parameter | Typ | Bedeutung |
|---|---|---|
| `withdraw` | `bool`, Default `false` | **muss `true` sein**, wenn das Dokument bereits per `release` freigegeben wurde |
| `password` | `string` \| `null` | für passwortgeschützte Dokumente |

```bash
curl -s -X DELETE "https://ingest.example.com/api/v1/jobs/job_4f9b...?withdraw=true" \
  -H "Authorization: Bearer $INGEST_TOKEN"
```

> **Wichtig:** Ohne `withdraw=true` schlägt das Löschen eines bereits freigegebenen Dokuments mit `409 "Job has an issued portal release and cannot be deleted"` fehl. Mit `withdraw=true` wird der Rückzug aus dem Wissensindex angestoßen — eine spätere erneute Freigabe desselben Jobs wird ignoriert (Tombstone), es muss ein neues Dokument hochgeladen werden.

### Bots (Fachseite/Owner)

#### `GET /api/v1/bots`

Die verwalteten Bots, deren Owner der Aufrufer ist (auch für Admins nur die eigenen).

```bash
curl -s https://ingest.example.com/api/v1/bots \
  -H "Authorization: Bearer $INGEST_TOKEN"
```

**Antwort (Beispiel, gekürzt)**

```json
{
  "items": [
    {
      "id": "rechtsberatung",
      "kind": "llm",
      "name": "Rechtsberatung intern",
      "description": "Beantwortet Fragen zu internen Richtlinien.",
      "enabled": true,
      "collections": ["vertragsvorlagen"],
      "require_sources": true,
      "no_context_reply": "Dazu finde ich leider keine Informationen.",
      "public": false,
      "grants": [{ "user_id": "usr_1", "name": "alice", "role": "owner" }],
      "updated_at": "2026-09-20T08:00:00Z"
    }
  ]
}
```

#### `PATCH /api/v1/bots/{bot_id}`

Owner pflegen Inhalt und Nutzerfreigaben ihres Bots — die technische Anbindung (Webhook, Geheimnis, Zeitlimit) bleibt Admins vorbehalten.

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `description` | `string`, ≤ 4000 Zeichen \| `null` | nein | |
| `system_prompt` | `string`, ≤ 12000 Zeichen \| `null` | nein | |
| `collections` | `list[string]`, ≤ 500 \| `null` | nein | nur Wissensbereiche, die der Owner selbst lesen darf |
| `require_sources` | `bool` \| `null` | nein | |
| `no_context_reply` | `string`, 1–2000 Zeichen \| `null` | nein | |
| `public` | `bool` \| `null` | nein | `true` = für alle freigegeben |
| `grants` | `list[{user_id?, team_id?, role}]`, ≤ 500 \| `null` | nein | ersetzt die Nutzerfreigaben komplett; `role` hier ist die Bot-Rolle (`user`) |

```bash
curl -s -X PATCH https://ingest.example.com/api/v1/bots/rechtsberatung \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"require_sources": true, "collections": ["vertragsvorlagen", "arbeitsrecht"]}'
```

> **Hinweis:** Ein Owner kann einem Bot nur Wissensbereiche zuordnen, die er selbst lesen darf (siehe „Bots antworten nur aus lesbaren Bereichen“ ganz oben) — Anlegen, Löschen und die technische Anbindung eines Bots bleiben in jedem Fall der Administration vorbehalten.

---

## Fehlercodes / Rate-Limits

Alle drei Dienste folgen demselben Grundmuster: Standard-HTTP-Statuscodes, Fehlermeldungen als `{"detail": "…"}` (FastAPI-Standard; bei `422` eine strukturierte Liste einzelner Feldfehler).

| Status | Bedeutung | Kommentar |
|---|---|---|
| `400` | Ungültige Anfrage | z. B. abgelaufener/bereits verwendeter Übergabe-Code bei der Weave-API-Anmeldung |
| `401` | Nicht authentifiziert | fehlender/ungültiger/abgelaufener Token oder Session; die genaue Ursache wird nie verraten |
| `403` | Kein Zugriff | z. B. Bot nicht freigegeben, oder Token-Verwaltung per Bearer-Token versucht (Weave-Ingest) |
| `404` | Nicht gefunden | gilt auch für „gehört jemand anderem“ — bewusst ununterscheidbar von „existiert nicht“ |
| `409` | Konflikt | z. B. Slug bereits vergeben, Wissensbereich nicht leer, Dokument bereits freigegeben |
| `422` | Validierungsfehler | fehlendes Pflichtfeld, falsches Format (z. B. `description` leer, `slug` mit Großbuchstaben) |
| `429` | Rate-Limit erreicht | siehe unten |
| `502` | Vorgelagerter Dienst nicht erreichbar/fehlerhaft | Weave-API, wenn Weave-Runtime nicht antwortet |
| `503` | Dienst (bewusst) nicht verfügbar | z. B. fehlend konfiguriertes Service-Secret, oder Weave-Ingest als Identitätsquelle nicht erreichbar |

### Rate-Limits

| Dienst | Limit (Default) | Fenster | Schlüssel | Antwort bei Überschreitung |
|---|---|---|---|---|
| Weave-API | 30 Anfragen/Minute (`RATE_LIMIT_PER_MINUTE`) | rollierend, 60 s, pro Prozess | pro angemeldetem Nutzer | `429` mit Header `Retry-After: <Sekunden>` |
| Weave-Ingest | 60 Anfragen/Minute (`RATE_LIMIT_PER_MINUTE`) | rollierend, 60 s, über Redis (mehrere Instanzen teilen sich das Limit) | pro Client (i. d. R. IP) | `429 "Rate limit exceeded"` |
| Weave-Tools | kein eigenes Rate-Limit | — | — | — (Begrenzung liegt beim `X-Tools-Service-Token`-Ausgeber bzw. den vorgelagerten Diensten) |

> **Hinweis:** Der Datei-Upload (`POST /api/v1/collections/{id}/upload`) ist in Weave-Ingest bewusst von der Rate-Begrenzung ausgenommen, damit ein Batch mit vielen Dateien nicht mittendrin blockiert wird.

---

## Nicht Teil dieser Referenz

Neben den oben beschriebenen Endpunkten bieten Weave-Ingest und Weave-API weitere, ausschließlich für den Betrieb gedachte Schnittstellen: Administrationsendpunkte (`/api/v1/auth/admin/*` in Weave-Ingest, u. a. Nutzer-, Team-, Provider- und technische-Identitäten-Verwaltung) sowie rein interne, dienst-zu-dienst genutzte Endpunkte (`/internal/*` bzw. `/api/v1/internal/*`, z. B. Token-Introspektion oder die Bot-Registry-Abfrage von Weave-Runtime). Diese sind absichtlich nicht Teil dieser Integrator-Referenz.
