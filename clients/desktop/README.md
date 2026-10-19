# Weave für den Desktop

Tray-App für macOS (Menüleiste) und Windows (Infobereich), zwei Ansichten:

- **Schmale Leiste** – Klick auf das Weave-Symbol oder `Cmd/Ctrl+Shift+Space`: eine schwebende
  Eingabeleiste wie bei Claude, in der Bildschirmmitte und auch über Vollbild-Apps; die Antwort
  klappt darunter auf.
- **App-Fenster** – beim Start der App (Dock, Launchpad, Startmenü) oder über „Weave öffnen“ im
  Tray-Menü: alles mit Agent-Auswahl, Fragen/Suchen und Wissensraum-Filter. Solange es offen
  ist, steht Weave im Dock; Schließen lässt Weave im Tray weiterlaufen. Beim Autostart nach der
  Anmeldung am Rechner startet Weave still im Tray.

Agent wählen, fragen – oder in allen Wissensräumen suchen, die man lesen darf. Antworten kommen
gestreamt mit Quellen; Quellen mit Link (z. B. Confluence) öffnen sich im Browser.

- **Agentenliste** – wird bei jedem Öffnen (Symbol, Kürzel, „Weave öffnen“) neu geladen; ein
  neu angelegter oder freigegebener Bot erscheint ohne Neustart.
- **Verlauf** – bleibt stehen, bis man ihn mit „Verlauf leeren“ (Papierkorb, `Cmd/Ctrl+N`)
  entfernt. Nach 30 Minuten ohne Nutzung öffnet Weave mit einer leeren Unterhaltung.
- **Im Web öffnen** – öffnet die laufende Unterhaltung im Web-Chat, sonst dessen Verlauf mit
  allen früheren Fragen (sie bleiben dort gespeichert). Die Adresse liefert weave-api
  (`api.CHAT_PUBLIC_URL`); ist sie leer, fehlt der Knopf.
- **Scrollen** – eine neue Frage rückt nach oben, die Antwort läuft darunter ein, ohne die
  Ansicht bis an ihr Ende mitzuziehen (wie im Web-Chat).

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
Für „Im Web öffnen“ zusätzlich `api.CHAT_PUBLIC_URL` = Adresse des Web-Chats (derselbe
Wert wie `chat.CHAT_APP_BASE_URL`).
Ohne PKCE nimmt weave-api keine Loopback-Adresse an, und der Code ist nur mit
dem passenden Verifier einlösbar.

## Einrichtung per Link aus Weave-Chat

Die App registriert das URL-Schema `weave://` (`tauri-plugin-deep-link`, Schema in
`tauri.conf.json`). In Weave-Chat öffnet „Desktop-App einrichten“ (Fußzeile der
Gesprächsleiste) den Link `weave://setup?server=<API-Adresse>`; die Adresse ist
`WEAVE_API_PUBLIC_BASE_URL` von Weave-Chat, also dieselbe, die auch die Anmeldung
im Browser ansteuert. Daneben steht ein Download-Link, falls die App noch fehlt.

Die App übernimmt nichts automatisch: Sie zeigt die Adresse im Einstellungsfenster
als Vorschlag („Einrichtung aus Weave-Chat“), erst „Übernehmen“ speichert sie, testet
die Verbindung und bietet „Anmelden“ an. Der Link trägt nur die Server-Adresse,
nie Zugangsdaten; andere Parameter oder eine `http://`-Adresse (außer localhost)
werden verworfen (`src-tauri/src/deeplink.rs`). Ist `ServerUrl` von der IT vorgegeben,
bleibt sie unverändert, der Vorschlag wird nur angezeigt. Jede Webseite kann einen
`weave://`-Link öffnen – deshalb die Nachfrage.

macOS registriert das Schema über das App-Bundle, Windows der Installer; ein Build aus
`tauri dev` registriert es unter Windows/Linux beim Start selbst, unter macOS
funktioniert der Link nur mit der gebauten `.app`.

## Was die App nutzt

| Endpunkt | Zweck |
|---|---|
| `GET /v1/bots` | Agenten, die man nutzen darf (bei jedem Öffnen neu) |
| `GET /v1/collections` | Wissensräume für den Filter |
| `POST /v1/chat/stream` | Frage stellen (Unterhaltung bleibt pro Agent erhalten) |
| `POST /v1/search` | Suche ohne Bot-Antwort |
| `GET /v1/me`, `POST /v1/auth/logout` | Konto; `chat_url` aus `/v1/me` für „Im Web öffnen“ |

## Vorgaben durch die IT (Intune/MDM)

| Name | Wirkung |
|---|---|
| `ServerUrl` | Adresse von weave-api, nicht änderbar |
| `Hotkey` | Tastenkürzel, nicht änderbar (leer = aus) |
| `UpdateCheck` | `false` schaltet die Update-Prüfung ab (wenn die IT Updates selbst verteilt) |
| `UpdateUrl` | eigener Update-Feed statt GitHub (Format `latest.json` von Tauri) |

macOS: Konfigurationsprofil für die Domain `io.github.bl0rb.weave.desktop`.
Windows: Werte unter `HKLM\SOFTWARE\Policies\Weave\Desktop` (oder HKCU); `UpdateCheck`
als DWORD (0/1) oder Zeichenfolge.

## Updates

Das Tray-Menü zeigt die Version und „Nach Updates suchen …“; ist ein Update da, wird
daraus „Update auf X installieren …“. Dasselbe steht unter *Einstellungen › Über Weave*,
und das Fragefenster zeigt einen Hinweis. Die App prüft 20 Sekunden nach dem Start und
dann alle 12 Stunden.

Feed ist `latest.json` am Release `desktop-latest`; jedes Desktop-Release (ohne
Vorabversionen) schreibt ihn neu. Pakete sind mit dem Schlüssel signiert, dessen
öffentlicher Teil in `src-tauri/tauri.conf.json` steht – ein Download, der nicht passt,
wird nicht installiert. macOS ersetzt die App, Windows installiert das MSI (passiv).
Danach startet Weave neu.

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
| `TAURI_SIGNING_PRIVATE_KEY` | Update-Signaturschlüssel (Inhalt der Datei aus `tauri signer generate`); ohne ihn kein Update-Feed |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Passwort dazu (leer, falls ohne Passwort erzeugt) |
