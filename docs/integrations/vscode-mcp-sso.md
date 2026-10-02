# VS Code mit Weave-MCP über SSO verbinden

VS Code übernimmt den OAuth-Login mit Authorization Code und PKCE beim
ausgewählten Identity Provider. Weave-MCP ist der Resource Server und
veröffentlicht OAuth Protected Resource Metadata. Weave-Ingest prüft
signierte JWT-Access-Tokens und ordnet sie vorhandenen, aktiven Nutzern zu.
Es werden weder Nutzer angelegt noch Teamrechte aus Token-Claims übernommen.
Bestehende Personal-, Delegations- und technische Tokens bleiben nutzbar.

## Voraussetzungen

- Vor dem Start des geänderten Ingest-Backends Migration
  `0041_oidc_object_identity` mit `alembic upgrade head` und der richtigen
  Datenbankkonfiguration ausführen; sie ist auch für Keycloak erforderlich.
- Einen aktivierten SSO-Provider in Weave-Ingest konfigurieren. Für diesen
  MCP-Endpunkt wird genau ein Provider ausgewählt; Entra und Keycloak
  werden beide unterstützt, mit den unten genannten Einstellungen.
- Einen separaten öffentlichen OAuth-Client für VS Code registrieren,
  Authorization Code mit PKCE S256 aktivieren, kein Client-Secret verteilen.
  Die von VS Code verwendete Callback-URL beim Provider erlauben. VS Code
  unterstützt je nach Ausführungsumgebung unter anderem
  `http://127.0.0.1:33418` und `https://vscode.dev/redirect`; die tatsächlich
  angefragte Redirect-URI muss zur Registrierung passen.
- Eine eigene MCP-Ressource/Audience und den delegierten Scope `mcp.read`
  beim Provider definieren. Audience der Zugriffstokens darf nicht die
  VS-Code-/Portal-Client-ID sein: ID-Tokens sind keine MCP-Zugriffstokens.
  Der Provider muss die vom MCP-Client gesendeten Resource Indicators und
  Scope-Anfragen für diese Ressource unterstützen bzw. passend konfigurieren.
- Öffentliche HTTPS-URL für `/mcp` bereitstellen. Zusätzlich müssen
  `/.well-known/oauth-protected-resource` und
  `/.well-known/oauth-protected-resource/mcp` denselben MCP-Dienst erreichen.
  Bei einem externen Pfadpräfix lautet der zweite Discovery-Pfad
  `/.well-known/oauth-protected-resource` plus vollständigem Ressourcenpfad;
  diese Route muss der Reverse Proxy ebenfalls durchreichen.
- Weave-Ingest und Weave-Tools müssen denselben `TOOLS_INTROSPECTION_TOKEN`
  verwenden. Dieses Service-Secret wird niemals in VS Code eingetragen.

## Server konfigurieren

Die Werte stehen in `weave.yaml` unter `services.ingest.settings` und
`services.tools.settings`. Für Compose können entsprechende Umgebungswerte
vor dem Rendern gesetzt werden. `MCP_OAUTH_SCOPES` enthält die vom Client
anzufordernden Scopes; `MCP_OAUTH_REQUIRED_SCOPES` die erwarteten Scope-
Namen im Zugriffstoken. Bei Helm werden die Einstellungen mit
`python scripts/weave_config.py helm-values` in `config.ingest` bzw.
`config.tools` übertragen. Geänderte Dienste neu bauen und bereitstellen;
eine bereits veröffentlichte Image-Version enthält diese Erweiterung nicht.

Keycloak-Beispiel (Adressen und Slug ersetzen):

```dotenv
# Weave-Tools
MCP_OAUTH_ISSUER=https://sso.example.org/realms/company
MCP_OAUTH_RESOURCE_URL=https://weave.example.org/mcp
MCP_OAUTH_SCOPES=["mcp.read"]

# Weave-Ingest
MCP_OAUTH_PROVIDER_SLUG=keycloak
MCP_OAUTH_AUDIENCE=https://weave.example.org/mcp
MCP_OAUTH_SUBJECT_CLAIM=sub
MCP_OAUTH_REQUIRED_SCOPES=["mcp.read"]
```

