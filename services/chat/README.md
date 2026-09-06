# Weave Chat

## Einordnung

Weave Chat ist die **Weboberfläche**, über die Menschen mit den Weave-Bots
sprechen: eine Next.js-App-Router-Anwendung (`src/app`) mit zwei Seiten — der
Chat-Ansicht `/` und `/login`. Sie hält keine eigenen Daten und hat keine
Datenbank. Jede Information, die sie anzeigt, kommt von **Weave-API**, und jedes
Credential, das sie hält, liegt serverseitig in diesem Next.js-Prozess, nie in
Browser-JavaScript.

Die einzige architektonische Regel dieses Dienstes: Er spricht mit genau einem
Backend. Alle serverseitigen Aufrufe gehen an Weave-APIs `/v1/*`-Surface; keiner
davon an Weave-Retrieval, Weave-Runtime oder das Weave-Tools-Backend, weder
direkt noch auf Umwegen. Erzwungen wird die Regel vom Aufbau selbst:
`src/lib/weave-api-server.ts` ist das einzige Modul, das eine URL baut, die
dieser Server aufruft, und es baut jede einzelne davon aus
`WEAVE_API_BASE_URL`.

## Zweck

- Die Chat-Ansicht ausliefern: Bot-Liste, Collection-Filter, gestreamte Antwort,
  Quellen-Karten, Trace-Panel, Guard-Banner, Theme-Umschalter
- Jede Browser-Aktion über die eigenen `/api/*`-Route-Handler dieses Servers
  leiten, damit der Browser nie ein Weave-API-Credential hält und nie einen
  Cross-Origin-Request stellt
- Nutzer anmelden — über **Mit Weave anmelden** (die föderierte Anmeldung über
  Weave-Ingest), über einen alternativen Direkt-OIDC-Knopf oder über ein
  Personal-API-Token
- Weave-APIs HTTP-Status-Codes und Fehler-Ereignisse im Stream in je einen
  verständlichen Satz übersetzen (`src/lib/errors.ts`), nie in einen rohen
  Status-Code, einen `detail`-Text des Upstreams oder einen Stacktrace

## Verantwortlichkeiten

- Session-Handling: den Einmal-Login-Code einlösen, das Credential im eigenen
  httpOnly-Cookie halten, es beim Logout löschen
- Das Credential an Server-zu-Server-Aufrufe anhängen, in genau der Form, in der
  Weave-API es für diese Credential-Art akzeptiert
- Den Chat-SSE-Stream Byte für Byte weiterreichen und ihn clientseitig parsen
- Bot-Antworten als bereinigtes Markdown rendern (`react-markdown` mit
  `remark-gfm` und `rehype-sanitize`)

## Nicht-Ziele / Abgrenzung

- **Kein zweites Backend**: ruft nie Weave-Retrieval, Weave-Runtime oder das
  Weave-Tools-Backend auf (weder REST noch MCP). Weave-API ist der einzige
  Upstream
- **Keine eigene Identität**: keine Nutzer-, Team- oder Token-Verwaltung und kein
  eigener OIDC-Ablauf — den führen Weave-API und Weave-Ingest; diese App löst nur
  den daraus entstehenden Einmal-Code ein
- **Kein Bot-Management**: Bots werden in Weave-Runtime konfiguriert; diese
  Oberfläche listet, was `GET /v1/bots` zurückgibt
- Keine Suche und keine Indexierung: das gehört zu Weave-Retrieval und
  Weave-Knowledge
- Keine Zugriffskontrolle: die Collection-Auswahl in der Seitenleiste ist ein
  *Filter* pro Anfrage, der nur einschränken kann, was der Aufrufer ohnehin
  lesen darf, und diese Menge nie erweitert — die Autorität wird serverseitig
  von Weave-Runtime und Weave-Retrieval aufgelöst (siehe
  `ChatRequestBody.collections` in `src/types/weave-api.ts`)
