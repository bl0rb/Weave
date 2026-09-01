# ADR 0006: Weave-Ingest als Identitätsanbieter der Plattform

**Status:** angenommen

**Datum:** 2026-09-01

## Kontext

ADR-0002 legt fest: Menschen melden sich per OIDC am Gateway (Weave-API) an.
Umgesetzt wurde das dort als **ein** statisch konfigurierter Provider aus zwei
Umgebungsvariablen, mit einer eigenen `users`-Tabelle in `weave_api` und
Personal-Tokens, die nur ein CLI im Container ausstellen kann.

Weave-Ingest hat unabhängig davon eine vollständige Benutzerverwaltung
entwickelt, weil es sie für sich selbst brauchte: lokale Benutzer mit Passwort,
Teams, eine **Tabelle** von OIDC-Verbindungen mit Testknopf und verschlüsseltem
Client-Secret, dazu eine Oberfläche für all das und Selbstbedienung für
persönliche Tokens.

Damit gab es zwei Kontenwelten für dieselben Menschen. Praktisch heißt das:

- Ein Administrator richtet OIDC zweimal ein, an zwei verschiedenen Orten und in
  zwei verschiedenen Formen (Datenbank hier, Umgebungsvariablen dort).
- Ein neuer Mitarbeiter kommt ohne Zutun eines Betreibers nicht in den Chat:
  sein Konto entsteht in Weave-Ingest, sein Zugang zum Chat aber nicht.
- Team-Zugehörigkeit — der Wert, an dem die Leserechte auf Collections hängen —
  wird an zwei Stellen gepflegt und driftet auseinander.

Der Chat sollte für Nutzer einfach sein. Zwei Konten sind das nicht.

## Entscheidung

**Weave-API bekommt keine eigene Identität mehr, sondern föderiert an
Weave-Ingest.** Der Chat schickt zum Anmelden auf dessen Anmeldeseite; wie sich
jemand dort ausweist, ist für den Chat unsichtbar und unerheblich.

Die Kette hat drei Sprünge und zwei Einmal-Codes:

1. `GET /v1/auth/ingest/login` (Weave-API) prüft `return_to` gegen die
   Allowlist, legt `state` und Ziel in ein signiertes Cookie und schickt den
   Browser zu `INGEST_LOGIN_URL`.
2. Weave-Ingest meldet die Person an — Passwort oder irgendeine konfigurierte
   OIDC-Verbindung — und leitet mit einem **Einmal-Code** an genau die eine
   Adresse zurück, die in `HANDOFF_CALLBACK_URL` steht.
3. Weave-API löst den Code **server-zu-server** gegen ein geteiltes Secret ein
   (`POST /api/v1/auth/handoff/exchange`), legt lokal einen Spiegel des Kontos
   an und mündet in den bereits bestehenden Pfad: ein eigener Einmal-Code für
   die Chat-Oberfläche, den deren Backend gegen eine Sitzung tauscht.

Festlegungen dazu:

- **Verknüpft wird über die Ingest-Benutzer-ID**, nie über Benutzername oder
  E-Mail. Beides kann ein Administrator ändern, ohne das Konto weitergeben zu
  wollen. Gespeichert wird sie mit Präfix (`weave-ingest:<id>`), damit sie mit
  dem Subject eines direkt konfigurierten OIDC-Providers nicht kollidieren kann.
- **Team und Admin-Bit werden bei jeder Anmeldung neu gelesen.** Sie gehören
  Weave-Ingest; eine Änderung dort muss beim nächsten Anmelden wirken, ohne dass
  jemand eine zweite Datenbank anfasst. Der Benutzername folgt bewusst *nicht*
  nach: er ist in `weave_api` eindeutig und beim Anlegen womöglich mit Suffix
  versehen worden.
- **Das Rücksprungziel ist auf der Ingest-Seite fest konfiguriert**, kein
  Parameter. Dort entsteht dadurch überhaupt keine Open-Redirect-Fläche; die
  variable Zieladresse (welche Chat-Instanz) bleibt allein Weave-APIs Problem,
  wo die geprüfte Allowlist bereits existiert.
- **Der Einmal-Code ist einmalig und kurzlebig** und für sich genommen wertlos:
  ohne das geteilte Secret lässt er sich nicht einlösen. Er reist durch eine
  URL — die Stelle, an der ein Geheimnis am ehesten in einem Zugriffsprotokoll
  landet —, und genau deshalb ist er so gebaut.
- **Fehlt das Secret, antwortet der Tausch mit 503**, nie unauthentifiziert:
  ein leeres Secret verglichen mit einem leeren Header wäre ein offenes
  Identitäts-Orakel.

Der bestehende OIDC-Pfad in Weave-API bleibt als Alternative erhalten. Ein
Betreiber konfiguriert das eine **oder** das andere.

## Konsequenzen

**Positiv**

- Benutzer, Teams und OIDC-Verbindungen werden an genau einer Stelle gepflegt,
  mit Oberfläche.
- Jede Anmeldeart, die Weave-Ingest kennt oder künftig lernt, gilt sofort auch
  für den Chat. Weder Gateway noch Chat erfahren davon.
- Team-Zugehörigkeit — und damit der Zugriff auf Collections — kann nicht mehr
  zwischen zwei Diensten auseinanderlaufen.
- Weave-API sieht nie ein Passwort und nie ein Provider-Token.

**Negativ**

- Weave-Ingest wird für die Anmeldung am Chat zur Voraussetzung. Fällt es aus,
  kann sich niemand neu anmelden (bestehende Sitzungen laufen weiter). Der
  Personal-Token-Pfad bleibt als Rückfallebene.
- Vier Einstellungen über drei Dienste müssen zusammenpassen; jede halbe
  Konfiguration versagt leise. Deshalb prüft `scripts/weave_config.py check`
  die Kette geschlossen (Abschnitt 6 dort).
- Ein zusätzlicher Netzwerksprung pro Anmeldung.

## Alternativen

- **Gemeinsame Datenbank.** Weave-API liest Ingests `users`-Tabelle direkt.
  Verworfen: bricht ADR-0004 ohne die Not, die ADR-0005 rechtfertigt (dort ging
  es um SQL, das in der Datenbank laufen *muss*), und koppelt Passwort-Hashing
  und Migrationen zweier Dienste aneinander.
- **Externer Identitätsanbieter vor beiden.** Sauber, aber verlangt einen
  zusätzlichen Dienst (Keycloak, Authentik) für einen Fall, den die Plattform
  bereits vollständig abdeckt. Bleibt möglich: Weave-Ingest ist dann dessen
  Client, und die Föderation hier ändert sich nicht.
- **Weave-Ingest zum echten OIDC-Provider ausbauen.** Der allgemeinste Weg und
  deutlich mehr Aufwand (Discovery, JWKS, Token-Endpunkt, Schlüsselrotation) für
  genau einen bekannten Konsumenten.