In Keycloak die Audience mittels Client Scope/Audience Mapper in das
Access Token aufnehmen und `mcp.read` als delegierten Scope bereitstellen.
Die Issuer-URL muss exakt zur Provider-Konfiguration und zum Token passen.
Für den ausgewählten Keycloak-Provider muss `sub` zwischen Portal- und
MCP-Access-Token dieselbe Benutzeridentität bezeichnen.

Entra: tenant-spezifischen v2-Issuer verwenden, etwa
`https://login.microsoftonline.com/<tenant-id>/v2.0`; kein `common`-Issuer.
Eine API-App-Registrierung für Weave-MCP und eine öffentliche Client-App
für VS Code registrieren und dieser die delegierte API-Berechtigung geben.
`MCP_OAUTH_AUDIENCE` entspricht dem tatsächlichen `aud` der v2-Zugriffstokens
(üblicherweise der Client-ID der API-App). Den tatsächlich ausgegebenen
`scp`-Wert als `MCP_OAUTH_REQUIRED_SCOPES` setzen (z. B. `["mcp.read"]`);
für `MCP_OAUTH_SCOPES` den vollständigen API-Scope verwenden
(z. B. `["api://<api-client-id>/mcp.read"]`). Entra-spezifische Scope-/Resource-
Konfiguration muss mit der verwendeten VS-Code-Version getestet werden.
Opaque Tokens und Microsoft-Graph-Tokens werden nicht akzeptiert.

Für Entra `MCP_OAUTH_SUBJECT_CLAIM=oid` setzen. Bestehende Nutzer
müssen sich danach einmal im Weave-Portal über Entra anmelden: dabei wird
die verifizierte `oid` gespeichert. Sie wird zusammen mit dem ausgewählten
Provider abgeglichen; E-Mail-Adressen dienen niemals zur Kontoverknüpfung.

## VS Code konfigurieren

Die Projektdatei `.vscode/mcp.json` verwendet OAuth ohne festen Bearer-Token.
Die öffentliche Client-ID wird beim Start abgefragt. Die URL an die
öffentliche MCP-Adresse anpassen, dann im klassischen Copilot-Chat
`MCP: List Servers` → `weave` → Start ausführen und die Browseranmeldung
abschließen. Unter den Chat-Tools `list_collections` und `search` aktivieren.

Die VS-Code-Agent-Host-Umgebung übernimmt Konfigurationen mit interaktiven
`${input:...}`-Variablen nicht. Dafür eine portable `.mcp.json` mit einem
`mcpServers`-Objekt verwenden und die öffentliche Client-ID direkt eintragen
(sie ist kein Geheimnis). Keine Access-/Refresh-Tokens ins Repository schreiben.

## Fehler und Prüfung

- Ohne Bearer-Token: HTTP 401 mit `WWW-Authenticate` und Discovery-Adresse.
- Ungültige Signatur, falscher Issuer/Audience, abgelaufenes Token,
  unbekannter oder deaktivierter Nutzer: kein Zugriff.
- Fehlender delegierter Scope: HTTP 403 mit `insufficient_scope`.
- Fehlkonfiguration oder nicht erreichbarer Identity Provider: HTTP 503.
- Persönliche und Team-Freigaben werden bei jedem HTTP-Aufruf neu aufgelöst,
  auch innerhalb einer vorhandenen MCP-Sitzung. JWT-Widerruf beim IdP selbst
  wird ohne dessen Introspection nicht erkannt; kurze Access-Token-Laufzeiten
  verwenden. Eine Deaktivierung in Weave greift beim nächsten Aufruf.

Automatische Tests decken signierte Tokens, beide Identitätsverfahren,
Discovery/Challenges und einen MCP-Protokollaufruf ab. Ein echter Browser-
Login ist erst mit Tenant/Realm, Audience, Client-ID und registrierter
Redirect-URI überprüfbar.

Referenzen: [VS-Code-MCP-Konfiguration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration),
[VS-Code-OAuth-Redirects](https://code.visualstudio.com/api/extension-guides/ai/mcp),
[Entra-MCP-Einrichtung](https://learn.microsoft.com/en-us/entra/agent-id/secure-mcp-server-with-entra-id),
[MCP-Autorisierung](https://modelcontextprotocol.io/specification/latest/basic/authorization).
