# ADR 0002: Authentifizierungs- und Autorisierungsstrategie

**Status:** angenommen

**Datum:** 2026-08-31

## Kontext

Die Weave-Plattform muss mehrere Szenarien authentifizieren:

1. Menschen nutzen die Plattform über Web/CLI
2. Externe Maschinen integrieren sich via API
3. Services kommunizieren untereinander (Service-to-Service)

Zentrale Authentifizierung reduziert Komplexität; dezentralisierte Autorisierung verhindert Single-Points-of-Failure. Die Lösung muss skalierbar und mit bestehenden Enterprise-Systemen integrierbar sein.

## Entscheidung

### Für Menschen: OIDC am Gateway

- **Provider:** Keycloak oder Entra (konfigurierbar)
- **Ort:** Weave-API (zentrales Gateway)
- **Flow:** Standard Authorization Code mit PKCE (Browser) / Client Credentials (CLI via Device Grant)
- **Token:** JWT mit `sub` (User-ID), `groups` (Team-Zugehörigkeit), `scope`

### Für Maschinen: Personal-API-Token

- **Pattern:** Bearer-Token (wie in PaddleDoc)
- **Format:** `weave_pat_<random_48chars>_<signature>`
- **Optionales Ablaufdatum:** Default unbegrenzt, aber `expires_at` setzbar
- **Authentifizierung:** Secret-Lookup in Weave-API
- **Verwendung:** HTTP Header `Authorization: Bearer weave_pat_...`

### Für Service-zu-Service: Kurzlebige interne Tokens

- **Typ:** Signierte JWT oder HMAC-basierte Tokens
- **Lebensdauer:** 5-15 Minuten
- **Signatur:** Mit Shared-Secret (z.B. `INTERNAL_API_SECRET`) oder asymmetrischen Schlüsseln
- **Transport:** HTTP-Header oder Request-Body
- **Voraussetzung:** Netzwerk-Isolation im Docker-Compose / Kubernetes

### Autorisierung überall

- **Am Gateway (Weave-API):**
  - `authorized_teams` überprüfen für jeden Request
  - Sichtbarkeits-Marker setzen (z.B. `X-User-Teams` Header)

- **In Services:**
  - Metadaten-Filter beim Datenzugriff (z.B. `WHERE team_id IN (...)`)
  - Kein Duplikation von Gateway-Logik; Vertrauen auf propagierte Metadaten

- **Nicht bei:** Einzelnen Services nicht replizieren; zentrale Quelle ist Authorität

## Konsequenzen

**Positiv:**
- Zentrale, konsistente Authentifizierung
- Standards-basiert (OIDC, JWT)
- Services können mit einfacher Metadaten-Filterung arbeiten
- Migrierbar zu anderen IdP-Providern

**Negativ:**
- Weave-API ist kritische Komponente; Ausfall blockiert Gateway-Access
- PAT-Management erfordert Audit-Logging

## Alternativen

1. **Verteilte Auth pro Service:**
   - Komplexer zu verwalten, höheres Konsistenz-Risiko

2. **Nur API-Keys ohne OIDC:**
   - Für Menschen umständlich (kein SSO)

3. **Service-Accounts ohne Isolation:**
   - Sicherheitsrisiko; Services könnten sich gegenseitig spoofing