- Nicht die Schnittstelle für Maschinen: Skripte, n8n und OpenAI-kompatible
  Clients sprechen mit einem Personal-Token direkt mit Weave-API
- Keine Session-Validierung pro Navigation: `src/middleware.ts` prüft nur, dass
  das Session-Cookie *vorhanden* ist, und das nur auf `/`. Ein abgelaufenes,
  aber vorhandenes Cookie erreicht die Seite und wird vom ersten
  authentifizierten Aufruf abgefangen

## Schnittstellen

**Eigene Route Handler (`src/app/api/**`)** — alles, was der Browser aufruft. Die
vier Proxy-Routen erzwingen ihre eigene Session-Prüfung
(`src/lib/require-session.ts`) und antworten mit JSON, nie mit einem Redirect,
wenn das Cookie fehlt; die beiden Session-Routen und der SSO-Callback setzen
bzw. löschen dieses Cookie selbst und haben deshalb keine solche Prüfung:

| Route | Aufruf auf Weave-API | Anmerkung |
|---|---|---|
| `GET /api/bots` | `GET /v1/bots` | Bot-Liste für die Seitenleiste |
| `GET /api/collections` | `GET /v1/collections` | Informativ; die lesbare Menge löst der Upstream aus dem Team des Aufrufers auf |
| `POST /api/chat` | `POST /v1/chat` | Nicht-streamender Fallback, nur genutzt, wenn der gestreamte Turn nicht gestartet werden konnte |
| `POST /api/chat/stream` | `POST /v1/chat/stream` | Reicht die SSE-Bytes unangetastet durch; geparst wird clientseitig in `src/lib/sse.ts` |
| `POST /api/session/login` | `GET /v1/bots` | Prüft ein eingetipptes Personal-Token, bevor irgendein Cookie gesetzt wird |
| `POST /api/session/logout` | `POST /v1/auth/logout` | Gateway-Aufruf nur für ein per SSO erlangtes Credential; Best-Effort und nie blockierend |
| `GET /api/auth/sso/callback` | `POST /v1/auth/session/exchange` | Löst den Einmal-Login-Code serverseitig ein |

Diese Tabelle ist die vollständige Liste der Weave-API-Routen, die dieser Dienst
aufruft. Die beiden Login-Knöpfe schicken zusätzlich den *Browser* auf
Weave-APIs `GET /v1/auth/ingest/login` und `GET /v1/auth/oidc/login`, als
einfache Top-Level-Navigationen — das sind Redirects, denen der Nutzer folgt,
keine Aufrufe, die dieser Server macht.

**Output:** HTML und JSON an den Browser auf der eigenen Herkunft dieser App,
dazu `text/event-stream` für einen gestreamten Turn. `public/robots.txt`
verbietet jedes Crawling.

**Abhängigkeiten:**

- Weave-API — der einzige Upstream, unter `WEAVE_API_BASE_URL`
- Node.js (das Container-Image baut auf `node:26-alpine` auf)

## Entwicklung

### Setup

```bash
cd services/chat
npm install
cp .env.example .env.local   # Next.js' eigene Konvention; Werte anpassen
```

`.env` und jede `.env.*`-Variante außer `.env.example` sind von der
`.gitignore` im Repo-Wurzelverzeichnis erfasst und dürfen nie eingecheckt
werden.

### Einzelbetrieb

```bash
npm run dev     # Entwicklungsserver, standardmäßig Port 3000
npm run build   # Produktions-Build
npm run start   # den Produktions-Build ausliefern
npm run lint    # eslint
```

Der Einzelbetrieb braucht eine erreichbare Weave-API. Ohne
`WEAVE_API_BASE_URL` fällt die App auf `http://localhost:8004` zurück
(`src/lib/weave-api-server.ts:resolveWeaveApiBaseUrl`), den lokalen
Entwicklungs-Port von Weave-API.

### Tests

```bash
npm test        # vitest run
```

