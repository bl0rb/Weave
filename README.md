# Weave-Tools

## Einordnung

Weave-Tools ist die **Action-Layer** des Weave-Systems. Sie stellt eine einheitliche Tool-Registry und Execution-Engine für externe Integrationen bereit, die von Weave-Runtime aus aufgerufen werden. Tools werden YAML-basiert konfiguriert und unterliegen Bot-spezifischen Berechtigungen.

## Zweck

- Tool-Registry für Action-Intents: REST-APIs, MCP, E-Mail, CRM, Web-Scraping
- Berechtigungen pro Bot aus YAML-Konfiguration erzwingen
- Webhook-Handling mit HMAC-SHA256-Signatur und exponentiellen Retries
- Error-Handling und Audit-Logging
- Abstraktion von Integrations-Details gegenüber Runtime

## Verantwortlichkeiten

- Tool-Discovery und Metadaten-Verwaltung
- Authentifizierung gegenüber externen Services (API-Keys, OAuth)
- Berechtigungs-Check vor jedem Tool-Aufruf
- Webhook-Verwaltung und Retry-Logik (Patterns aus Weave-Ingest)
- Timeout und Rate-Limiting pro Tool

## Nicht-Ziele / Abgrenzung

- **Keine Nutzer-Auth**: Authorization erfolgt Bot-basiert
- **Keine Tool-Implementierung**: Tools sind externe Services
- Keine Konversations-Logik: Reine Execution-Engine
- Keine Indexierung oder Suche: Stateless Request-Response

## Schnittstellen

**Input:**
- POST `/execute`: Tool-ID, Parameters, Bot-Context (für Permissions)

**Output:**
- JSON: Result (oder Error mit HTTP-Status), Execution-Time, Retry-Info

**Abhängigkeiten:**
- Externe Services: REST-APIs, MCP-Server, E-Mail-Gateways, CRM
- Weave-API (optional): Für Webhook-Callbacks an Nutzer

## Status

**Phase 5** — Nicht begonnen. Abhängig von Weave-Runtime und Weave-API Stabilität.