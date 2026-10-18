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