Siebzehn Testdateien unter `src/`, die den SSE-Parser, die
Fehler-Mapping-Tabelle, den Chat-Request-Builder, den Aufbau der SSO-URL, den
Weave-API-Client, jeden session-relevanten Route Handler und die
Chat-Komponenten abdecken. Die Suite läuft standardmäßig in Vitests schlichter
`node`-Umgebung — die Route Handler sind gewöhnlicher Web-Fetch-API-Code — und
die sechs Komponententests (`login-form`, `chat-app`, `sidebar`, `source-cards`,
`trace-panel`, `guard-banner`) melden sich über ein pro Datei gesetztes
`// @vitest-environment jsdom`-Pragma selbst für `jsdom` an. `server-only` wird
per Alias auf einen No-Op-Stub umgeleitet (`src/test-stubs/server-only.ts`),
damit ein `server-only`-geschütztes Modul importiert werden kann, ohne dafür
einen Next.js-Build auszuführen.

### Umgebungsvariablen

Alle fünf werden pro Anfrage live in `server-only`-Modulen gelesen
(`src/lib/weave-api-server.ts`, `src/lib/sso.ts`). Es gibt nirgendwo in `src/`
eine `NEXT_PUBLIC_*`-Variable, und `next.config.ts` hat bewusst keinen
`env`-Block — ein dort gelisteter Wert würde zur Build-Zeit fest in das
Client-Bundle eingefroren.

| Variable | Default | Erreicht den Browser |
|---|---|---|
| `WEAVE_API_BASE_URL` | `http://localhost:8004` | Nie. Nur Server-zu-Server-Adresse; darf ein rein interner Host sein |
| `WEAVE_API_PUBLIC_BASE_URL` | fällt auf `WEAVE_API_BASE_URL` zurück | Als Teil des `href` des Login-Links — genau dafür ist sie da |
| `APP_BASE_URL` | `http://localhost:3000` | Als `return_to`-Parameter im `href` des Login-Links |
| `WEAVE_API_INGEST_LOGIN_ENABLED` | nicht gesetzt (aus) | Nur ihre Wirkung: ob der Knopf "Mit Weave anmelden" erscheint |
| `WEAVE_API_OIDC_ENABLED` | nicht gesetzt (aus) | Nur ihre Wirkung: ob der Knopf "Mit SSO anmelden" erscheint |

Die beiden `*_ENABLED`-Flags sind eigene Einstellungen dieser App, nichts, was
von Weave-API gelesen würde. Weave-API hat keine Laufzeit-Abfrage "ist Login X
konfiguriert": ihre `/v1/auth/oidc/*`- und `/v1/auth/ingest/*`-Router antworten
`404`, wenn sie nicht konfiguriert sind, absichtlich ununterscheidbar davon, nie
eingehängt worden zu sein. Wer die beiden zusammen ausrollt, hält die Flags
synchron. Ein Auseinanderlaufen ist in beide Richtungen harmlos — ein Knopf, der
beim Klick `404`t, oder ein funktionierender Login, der verborgen bleibt.

`APP_BASE_URL` wird konfiguriert, statt aus dem `Host`-Header einer Anfrage
abgeleitet zu werden, weil sie exakt (Schema, Host, Port) einem Eintrag in
Weave-APIs eigener `OIDC_POST_LOGIN_ALLOWED_URLS` entsprechen muss — und zwar
diesem Wert plus `/api/auth/sso/callback`.

Eine Anmerkung zu `.env.example`: sie dokumentiert alle fünf Variablen oben und
zusätzlich `NODE_ENV` — Letzteres nur, um festzuhalten, dass es hier nichts
entscheidet. `src/lib/session.ts` bestimmt das `Secure`-Attribut des Cookies
anhand der Verbindung (`X-Forwarded-Proto`, sonst das Schema der Anfrage
selbst), ausdrücklich nicht anhand von `NODE_ENV` — diese App wird als
Produktions-Build ausgeliefert und regelmäßig über einfaches HTTP erreicht, wo
ein `Secure`-Cookie vom Browser verworfen würde: die Anmeldung sähe erfolgreich
aus, während nichts gespeichert wird.

