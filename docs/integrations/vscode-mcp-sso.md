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

Die öffentliche HTTPS-URL liefert bei Helm der Ingress `ingress.toolsMcp`
(standardmäßig aus, `hosts` und `tls` wie bei den anderen Ingresses setzen).
Er zeigt auf den Service `tools-mcp` und reicht mit der Vorgabe `path: /`
neben `/mcp` auch die beiden Discovery-Pfade
`/.well-known/oauth-protected-resource` und
`/.well-known/oauth-protected-resource/mcp` durch; wer die Pfade
einschränkt, muss diese drei behalten. Teilt sich der MCP-Dienst den Host
mit dem Portal, gehört `/` dort bereits dem Portal; dann unter
`ingress.toolsMcp.hosts[].paths` nur `/mcp` und
`/.well-known/oauth-protected-resource` (beide `Prefix`) eintragen. Liefert
die MCP-URL eine HTML-404-Seite des Portals, fehlt genau diese Weiterleitung.
Vorher `MCP_OAUTH_ISSUER` und
`MCP_OAUTH_RESOURCE_URL` setzen und `MCP_ALLOWED_HOSTS` um den öffentlichen
Hostnamen ergänzen, sonst antwortet der Dienst mit 421.

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

### Entra Schritt für Schritt

API-App (Weave-MCP, nicht die App-Registrierung des Portal-Logins):

1. „Expose an API“: Application ID URI `api://<api-client-id>` – ohne
   Scope-Namen, sonst entsteht `…/mcp.read/mcp.read`.
2. Scope `mcp.read` hinzufügen, Zustand „Enabled“. Bei „Admins only“ muss
   ein Admin der Client-App die Berechtigung erteilen.
3. Manifest: `requestedAccessTokenVersion` auf `2` setzen (im neuen Editor
   unter `api`). Sonst stellt Entra v1-Tokens mit Issuer
   `https://sts.windows.net/<tenant-id>/` aus, und der Issuer passt nicht.

Öffentliche Client-App (VS Code bzw. Claude Code):

1. „Authentication“: Plattform „Mobile and desktop applications“ mit der
   Redirect-URI des Clients, für Claude Code
   `http://localhost:<callback-port>/callback`; „Allow public client flows“
   einschalten, kein Client-Secret.
2. „API permissions“: delegierte Berechtigung `mcp.read` der API-App
   hinzufügen und „Grant admin consent“ ausführen – oder die Client-ID in
   der API-App unter „Authorized client applications“ eintragen.

Daraus ergeben sich die Werte: `MCP_OAUTH_AUDIENCE=<api-client-id>`,
`MCP_OAUTH_SCOPES=["api://<api-client-id>/mcp.read"]`,
`MCP_OAUTH_REQUIRED_SCOPES=["mcp.read"]`, `MCP_OAUTH_SUBJECT_CLAIM=oid`.

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

## Claude Code konfigurieren

Claude Code spricht denselben Endpunkt per OAuth mit PKCE an. Ohne Dynamic
Client Registration (Entra) braucht es die Client-ID der öffentlichen
Client-App und einen festen Callback-Port, dessen Redirect-URI
`http://localhost:<port>/callback` beim Provider registriert ist:

```bash
claude mcp add --transport http --scope project \
  --client-id <public-client-id> --callback-port 8976 \
  weave https://weave.example.org/mcp
```

- URL immer mit `/mcp`; die Portal-Wurzel ist kein MCP-Endpunkt.
- Kein `Authorization`-Header eintragen, Claude Code holt und erneuert das
  Token selbst.
- Die anzufordernden Scopes nennt der Server in der 401-Antwort
  (`MCP_OAUTH_SCOPES`). Lässt der Client sie explizit setzen, für Entra
  `api://<api-client-id>/mcp.read` angeben, für Keycloak `mcp.read`.
- In Claude Code `/mcp` öffnen, `weave` → Authenticate, Browser-Login
  abschließen.

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
