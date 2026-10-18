# Weave für den Desktop

Tray-App für macOS (Menüleiste) und Windows (Infobereich): Klick auf das
Weave-Symbol oder `Cmd/Ctrl+Shift+Space`, Agent wählen, fragen – oder in allen
Wissensräumen suchen, die man lesen darf. Antworten kommen gestreamt mit
Quellen; Quellen mit Link (z. B. Confluence) öffnen sich im Browser.

Gebaut mit Tauri 2 wie [KeyZapper](https://github.com/bl0rb/ClaudeKeyZapper);
die Einstellungen sehen auch so aus. Keine Build-Kette für die Oberfläche
(`ui/` ist reines HTML/JS).

## Anmeldung (SSO)

Die App meldet sich wie eine native App nach RFC 8252 an:

1. Sie öffnet im Browser die normale Weave-Anmeldung („Sign in with Weave“,
   sonst direktes OIDC) mit `return_to=http://127.0.0.1:<freier Port>/callback`
   und einer PKCE-`code_challenge`.
2. Nach dem SSO leitet weave-api mit einem Einmal-Code auf diesen Port zurück.
3. Die App tauscht Code + `code_verifier` gegen ein Sitzungs-Token
   (`POST /v1/auth/session/exchange`) und legt es im Schlüsselbund bzw. in der
   Windows-Anmeldeinformationsverwaltung ab.

Voraussetzung auf dem Server: `api.NATIVE_LOGIN_ENABLED: true` in `weave.yaml`.
Ohne PKCE nimmt weave-api keine Loopback-Adresse an, und der Code ist nur mit
dem passenden Verifier einlösbar.

## Was die App nutzt

| Endpunkt | Zweck |
|---|---|
| `GET /v1/bots` | Agenten, die man nutzen darf |
| `GET /v1/collections` | Wissensräume für den Filter |
| `POST /v1/chat/stream` | Frage stellen (Unterhaltung bleibt pro Agent erhalten) |
| `POST /v1/search` | Suche ohne Bot-Antwort |
| `GET /v1/me`, `POST /v1/auth/logout` | Konto |

## Vorgaben durch die IT (Intune/MDM)

| Name | Wirkung |
|---|---|
| `ServerUrl` | Adresse von weave-api, nicht änderbar |
| `Hotkey` | Tastenkürzel, nicht änderbar (leer = aus) |

macOS: Konfigurationsprofil für die Domain `io.github.bl0rb.weave.desktop`.
Windows: Zeichenfolgen unter `HKLM\SOFTWARE\Policies\Weave\Desktop` (oder HKCU).

## Entwickeln

```bash
npm install
npm run dev
```

Tests der Rust-Seite: `cargo test` in `src-tauri/`. Paket bauen: `npm run build`
(macOS `.app`/`.dmg`, Windows `.msi`/`.exe`; Windows braucht die WebView2-Runtime).

Auf macOS 27 bricht der Release-Build derzeit mit `mis-aligned LINKEDIT` in
Proc-Macro-Bibliotheken ab (das System-`strip` beschädigt sie). Lokal hilft
`CARGO_PROFILE_RELEASE_STRIP=none npm run build`; die CI läuft auf macOS 15.

## Builds und Releases

`.github/workflows/desktop.yml` testet und baut bei jedem Pull Request, der
`clients/desktop/` berührt, beide Installer: `Weave-<version>.dmg` (macOS,
universal) und `Weave-<version>.msi` (Windows). Sie liegen als Artefakte am Lauf.

Ein Tag `desktop-vX.Y.Z` veröffentlicht beide als GitHub-Release
(`desktop-vX.Y.Z-rc.1` als Vorabversion). Das Präfix hält diese Tags aus
`release.yml` heraus, das bei `v*` den Server-Stack veröffentlicht.

```bash
git tag desktop-v0.1.0
```

```bash
git push origin desktop-v0.1.0
```

Signieren ist optional und schaltet sich mit den Repository-Secrets ein. Ohne
sie ist die macOS-App nur ad hoc signiert (Gatekeeper warnt beim ersten Start)
und das MSI unsigniert (SmartScreen warnt) – gut zum Testen, nicht für den
Rollout.

| Secret | Inhalt |
|---|---|
| `APPLE_CERTIFICATE` | „Developer ID Application“-Zertifikat als `.p12`, base64 |
| `APPLE_CERTIFICATE_PASSWORD` | Passwort der `.p12` |
| `APPLE_SIGNING_IDENTITY` | z. B. `Developer ID Application: Firma (TEAMID)` |
| `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | Notarisierung: Apple-ID, app-spezifisches Passwort, Team-ID |
| `WINDOWS_CERTIFICATE` | Code-Signing-Zertifikat als `.pfx`, base64 |
| `WINDOWS_CERTIFICATE_PASSWORD` | Passwort der `.pfx` |
