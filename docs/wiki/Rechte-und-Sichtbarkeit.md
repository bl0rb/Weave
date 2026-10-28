# Rechte und Sichtbarkeit

Wer sieht was in Weave — und warum man sich darauf verlassen kann, dass die
Grenze hält. Diese Seite ordnet ein; die verbindlichen Details stehen im
Repository und sind hier verlinkt, nicht abgeschrieben.

Die kurze Fassung in drei Sätzen: Es gibt eine Anmeldung für Portal, Chat,
Desktop-App und MCP. Der Zugang zu einem Wissensbereich hängt an der Gruppe.
Und die Rechtegrenze steckt in der Datenbankabfrage selbst — nicht in einem
Filter, der hinterher aufräumt.

Ein Wort zu den Begriffen: Das Portal nennt die Gruppen seit v0.7.3 „Gruppen“;
in den API-Feldern, Verträgen und im Betriebshandbuch heißen sie weiterhin
`team` beziehungsweise Teams. Gemeint ist dasselbe.

---

## Eine Anmeldung für Portal und Chat

Weave-Ingest ist der Identitätsanbieter der Plattform. Dort — und nur dort —
werden Benutzer, Gruppen und Anmeldeverfahren gepflegt, unter
**Administration → Konten & Zugriffe**. Der Chat führt keine zweite
Kontenwelt: Wer sich am Portal anmelden kann, kann sich am Chat anmelden, mit
derselben Identität. Dasselbe gilt für die Desktop-App: sie meldet sich über
denselben Weg an, hält das Sitzungstoken im Schlüsselbund des
Betriebssystems und bekommt nie mehr zu sehen als dieselbe Person im Browser.

Für den Menschen sieht das so aus: Der Chat schickt zur Anmeldeseite von
Ingest. Dort meldet man sich an — mit Passwort oder über einen der dort
konfigurierten Unternehmens-Anbieter (OIDC/SSO). Ist dort schon eine Sitzung
offen, geht es ohne weiteren Klick zurück. Der Chat selbst erfährt nie, womit
sich jemand ausgewiesen hat, und sieht weder ein Passwort noch ein
Provider-Token.