### Anmeldung

Drei Wege hinein, die alle beim selben httpOnly-Cookie enden. Der primäre ist
**Mit Weave anmelden** (`WEAVE_API_INGEST_LOGIN_ENABLED`), die föderierte
Anmeldung über Weave-Ingest, wo Nutzer, Teams und OIDC-Verbindungen ohnehin
schon verwaltet werden:

1. Der Knopf auf `/login` ist eine einfache Top-Level-Navigation zu Weave-APIs
   `GET /v1/auth/ingest/login?return_to=<APP_BASE_URL>/api/auth/sso/callback`
   (`src/lib/sso.ts:buildWeaveLoginUrl`).
2. Weave-API führt die ganze Anmeldung selbst durch — nichts davon berührt diese
   App — und leitet den Browser mit einem frischen, einmal verwendbaren
   `code`-Query-Parameter auf diesen Callback zurück, weil `return_to` zu ihrer
   Allowlist gepasst hat.
3. `GET /api/auth/sso/callback` tauscht diesen Code **serverseitig** gegen ein
   echtes Sitzungs-Token (`POST /v1/auth/session/exchange`), nie aus
   Browser-JavaScript, und leitet auf `/` weiter.
4. Das Token landet in derselben Antwort direkt im eigenen Cookie dieser App.
   Seine Lebensdauer wird aus Weave-APIs eigenem `expires_at` abgeleitet, sodass
   das Cookie nie die dahinterliegende Session überlebt.

Der Direkt-OIDC-Knopf (`WEAVE_API_OIDC_ENABLED`) ist die Alternative für ein
Gateway, das stattdessen auf einen OIDC-Provider zeigt; er nimmt über
`GET /v1/auth/oidc/login` denselben Weg und endet beim selben Callback. Ein
Betreiber konfiguriert das eine oder das andere, in der Praxis erscheint also
höchstens ein Knopf. Jeder Fehlschlag leitet auf `/login?error=...` weiter, was
die Login-Seite in einen Satz übersetzt — `missing_code`, `invalid_code` und
`gateway_unreachable` sind die drei Gründe, und Weave-APIs eigene Weigerung,
"unbekannt", "abgelaufen" und "bereits verbraucht" auseinanderzuhalten, wird
unverändert durchgereicht.

Der dritte Weg ist das Personal-API-Token, das ins Formular eingetippt wird.
`POST /api/session/login` prüft es zuerst gegen `GET /v1/bots` — die billigste
authentifizierte Route — und setzt erst dann das Cookie. Ein Token, das
Weave-API ablehnt, berührt nie ein Cookie.

**Das Cookie** (`src/lib/session.ts`) heißt `weave_api_token`, mit einem
Begleiter `weave_api_token_kind`, der zusammen mit ihm gesetzt und gelöscht
wird, nie unabhängig davon. Beide sind `httpOnly`, `sameSite=lax`, `path=/`,
`Secure`, sofern die Verbindung es zulässt, und haben standardmäßig eine
Lebensdauer von 30 Tagen für ein Personal-Token, das serverseitig kein bekanntes
Ablaufdatum hat. `httpOnly` ist das, was die ganze Konstruktion trägt: der Wert
ist für `document.cookie` und für das eigene Client-Bundle dieser App
unsichtbar. `import 'server-only'` am Kopf von `session.ts` macht aus jedem
künftigen Import aus einer `'use client'`-Datei einen Build-Zeit-Fehler statt
eines stillen Lecks.

**Die Art ist entscheidend**, weil Weave-API die beiden Credentials nur über
ihren jeweils eigenen Transportweg akzeptiert
(`src/lib/weave-api-server.ts:credentialHeaders`):
ein Personal-Token geht als `Authorization: Bearer <token>` hinaus, ein per SSO
erlangtes Sitzungs-Token als `Cookie: weave_api_session=<token>`. Eines von
beiden über den Transportweg des anderen zu schicken, ergäbe ein `401`.

