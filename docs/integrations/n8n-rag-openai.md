# n8n-Beispielflow: Weave-RAG mit externem OpenAI-kompatiblem LLM

Importierbarer n8n-Workflow: [n8n-rag-openai.workflow.json](n8n-rag-openai.workflow.json).

Der Flow ist ein Bot-Provider nach [contracts/n8n-flow.md](../../contracts/n8n-flow.md):
Weave-Runtime delegiert einen Chat-Turn an n8n, n8n sucht mit den Rechten des
fragenden Menschen über Weave-Tools im RAG und lässt die Treffer von einem
beliebigen OpenAI-kompatiblen Endpunkt (vLLM, Ollama, LiteLLM, OpenAI, …)
beantworten.

```text
Weave-Runtime ──POST + X-Weave-Signature──▶ Webhook
  → Signatur prüfen (HMAC über Roh-Body)      ─ ungültig → 401
  → Konfiguration (LLM-URL, Modell, top_k, Prompt)
  → Weave-Tools: POST {tools_base_url}/api/v1/tools/search
       Authorization: Bearer <delegation_token>
  → Kontext aufbauen ([1]…[n] + sources)      ─ keine Treffer → leere Antwort
  → LLM: POST {llm_base_url}/chat/completions
  → Antwort formen → {answer, sources} an Weave-Runtime
```

## Einrichtung in n8n

1. Umgebungsvariablen der n8n-Instanz (bei externen Task-Runnern auch am Runner):
   - `WEAVE_DELEGATION_SECRET` — identisch mit Weave-Runtime und Weave-Tools
   - `NODE_FUNCTION_ALLOW_BUILTIN=crypto` — für die HMAC-Prüfung im Code-Node
   - `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` — damit der Code-Node das Secret lesen darf
2. Workflow importieren (*Import from File*).
3. Credential **LLM API-Key** (Typ *Bearer Auth*) mit dem Key des LLM-Endpunkts
   anlegen und am Node *LLM: Chat Completion* auswählen (ohne Key:
   Authentifizierung auf *None*). Für Weave braucht n8n keine gespeicherten
   Zugangsdaten — die Suche läuft mit dem Delegations-Token aus dem Webhook.
4. Node **Konfiguration** anpassen: `llm_base_url` (inkl. `/v1`), `llm_model`,
   optional `top_k`, `temperature`, `max_history`, `system_prompt`.
5. Workflow aktivieren. Produktions-URL: `https://<n8n>/webhook/weave-rag-openai`.

## Einrichtung in Weave-Runtime

In Weave-Tools `TOOLS_REST_REQUIRE_SERVICE_TOKEN=false` setzen (weave.yaml →
`render`), damit die REST-Suche allein mit `Authorization: Bearer` funktioniert.
Bleibt der Schalter auf `true`, muss der Such-Node zusätzlich den Header
`X-Tools-Service-Token: <TOOLS_API_TOKEN>` senden.

Bot nach [services/runtime/bots/n8n-agent.yaml.example](../../services/runtime/bots/n8n-agent.yaml.example) anlegen:

- `n8n.webhook_url` auf die Produktions-URL oben setzen, `n8n.streaming: false`
- deren Basis-URL in `N8N_ALLOWED_BASE_URLS` eintragen
- `TOOLS_BASE_URL` auf die **von n8n aus** erreichbare Weave-Tools-URL setzen
- `retrieval.collections` bestimmt, welche Collections überhaupt ins Delegations-Token gelangen

## Manuell testen

Weave-Tools akzeptiert im `Authorization`-Header auch ein persönliches Token.
Für einen Test ohne Weave-Runtime lässt sich der Body daher selbst signieren:

```bash
BODY='{"allowed_collections":[],"bot_id":"n8n-agent","delegation_token":"<PERSÖNLICHES_TOKEN>","history":[],"message":"Welche Verträge laufen nächsten Monat aus?","tools_base_url":"http://weave-tools-backend:8000","user":{"id":"test","team":null,"teams":[],"username":"test"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEAVE_DELEGATION_SECRET" -hex | sed 's/^.* //')
curl -sS https://<n8n>/webhook/weave-rag-openai -H 'Content-Type: application/json' -H "X-Weave-Signature: $SIG" --data-binary "$BODY"
```

## Andere Tools: Bearer gegen Weave

| Ziel | Token als `Authorization: Bearer …` | Ergebnis |
|---|---|---|
| Weave-Tools REST `/api/v1/tools/search` (bei `TOOLS_REST_REQUIRE_SERVICE_TOKEN=false`) oder MCP `/mcp` | `wti_…` einer technischen Identität (Ingest-Admin → *Technische Identitäten*, Collections explizit freigeben) | Rohe RAG-Treffer zur Weiterverarbeitung mit eigenem LLM |
| Weave-API `/v1/chat/completions` (OpenAI-kompatibel, `model` = Bot-ID) | persönliches API-Token | Fertige Bot-Antwort von Weave |

Für eigenständige n8n-Flows ohne Weave-Runtime (z. B. Zeitplan) ersetzt ein
`wti_`-Token als *Bearer Auth*-Credential am Such-Node das Delegations-Token;
Webhook und Signaturprüfung entfallen dann.

## Hinweise

- **Keine Ausführungsdaten:** Der Workflow speichert bewusst keine Executions,
  weil Webhook-Body und Header das Delegations-Token enthalten (Vertrag,
  Abschnitt „Geheimhaltung“). Zum Debuggen nur in einer Testumgebung und nur
  vorübergehend in den Workflow-Settings einschalten.
- **Rechte:** Der Flow vertraut nie `allowed_collections` aus dem Body. Den
  Umfang erzwingt Weave-Tools anhand des Tokens; Weave-Runtime filtert die
  zurückgemeldeten `sources` zusätzlich gegen den signierten Umfang.
- **Quellen:** Alle Treffer werden als `sources` zurückgegeben, in derselben
  Reihenfolge wie die Nummern `[1]…[n]` im LLM-Kontext.
- **Keine Treffer:** Der LLM-Aufruf entfällt; bei `guard.require_sources: true`
  ersetzt Weave-Runtime die Antwort durch `no_context_reply`.
- **Transport:** Bearer-Tokens nur über HTTPS oder innerhalb eines
  abgeschlossenen Container-Netzes übertragen.
- **Fehler** (Weave-Tools 401/503, LLM nicht erreichbar) lassen den Flow
  scheitern; Weave-Runtime meldet dem Nutzer dann `503`.
- **Varianten:** eine Collection gezielt durchsuchen (`collection` im
  Such-Body, wirkt nur als Filter innerhalb des Tokens), Folgefragen vor der
  Suche per LLM umformulieren.