Die Begründung, warum das so gebaut ist — und was die Alternativen gewesen
wären — steht in
[ADR-0006](https://github.com/bl0rb/Weave/blob/main/docs/adr/0006-foederierte-anmeldung.md).
Die Einrichtung beschreibt
[docs/betrieb.md, Abschnitt 8](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#8-anmeldung-einrichten);
dort steht auch, welche Werte über die Dienste hinweg zusammenpassen müssen.

Diese Seite beschreibt durchgehend den empfohlenen Weg. Es gibt daneben einen
zweiten, den ein Betreiber stattdessen wählen kann: OIDC direkt am Gateway,
mit einem statisch konfigurierten Provider und den Gruppen aus einem Claim
([betrieb.md, Abschnitt 8.2](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#82-alternative-oidc-direkt-am-gateway)).
Konfiguriert wird das eine oder das andere, nie beides zugleich. Alles, was
weiter unten über Gruppen, Wissensbereiche und die Rechtegrenze steht, gilt für
beide Wege gleichermaßen — sie unterscheiden sich nur darin, woher die
Identität und die Gruppenzugehörigkeit stammen.

### Was regelmäßig missverstanden wird

**Gruppen und Admin-Rechte werden bei jeder Anmeldung neu gelesen.** Sie gehören
Ingest. Eine Änderung dort wirkt beim nächsten Anmelden, ohne dass irgendwo
sonst etwas nachgezogen werden muss (ADR-0006).

**Der Anzeigename folgt einer Umbenennung nicht.** Verknüpft wird über die
interne Ingest-Benutzer-ID, nie über Benutzername oder E-Mail-Adresse — beides
kann ein Administrator ändern, ohne damit ein Konto weitergeben zu wollen. Wer
im Chat plötzlich einen Namen mit angehängter Zahl sieht: Das ist ein
Namenskonflikt mit einem bereits bestehenden Konto, siehe betrieb.md
Abschnitt 8.

**Abmelden gilt nur lokal.** Wer sich aus dem Chat abmeldet, ist bei Ingest
weiterhin angemeldet; der nächste Klick auf den Anmeldeknopf führt ohne
Rückfrage wieder hinein. Das ist bei jeder gemeinsamen Anmeldung so. Wirklich
abmelden heißt, sich auch bei Ingest abzumelden.

**Ingest ist für die Anmeldung eine Voraussetzung.** Fällt es aus, kann sich
niemand neu anmelden — bestehende Sitzungen laufen weiter, und der
Personal-Token-Pfad bleibt als Rückfallebene (ADR-0006, Konsequenzen).

---

## Gruppen sind der Zugang zu Wissensbereichen

Ein Wissensbereich (technisch: eine Collection) trägt eine Liste berechtigter
Gruppen. Wer in einer dieser Gruppen ist, darf die Inhalte des Bereichs über
die Suche und den Chat lesen. Wer nicht, nicht. Eine Person kann mehreren
Gruppen angehören; die primäre Gruppe entscheidet zusätzlich darüber, wem die
eigenen Aufträge und Bereiche im Portal sichtbar sind
([betrieb.md, Abschnitt 3.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#31-mehrere-teams-und-rechteentzug)).

Zwei Eigenschaften dieser Liste sollte man kennen, weil sie sich nicht von
selbst erschließen:

- **Eine leere Liste bedeutet „für alle lesbar", nicht „für niemanden".** Das
  ist die Einstellung „Alle angemeldeten Gruppen" im Portal, und der Wechsel
  dorthin verlangt deshalb eine ausdrückliche Bestätigung. Nachzulesen im
  [Chunk-Store-Vertrag](https://github.com/bl0rb/Weave/blob/main/contracts/chunk-store.md#tabelle-collections)
  (Feld `read_teams`) und in
  [docs/wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#wissensbereiche-administrieren).
- **Der Gruppenname muss zeichengleich übereinstimmen.** Verglichen wird der
  Name, nicht eine ID. Ein Tippfehler oder eine abweichende Schreibweise
  führt nicht zu einer Fehlermeldung, sondern dazu, dass sich jemand
  erfolgreich anmeldet und nichts findet (betrieb.md, Abschnitt 8).

Wer keiner Gruppe zugeordnet ist, hat außer den offenen Bereichen keine
lesbaren Wissensbereiche. Im Trace einer Chat-Antwort erscheint das als
`guard.reason: no_collections` — siehe
[docs/betrieb.md, Abschnitt 10](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest).

**Rechteentzug wirkt sofort.** Wird eine Gruppe aus einem Wissensbereich
entfernt, löst das einen internen, signierten Hinweis aus, der die verkleinerte
Liste in der Regel binnen Millisekunden weiterreicht. Schlägt das fehl, greift
ein periodischer Abgleich als Netz; das verbleibende Zeitfenster ist dann durch
dessen Intervall begrenzt. Der genaue Ablauf steht im
[Chunk-Store-Vertrag, Abschnitt „Sync-Richtung"](https://github.com/bl0rb/Weave/blob/main/contracts/chunk-store.md#sync-richtung).

Wie Wissensbereiche angelegt, befüllt und freigegeben werden, steht auf
[[Wissensbereiche-und-Freigaben]].

**Bots haben eigene Freigaben.** Seit ADR-0008 gehört ein Bot einer Person
und ist an Personen und Gruppen freigegeben oder für alle angemeldeten Nutzer
offen; wer eigene Bots besitzt, sieht im Portal den Eintrag **Meine Bots**.
Diese Freigabe entscheidet nur, wer den Bot *benutzen* darf. Was er findet,
bleibt die Schnittmenge aus seinen Wissensbereichen und den Leserechten des
Fragenden — eine Bot-Freigabe öffnet keinen Wissensbereich
([ADR-0008](https://github.com/bl0rb/Weave/blob/main/docs/adr/0008-besitz-freigaben-metadaten.md)).

---

## Die Rechtegrenze steckt in der Abfrage

Das ist der Punkt, auf den es ankommt.

Man könnte eine Suche naheliegend so bauen: erst suchen, dann aus dem Ergebnis
entfernen, was der Fragende nicht sehen darf. Weave macht das ausdrücklich
nicht. Die Einschränkung auf die erlaubten Gruppen und Wissensbereiche wird
als Bedingung an die Datenbankabfrage selbst gehängt — **bevor** irgendetwas
sortiert oder auf die besten Treffer gekürzt wird.

Der Unterschied ist doppelt:

- **Sicherheit.** Was außerhalb des erlaubten Umfangs liegt, ist nie Teil des
  Ergebnisses, das die Suche überhaupt bildet. Es gibt keinen Zwischenstand, in
  dem fremde Inhalte im Speicher liegen und darauf warten, aussortiert zu
  werden.
- **Qualität.** Ein nachträglicher Filter würde die Plätze in der
  Trefferliste an Dokumente vergeben, die der Fragende gar nicht sehen darf —
  und diese Plätze wären dann für die passenden, erlaubten Treffer weg. Die
  Antwort würde also nicht nur unsicherer, sondern auch schlechter.

Der zweite Teil derselben Regel: **Wer außerhalb steht, bekommt keine Treffer,
nicht etwa versteckte.** Kein Fehler, kein „Zugriff verweigert", kein Hinweis
darauf, dass es den Bereich überhaupt gibt. Wer nach einem Wissensbereich
fragt, für den er nicht berechtigt ist, bekommt ein leeres Ergebnis — genauso,
als gäbe es dort nichts. Diese Haltung ist als „Grundregel Rechte" im
[n8n-Flow-Vertrag](https://github.com/bl0rb/Weave/blob/main/contracts/n8n-flow.md#grundregel-rechte-gilt-fuer-jede-stufe-dieser-kette)
ausformuliert und gilt ausdrücklich für jede Stufe der Kette.

### Warum man sich darauf verlassen kann

- **Es gibt genau eine Stelle, die entscheidet.** Weave-Retrieval ist die
  Lese-Autorität für „welche Wissensbereiche darf Gruppe X lesen"
  ([collections.py](https://github.com/bl0rb/Weave/blob/main/services/retrieval/backend/app/services/collections.py)).
  Die Durchsetzung in der Suche selbst ist davon unabhängig noch einmal als
  harte Grenze gebaut
  ([search.py](https://github.com/bl0rb/Weave/blob/main/services/retrieval/backend/app/services/search.py),
  `apply_filters`) — zwei Riegel, nicht einer.
- **Ein Filter kann nur einschränken, nie erweitern.** Grenzt jemand ein
  Gespräch auf bestimmte Wissensbereiche ein, wird diese Auswahl mit dem
  ohnehin Erlaubten geschnitten. Ein Bereich, der nicht schon erlaubt war,
  kommt dadurch nicht hinzu. Genauso wenig kann eine Bot-Konfiguration mehr
  freigeben als die Gruppen des Fragenden hergeben.
- **Das Wissensgedächtnis unterliegt derselben Grenze.** Die
  Zusammenfassungen und Bereichsübersichten, aus denen ein Bot seit v0.7.9
  „Was weißt du?“ beantwortet, liest Weave-Retrieval mit demselben
  Rechteprädikat wie die Suche und verweigert jede Anfrage ohne konkrete
  Liste erlaubter Bereiche
  ([ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md)).
- **Nur freigegebene Dokumente sind überhaupt suchbar.** Die Abfrage
  beschränkt sich zusätzlich immer auf indizierte Dokumente; blockierte,
  ersetzte, wartende und fehlgeschlagene tauchen unabhängig von allen
  Rechten nie in einem Suchergebnis auf.
- **Die Suche kann die Rechtedaten technisch nicht verändern.** Weave-Retrieval
  verbindet sich mit einer eigenen, rein lesenden Datenbankrolle ohne jedes
  Schreibrecht. Das ist keine Konvention, sondern auf Datenbankebene
  durchgesetzt — siehe
  [ADR-0005, Abschnitt „DB-Rolle"](https://github.com/bl0rb/Weave/blob/main/docs/adr/0005-geteiltes-read-model.md#db-rolle).
- **Die vollständige Rechtematrix bleibt intern.** Die Abfrage, die für jeden
  Bereich alle berechtigten Gruppen auf einmal zurückgibt, ist Administratoren
  vorbehalten — wer sie lesen darf, kennt die systemweite Zugriffskarte, und
  das allein wäre schon eine Preisgabe. Sie verlässt auch die interne
  Dienstgrenze nicht: der Hinweis, der einen Abgleich auslöst, ist kein
  wählbares Ereignis einer benutzerverwalteten Webhook-Verbindung und geht nie
  an n8n oder andere externe Ziele
  ([contracts/events/collection.updated.md](https://github.com/bl0rb/Weave/blob/main/contracts/events/collection.updated.md#dienstgrenze)).
- **Der Registry-Sync selbst ist ein eigenes Service-Credential.** Knowledge
  holt die Rechtematrix mit `WEAVE_KNOWLEDGE_INGEST_API_TOKEN`, das nur die
  Registry lesen und freigegebene Snapshots holen darf — kein persönlicher
  Admin-Token mehr, der nebenbei Benutzer verwalten könnte
  ([[Erste-Schritte]], Schritt 5).

---

## Delegations-Token: warum ein Automat nie mehr sieht

Ein n8n-Flow oder ein MCP-Client fragt nicht für sich selbst, sondern im
Auftrag eines Menschen. Damit stellt sich die Frage, mit wessen Rechten er
arbeitet.

Ein **Delegations-Token** ist die Antwort darauf: ein kurzlebiges, signiertes
Ausweispapier, das Weave-Runtime für genau einen Gesprächszug ausstellt. Der
entscheidende Bau: **Das Token ist der Umfang.** Die Liste der Wissensbereiche,
die der fragende Mensch in diesem Moment lesen darf, ist in das Token
hineingeschrieben und mitsigniert. Der Automat bekommt kein Konto und keine
Kopie des dauerhaften persönlichen Tokens des Menschen, sondern eine Leihgabe,
die nach kurzer Zeit von selbst wertlos wird (standardmäßig fünf Minuten, siehe
[docs/betrieb.md, Abschnitt 9](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#9-n8n-und-mcp)).

Daraus folgt das Versprechen der Überschrift, und zwar in beide Richtungen:

- **Hinweg.** Der erlaubte Umfang wird immer serverseitig aus der Identität
  abgeleitet, nie aus dem, was der Aufrufer behauptet. Ein Bereichsname, der
  aus einem Werkzeugaufruf, einem Anfrage-Body oder einer Modellausgabe kommt,
  kann innerhalb des Tokens einschränken — erlauben kann er nichts.
- **Rückweg.** Was ein Flow als Quelle seiner Antwort zurückmeldet, ist
  zunächst nur eine Behauptung. Weave prüft jede gemeldete Quelle gegen genau
  den Umfang, der in dieses eine Token signiert wurde, und verwirft, was nicht
  passt. Verworfen wird immer nur die einzelne Quelle, nie der ganze Aufruf;
  im Trace erscheint deren Anzahl als `trace.n8n.dropped_sources`.

Zwei weitere Eigenschaften, die dasselbe absichern:

- **Kein Zwischenspeicher.** Der Rechteumfang wird bei jedem einzelnen Aufruf
  frisch aufgelöst. Ein zurückgezogenes persönliches Token oder ein entzogener
  Gruppenzugang wirkt damit ab der nächsten Anfrage, nicht erst wenn irgendein
  Ablaufzeitpunkt erreicht ist.
- **Fehlt das Geheimnis, wird nichts ausgestellt.** Ohne konfiguriertes
  Signaturgeheimnis verweigert Weave das Ausstellen und das Einlösen —
  ausdrücklich mit einem Konfigurationsfehler, nie mit einem ungeprüften
  Durchlass. Ein leeres Geheimnis wäre eine gültige, von jedem nachrechenbare
  Signatur; genau deshalb wird dieser Fall zuerst geprüft.

Wer die Mechanik nachlesen will:
[services/tools/app/services/scope.py](https://github.com/bl0rb/Weave/blob/main/services/tools/app/services/scope.py)
beschreibt Format und Prüfdisziplin des Tokens ausführlich,
[services/runtime/backend/app/services/delegation.py](https://github.com/bl0rb/Weave/blob/main/services/runtime/backend/app/services/delegation.py)
das Ausstellen, und der
[n8n-Flow-Vertrag](https://github.com/bl0rb/Weave/blob/main/contracts/n8n-flow.md#delegations-token)
die Regeln für den Flow selbst. Was ein Bot darüber hinaus einschränken kann,
steht auf [[Chat-und-Bots]].

---

## MCP mit eigener Anmeldung (SSO)

Seit v0.7.0 kann sich ein MCP-Client wie VS Code auch direkt mit dem Konto des
Menschen anmelden, der davor sitzt — über denselben Unternehmens-Anbieter
(Entra ID oder Keycloak), den das Portal nutzt. Der Client holt sich beim
Anbieter ein Zugriffstoken; Weave-Ingest prüft dessen Signatur, Aussteller,
Zielgruppe und Ablauf und ordnet es einem **vorhandenen, aktiven** Benutzer zu.

Zwei Dinge macht Weave dabei ausdrücklich nicht:

- **Kein Konto aus dem Token.** Gibt es den Benutzer in Weave nicht, bleibt der
  Zugang zu. Angelegt wird niemand.
- **Keine Gruppen aus dem Token.** Was der Client sehen darf, kommt aus den
  Gruppen, die in Weave gepflegt sind — nicht aus Gruppen oder Rollen, die der
  Anbieter in das Token schreibt.

Für den Rechteumfang gilt damit dasselbe wie im Chat: genau die Wissensbereiche,
die dieser Mensch lesen darf, bei jedem Aufruf frisch aufgelöst. Die
Einrichtung beim Anbieter und in Weave beschreibt
[docs/integrations/vscode-mcp-sso.md](https://github.com/bl0rb/Weave/blob/main/docs/integrations/vscode-mcp-sso.md),
Schritt für Schritt auch [[MCP-mit-SSO-einrichten]].

Die Desktop-App (seit v0.7.8) geht einen verwandten Weg: Sie meldet sich wie
der Browser über die Anmeldeseite von Ingest an, nimmt die Antwort aber auf
einer lokalen Adresse entgegen und löst den Einmalcode nur mit dem passenden
PKCE-Nachweis ein (RFC 8252). Der Betreiber schaltet das in Weave-API mit
`NATIVE_LOGIN_ENABLED` frei; ohne diesen Schalter lehnt das Gateway lokale
Rücksprungadressen ab.

---

## Maßgebliche Quellen

| Thema | Quelle |
|---|---|
| Anmeldung einrichten, beide Wege | [docs/betrieb.md, Abschnitt 8](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#8-anmeldung-einrichten) |
| MCP-Anmeldung über SSO | [docs/integrations/vscode-mcp-sso.md](https://github.com/bl0rb/Weave/blob/main/docs/integrations/vscode-mcp-sso.md) |
| n8n, MCP, Delegation | [docs/betrieb.md, Abschnitt 9](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#9-n8n-und-mcp) |
| Warum Ingest der Identitätsanbieter ist | [ADR-0006](https://github.com/bl0rb/Weave/blob/main/docs/adr/0006-foederierte-anmeldung.md) |
| Warum Retrieval direkt und nur lesend auf die Daten geht | [ADR-0005](https://github.com/bl0rb/Weave/blob/main/docs/adr/0005-geteiltes-read-model.md) |
| Berechtigte Gruppen je Wissensbereich, Sync und Rechteentzug | [contracts/chunk-store.md](https://github.com/bl0rb/Weave/blob/main/contracts/chunk-store.md) |
| Besitz und Freigaben von Wissensbereichen und Bots | [ADR-0008](https://github.com/bl0rb/Weave/blob/main/docs/adr/0008-besitz-freigaben-metadaten.md) |
| Wissensgedächtnis und seine Rechtegrenze | [ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md) |
| Grundregel Rechte über die ganze Kette | [contracts/n8n-flow.md](https://github.com/bl0rb/Weave/blob/main/contracts/n8n-flow.md) |
| Wissensbereiche im Portal verwalten | [docs/wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md) |
| Netzaufbau und dienstinterne Zugangsdaten | [README, „Network design"](https://github.com/bl0rb/Weave/blob/main/README.md#network-design) |
| Begriffe nachschlagen | [docs/glossar.html](https://github.com/bl0rb/Weave/blob/main/docs/glossar.html) |

Bildschirmfotos der zugehörigen Oberflächen — Anmeldung, Gruppenverwaltung,
SSO-Anbieter, Wissensbereiche in der Administration — liegen unter
[docs/screenshots/user-wiki/](https://github.com/bl0rb/Weave/tree/main/docs/screenshots/user-wiki).

Siehe auch: [[Wissensbereiche-und-Freigaben]], [[Chat-und-Bots]],
[[Betrieb]], [[Haeufige-Fragen]].