**Logout** (`POST /api/session/logout`) löscht immer das Cookie dieser App. Für
ein per SSO erlangtes Credential ruft er zuerst Weave-APIs
`POST /v1/auth/logout` auf, damit auch die Session auf Gateway-Seite endet;
dieser Aufruf ist Best-Effort und wirft nie einen Fehler, sodass ein nicht
erreichbares Gateway einen Nutzer hier nicht im angemeldeten Zustand gefangen
halten kann.
Ein Personal-Token hat keine serverseitige Session, die enden könnte —
widerrufen wird es in Weave-APIs eigener Token-Verwaltung.

### Docker

```bash
docker build -t weave-chat services/chat
```

Mehrstufiger Build auf `node:26-alpine`: in der Runner-Stage gehören die
kopierten Dateien dem `node`-Nutzer des Images, der Container wechselt auf
diesen Nutzer herunter, statt als root zu laufen, gibt `3000` frei und führt
`npm run start` aus.

### Benennung: der Dienst heißt weiterhin weave-tools-frontend

Die Chat-Oberfläche ist nach `services/chat` umgezogen, die Deployment-Namen
sind aber nicht mitgezogen. Beide Compose-Dateien nennen den Dienst weiterhin
`weave-tools-frontend`; den Build-Kontext `../services/chat` setzt allein das
lokale Override, die Hauptdatei nennt ihn nur in einem Kommentar:

| Wo | Name |
|---|---|
| Compose-Dienst | `weave-tools-frontend` (`deploy/docker-compose.weave.yml`, `deploy/docker-compose.local.yml`) |
| Image | `ghcr.io/bl0rb/weave-tools-frontend:${WEAVE_TOOLS_FRONTEND_TAG:-latest}` |
| Container | `weave_tools_frontend` |
| Build-Kontext | `../services/chat`, Dockerfile im Wurzelverzeichnis des Dienstes |
| Host-Port | `${TOOLS_FRONTEND_PORT:-3001}` → Container-`3000` |

Zwei weitere Überbleibsel desselben Umzugs: dieses Paket heißt in `package.json`
weiterhin `frontend`, und `APP_BASE_URL` des Dienstes wird aus der Variablen
`CHAT_APP_BASE_URL` gespeist (`chat`-Abschnitt in `weave.yaml`), mit Default
`http://localhost:3001` passend zum veröffentlichten Port. Im Stack erreicht
dieser Dienst das Gateway unter der internen Adresse `http://weave-api:8000`,
weshalb es `WEAVE_API_PUBLIC_BASE_URL` als separaten, vom Browser erreichbaren
Wert gibt.

## Status

Die Chat-Oberfläche deckt Weave-APIs vier Lese- und Chat-Routen vollständig ab.
Ein Turn wird standardmäßig gestreamt (`POST /api/chat/stream`); das
nicht-streamende `POST /api/chat` ist ein Fallback, der nur greift, wenn der
gestreamte Turn überhaupt nicht gestartet werden konnte, nie der Standardweg.
Der Client unterscheidet die drei Arten, wie ein Stream enden kann — ein
sauberes `done`, ein `error`-Ereignis im Stream und eine Verbindung, die ohne
beides endete — und behandelt Teiltext aus dem letzten Fall so, wie Weave-API
selbst es tut: nicht als fertige Antwort (`src/lib/run-chat-stream.ts`).

Beide Login-Wege und der Personal-Token-Weg teilen sich ein Cookie und eine
Credential-Abstraktion; ihre Route Handler sind gegen ein gestubbtes `fetch`
getestet, das für Weave-API einspringt, bis hin zu den Cookie-Attributen, die
jeder einzelne davon setzt. Die UI-Texte sind deutsch (`<html lang="de">`,
`src/lib/errors.ts`); der Code ist englisch.
