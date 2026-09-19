# Vertrag: Technische Identitäten (Schritt 5)

**Vertrag-Version:** 1
**Status:** angenommen (Weave-Ingest und Weave-Tools sind in diesem Monorepo implementiert)
**Anbieter (Owner):** Weave-Ingest — verwaltet technische Identitäten (`services/ingest/backend/app/api/technical_identities.py`: Admin-CRUD, Token-Ausgabe/-Rotation/-Widerruf, Audit-Log) und beantwortet die Introspektion.
**Konsument (Caller dieses Vertrags):** Weave-Tools (`services/tools/app/services/scope.py`s `_resolve_technical_scope`), als dritter Zweig neben Personal- und Delegations-Token-Auflösung.

## Zweck

Schritt 5 unterscheidet drei Zugriffsmodelle auf Weave-Tools' Wissenssuche:

1. **Anwendung im Auftrag eines Menschen** → kurzlebiges Delegations-Token mit dem eingeschränkten Scope des Nutzers (existiert bereits, siehe `contracts/n8n-flow.md`).
2. **Eigenständige Integration** → **technische Identität** mit explizit gewährten Collections (dieser Vertrag).
3. **Externer KI-Agent** → dieselben Rechte über MCP, mit demselben Token-Format und derselben Prüfung wie 2.

Eine technische Identität ist kein Nutzerkonto und kein Bot: sie ist ein eigenständiger, administrierter Berechtigungsnachweis für ein System AUSSERHALB von Weave — z. B. ein externer MCP-Client oder eine standalone Integration, die per REST auf Weave-Tools zugreift. Eine neu angelegte Identität hat **keinen** Wissenszugriff, bis ein Administrator explizit Collections zuweist (`allowed_collections: []` per Default). Collection-Parameter in einer Anfrage dürfen diesen Umfang nur EINSCHRÄNKEN, nie erweitern — dieselbe Regel wie bei Personal- und Delegations-Token (siehe `contracts/n8n-flow.md`s "Grundregel Rechte").

Beide Oberflächen von Weave-Tools (MCP und REST) nutzen exakt dieselbe Rechteprüfung (`resolve_scope()`), also gilt dieser Vertrag für beide gleichermaßen — es gibt keinen separaten Code-Pfad für "MCP-Aufrufer" gegenüber "REST-Aufrufer".

## Token-Format

Ausgestellt von Weave-Ingest, nie von Weave-Tools selbst:

```
wti_<32+ Bytes urlsicherer Zufall>
```

Das Präfix `wti_` ("Weave Technical Identity") ist bewusst anders als Ingests eigene Personal-API-Tokens (`pd_...`, `services/ingest/backend/app/api/auth.py`) — Weave-Tools' `resolve_scope()` erkennt eine technische Identität AUSSCHLIESSLICH an diesem Präfix, rein strukturell (nach dem Delegations-Token-Test auf genau einen `.`, vor dem Fallback auf die Personal-Token-Introspektion bei Weave-API), nie an einem client-deklarierten "Typ"-Feld.

Wie ApiToken/Session wird NIE der Rohwert gespeichert — nur `sha256(token)` in `technical_identities.token_hash`. Der Rohwert wird genau einmal angezeigt: bei Erstellung (`POST /api/v1/auth/admin/technical-identities`) oder Rotation (`POST .../{id}/rotate`).

## Introspektions-Vertrag

`POST {INGEST_BASE_URL}/api/v1/internal/technical-identities/introspect`

**Auth:** `Authorization: Bearer <TOOLS_INTROSPECTION_TOKEN>` — ein statisches, geteiltes Service-Secret zwischen Weave-Tools (Caller) und Weave-Ingest (Verifier), analog zu `INTROSPECTION_SERVICE_TOKEN` zwischen Weave-Tools und Weave-API, aber ein eigenständiges Secret (andere Upstream-Instanz, anderer Identitäts-Speicher). Fehlt es oder ist es falsch: `503`/`401`.

**Request:**

```json
{"token": "wti_<raw-value-vom-aufrufer>"}
```

**Response** — nicht-enumerierend, spiegelt Weave-APIs eigenen `/internal/tokens/introspect`-Vertrag exakt: unbekannt, widerrufen, deaktiviert und abgelaufen ergeben alle dieselbe Antwort, niemals unterscheidbar:

```json
{"active": false}
```

oder, für eine aktive Identität:

```json
{
  "active": true,
  "identity_id": "…",
  "name": "n8n-integration",
  "allowed_collections": ["handbuch", "faq"],
  "expires_at": null
}
```

`last_used_at` wird dabei höchstens einmal pro Minute aktualisiert (dieselbe Schranke wie bei Personal-API-Tokens). Ein abgelehnter Versuch (gefunden, aber widerrufen/deaktiviert/abgelaufen) erzeugt eine Audit-Zeile (`event: introspected_denied`); eine unbekannte Token-Zeichenkette nicht (es gibt keine `identity_id`, an die sich diese Zeile hängen ließe).

## Scope-Auflösung in Weave-Tools

`app/services/scope.py`s `resolve_scope()` erkennt einen `wti_`-präfigierten Bearer-Token als dritten Zweig (neben genau-einem-Punkt für Delegations-Token und allem anderen für Personal-Token) und liefert:

```python
Scope(
    kind='technical',
    user_id=identity_id,
    username=name,
    team=None,
    teams=None,
    allowed_collections=allowed_collections,  # exakt Ingests Antwort, unverändert
)
```

`scope.kind == 'technical'` dient ausschließlich Logging/Audit — keine Zugriffsentscheidung in Weave-Tools verzweigt jemals darauf; die Durchsetzung ist exakt dieselbe generische Schnittmenge wie bei jedem anderen Scope (`app/services/tools.py`s `list_collections_for_scope`/`search_for_scope`): eine angefragte Collection außerhalb von `allowed_collections` ergibt ein leeres Ergebnis, nie einen Fehler, nie einen Hinweis auf deren Existenz.

**Die eine dokumentierte Ausnahme von "nie cachen":** dieser Pfad hält einen In-Prozess-Cache mit TTL `TECHNICAL_IDENTITY_CACHE_SECONDS` (Default 45s, Bereich 30–60s), keyed auf `sha256(token)`. Ein widerrufenes/geändertes technisches Identität kann dadurch bis zu dieser TTL nachwirken — ein bewusster, begründeter Kompromiss (siehe `scope.py`s Modul-Docstring), NICHT auf Personal- oder Delegations-Token übertragen, wo Widerruf weiterhin sofort wirkt.

## Audit-Log

Zwei getrennte, sich ergänzende Audit-Spuren:

- **Weave-Ingest** (`technical_identity_audit`-Tabelle): eine Zeile pro Verwaltungsaktion (`created`, `rotated`, `revoked`, `updated`) mit Akteur (Admin-Username) und Details (nie das Token selbst), sowie pro abgelehntem Introspektions-Versuch (`introspected_denied`). Einsehbar im Admin-Tab „Technische Identitäten" pro Identität.
- **Weave-Tools** (strukturierte Log-Zeile, `app/services/tools.py`s `_audit`): eine Zeile pro Werkzeugaufruf (`list_collections`/`search`) mit `kind`, `caller` (identity_id/user_id), angefragter Collection und Trefferzahl oder `denied=True` — nie das Bearer-Token, nie Dokumenttext.

## Verwendung über MCP und REST

Ein externes System spricht Weave-Tools identisch zu jedem anderen Aufrufer an — der einzige Unterschied ist der Token selbst:

```
GET  {TOOLS_BASE_URL}/api/v1/tools/collections
POST {TOOLS_BASE_URL}/api/v1/tools/search
Authorization: Bearer wti_<raw-value>
```

oder über MCP (`streamable-HTTP`, `{TOOLS_BASE_URL}/mcp` bzw. der eigene `weave-tools-mcp`-Dienst):

```python
async with streamablehttp_client(f'{TOOLS_MCP_URL}/mcp', headers={'Authorization': f'Bearer {token}'}) as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        await session.call_tool('search', {'query': 'Urlaubsantrag'})
```

Weave-Tools verlangt für REST zusätzlich `X-Tools-Service-Token` (die grobe „darf dieser Aufrufer Weave-Tools überhaupt erreichen"-Tür, siehe `services/tools/README.md`) — für MCP gibt es diese Tür bewusst nicht, dort entscheidet allein der `Authorization`-Header.

## Interne Retrieval-Zugangsdaten werden nie herausgegeben

Weder REST noch MCP geben `RETRIEVAL_API_TOKEN` (Weave-Tools' eigenes Credential gegenüber Weave-Retrieval) jemals an einen Aufrufer weiter — externe Systeme erreichen Wissen ausschließlich über Weave-Tools' eigene, scope-geprüfte Endpunkte, nie direkt gegen Weave-Retrieval.
