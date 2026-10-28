Diese Seite ordnet ein, wie aus einer Frage im Chat eine belegte Antwort wird
und woher die Bots stammen, die dabei antworten. Sie ersetzt keine der
massgeblichen Beschreibungen: der Ablauf steht feldgenau in
[`contracts/internal-chat.md`](https://github.com/bl0rb/Weave/blob/main/contracts/internal-chat.md),
die Bot-Konfiguration in
[`services/runtime/README.md`](https://github.com/bl0rb/Weave/blob/main/services/runtime/README.md),
die Betriebsseite in
[`docs/betrieb.md`](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md).

## Wer was tut

Vier Dienste sind an einem Chat-Turn beteiligt, in dieser Reihenfolge. Das
gilt für jeden Einstieg gleich — Web-Chat, Desktop-App und MCP-Client reden
alle mit demselben Gateway.

- **Weave Chat** ([`services/chat`](https://github.com/bl0rb/Weave/blob/main/services/chat/README.md))
  ist die Weboberfläche. Sie hat keine Datenbank und genau einen Upstream: das
  Gateway. Sie ruft weder Runtime noch Retrieval direkt auf.
- **Weave-API** ([`services/api`](https://github.com/bl0rb/Weave/blob/main/services/api/README.md))
  ist das Gateway: Anmeldung, Rate-Limit, Konversations-Verlauf in PostgreSQL.
  Chat-Intelligenz gehört ausdrücklich nicht dazu.
- **Weave-Runtime** ([`services/runtime`](https://github.com/bl0rb/Weave/blob/main/services/runtime/README.md))
  ist der Bot-Orchestrator: Intent-Routing, Bot-Auswahl, Rechte-Schnittmenge,
  LLM-Aufruf, Response Guard. Sie ist zustandslos und speichert nichts.
- **Weave-Retrieval** ([`services/retrieval`](https://github.com/bl0rb/Weave/blob/main/services/retrieval/README.md))
  ist die Suche. Sie wird nur aufgerufen, wenn der Turn sie tatsächlich braucht.

Die Trennung ist der Grund dafür, dass ein Fehlerbild oft weit vom Symptom
entfernt liegt: eine Antwort ohne Quellen kann eine Router-Entscheidung sein,
eine leere Rechte-Schnittmenge, ein leerer Index oder eine nicht erreichbare
Suche. Der `trace`, den jede Antwort mitliefert, unterscheidet diese Fälle;
wie man ihn liest, steht in
[docs/betrieb.md, Abschnitt 10](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest).

## Von der Frage zur belegten Antwort

### Zuerst entscheidet der Router, ob überhaupt gesucht wird

Bevor irgendetwas durchsucht wird, klassifiziert der Intent-Router die Nachricht
in einen der Intents `conversational`, `knowledge`, `document`, `action`,
`complex` und — mit eingeschaltetem Wissensgedächtnis — `overview`. Nur
`knowledge` führt zu einer Suche; `overview` beantwortet der Bot aus den
Zusammenfassungen (siehe unten). `conversational` geht direkt ans Modell und
liefert bewusst keine Quellen. `document`, `action` und `complex` beantwortet
ein LLM-Bot in dieser Version mit einem festen Hinweistext, ohne Modellaufruf
— bei einem n8n-Bot dagegen werden genau diese Intents an den Flow
weitergereicht.

Ebenfalls vor jeder Suche prüft der Router die **gesperrten Themen** eines
Bots (seit v0.7.15): trifft die Nachricht ein Thema aus `guard.blocked_topics`,
antwortet der Bot mit dem dafür hinterlegten Text, ohne Suche, n8n-Aufruf oder
Modellaufruf. Schlüsselwörter wirken in beiden Router-Modi; im Modus `llm`
klassifiziert derselbe Router-Aufruf zusätzlich das Thema.

Zwei Punkte, die regelmässig missverstanden werden:

- Der Router läuft in zwei Modi (`ROUTER_MODE`): `rules` ist der Standard, ein
  deterministischer Schlüsselwort-Klassifikator ohne Netzaufruf; `llm` delegiert
  die Klassifikation an ein Modell und fällt bei jedem Fehler auf `rules` zurück,
  statt den Turn scheitern zu lassen.
- Eine Router-Entscheidung für `knowledge` genügt nicht, damit gesucht wird.
  Zusätzlich muss der Bot selbst Retrieval aktiviert haben. Ein Bot ohne
  Wissensanbindung löst nie einen Such-Aufruf der Runtime aus, egal wie die
  Frage lautet. Für einen n8n-Bot gilt das anders herum: Runtime sucht dort
  grundsätzlich nicht selbst, der Flow sucht mit seinem Delegations-Token.

Wer also glaubt, der Bot habe den Index nicht gefunden, sollte zuerst
`trace.intent` ansehen: `conversational` bei einer Wissensfrage heisst, dass gar
nicht gesucht wurde.

### Die Suche: zwei Signale, fusioniert, dann rerankt

Weave-Retrieval ist eine Hybrid-Suche. In Stichworten:

- Vektorsuche (pgvector) und Volltextsuche laufen **parallel** über denselben
  Korpus, jeweils bis `top_k` Treffer.
- **Reciprocal Rank Fusion** kombiniert beide Ranglisten allein über die
  Rang-Position, nicht über die Rohscores — Cosine-Similarity und Volltext-Rang
  sind nicht vergleichbar. Ein Chunk, den beide Signale finden, gewinnt fast
  immer.
- **Reranking** ist optional (`none` als Vorgabe, daneben ein Test-Provider und
  ein Cohere/Jina-kompatibler API-Provider). Ein Reranker-Ausfall lässt die Suche
  nie scheitern: sie fällt auf die Fusions-Reihenfolge zurück und markiert das im
  Trace.
- Erst **nach** dem Reranking wird auf `final_k` gekürzt. Ein Reranker kann eine
  Kandidatenmenge nur verkleinern, nie vergrössern.

Die vollständige Pipeline samt Parametern, Trace-Feldern und Fehlerbildern steht
in [`services/retrieval/README.md`](https://github.com/bl0rb/Weave/blob/main/services/retrieval/README.md).

### Der Response Guard: keine Belege, keine Antwort

Bei aktiviertem Retrieval erzwingt der Guard eine belegte Antwort. Findet die
Suche nichts Verwertbares, antwortet der Bot mit einem festen Text statt mit
einer vom Modell erfundenen Antwort. Das ist die Vorgabe, nicht eine Option, die
erst eingeschaltet werden muss.

Der Trace nennt drei unterschiedliche Gründe, und die Unterscheidung ist der
eigentliche Nutzen:

- `no_context` — gesucht wurde, gefunden wurde nichts.
- `no_collections` — es gab von vornherein keinen Wissensbereich, den dieser Bot
  und dieser Mensch beide nutzen dürfen. Es wurde gar nicht erst gesucht.
- `filter_excluded_all` — die Rechte hätten gereicht, aber die eigene Auswahl in
  der Seitenleiste hat den Suchraum geleert. Das ist der einzige der drei Fälle,
  den der Fragende allein beheben kann.

Zwei Dinge, die der Guard ausdrücklich **nicht** tut: er weicht beim Streaming
nicht auf. Ein Guard-Text wird als gewöhnlicher Textstrom ausgeliefert, die
Quellenpflicht bestimmt nur, welcher Text gestreamt wird. Und er springt nicht
für einen Ausfall ein: ist Weave-Retrieval nicht erreichbar, antwortet Runtime
mit einem Fehler, nie mit einer stillen, unbelegten Antwort.

Bei einem n8n-Bot gilt die Quellenpflicht in der Gegenrichtung. Was ein Flow als
Quelle zurückmeldet, ist eine Behauptung. Weave prüft jede gemeldete Quelle gegen
den signierten Umfang des Delegations-Tokens und verwirft, was nicht passt; die
verworfenen Einträge stehen im Trace. Details in
[`contracts/n8n-flow.md`](https://github.com/bl0rb/Weave/blob/main/contracts/n8n-flow.md).

### Das Wissensgedächtnis: Überblick statt Treffer (ab v0.7.9, Opt-in)

Eine Hybrid-Suche findet Textstellen, keine Übersicht. Auf „Was weißt du
eigentlich?“ oder „Welche Dokumente gibt es zur Reisekostenrichtlinie?“ liefert
sie bestenfalls zufällige Abschnitte. Dafür gibt es seit v0.7.9 das
**Wissensgedächtnis**: Weave-Knowledge baut nach jeder Änderung an einem
Wissensbereich — Freigabe, neue Version, Rücknahme, Umbenennung — je Dokument
eine Zusammenfassung mit Titel, Typ und Kernpunkten und je Bereich eine
Übersicht, entprellt pro Bereich. Der Betreiber schaltet es in `weave.yaml` mit
`KNOWLEDGE_MEMORY_MODE` ein: `extractive` kommt ohne Modellbudget aus, `llm`
nutzt den zentralen Chat-Provider und fällt bei Fehlern auf `extractive`
zurück. Standard ist `off`, und dann ändert sich nichts.

Drei Dinge, die man dazu wissen sollte:

- **Übersichtsfragen bekommen den Intent `overview`** und werden aus dem
  Gedächtnis beantwortet, mit Quellen, die der Chat als „Zusammenfassung“ oder
  „Bereichs-Übersicht“ kennzeichnet. Hat das Gedächtnis nichts, läuft die
  reguläre Suche.
- **Seit v0.7.10 lenkt das Gedächtnis auch Wissensfragen.** Ein Bot ohne
  eigene Retrieval-Filter fragt zuerst, welche Dokumente zur Frage passen, und
  konzentriert die Suche auf diese; findet sie dort nichts, sucht sie ohne
  Fokus erneut. Zitierbar bleiben nur echte Textabschnitte; die
  Zusammenfassungen erreichen das Modell als Orientierung, nie als Beleg. Im
  Agentenmodus steht Subagenten dafür ein Werkzeug `memory_lookup` zur
  Verfügung.
- **Die Rechtegrenze gilt unverändert.** Gedächtnis-Einträge werden mit
  demselben Prädikat gelesen wie Textabschnitte, und ohne konkrete Liste
  erlaubter Bereiche verweigert Weave-Retrieval die Anfrage.

Modus, Modell, Wartezeit und Grenzen stellt ein Administrator unter
**Administration → Bots & KI → Wissensgedächtnis** ein; dort stehen auch
Abdeckung, Fehler und Modellaufrufe je Bereich und ein bestätigter
Neuaufbau. Begründung und Grenzen des Entwurfs:
[ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md).

### Der Collection-Filter in der Oberfläche kann nur einschränken

In der Seitenleiste lassen sich Wissensbereiche auswählen. Das ist ein Filter für
die nächste Nachricht, keine Rechtevergabe. Die Auswahl schneidet nur von dem ab,
was ohnehin lesbar wäre; sie kann nichts hinzufügen. Keine Auswahl bedeutet kein
Filter, also alles, was erlaubt ist — nicht "nichts".

Die Autorität liegt serverseitig: Runtime bildet die Schnittmenge aus den
Bereichen, die der Bot nutzen darf, und denen, die der anfragende Mensch laut
seinen Gruppen lesen darf. Ein Bereichsname aus einem Anfrage-Feld, einem
Tool-Argument oder einer Modell-Ausgabe ist immer nur eine Einschränkung
innerhalb dieser Schnittmenge. Wie Leserechte an Wissensbereichen entstehen,
steht unter [[Wissensbereiche-und-Freigaben]] und [[Rechte-und-Sichtbarkeit]].

## Zwei Quellen für Bots

Runtime stellt die Bot-Liste vor jeder Anfrage neu zusammen, aus zwei Quellen.
Seit v0.7.12 fährt jeder Turn — direkter RAG-Bot, n8n-Bot, Agentenmodus mit
einem oder mehreren Subagenten — durch denselben kompilierten LangGraph-Graphen;
am Vertrag, am Trace und an den Fehlertexten hat das nichts geändert.

### Lokale YAML-Dateien

Bots als YAML-Dateien in Runtimes Bot-Verzeichnis (`BOTS_DIR`). Im
Compose-Betrieb ist das das Volume `runtime_bots`, das Docker beim ersten Mount
aus den Beispiel-Bots des Images befüllt; Dateien dort werden pro
Anfrage neu gelesen, ein Neustart ist nicht nötig. Feld-für-Feld-Beschreibung
eines Bots — Modell, Prompt, Retrieval, erlaubte Gruppen, Guard — in
[`services/runtime/README.md`](https://github.com/bl0rb/Weave/blob/main/services/runtime/README.md),
Volume-Details in
[`deploy/README.md`](https://github.com/bl0rb/Weave/blob/main/deploy/README.md).

### Zentral in Ingest gepflegte Bots

Seit v0.1.0 werden n8n-Bots nicht mehr als Datei auf der Platte gepflegt, sondern
zentral in Weave-Ingest, inzwischen unter **Administration → Bots & KI →
Bots** und — für die eigenen Bots — unter **Meine Bots** im Portal: Anzeigename,
Webhook-Adresse, optionales Flow-Token, Timeout, erlaubte Gruppen und
Wissensbereiche. Der Bot-Editor pflegt dort auch LLM-Bots mit Prompt,
Retrieval, Endpunkt und Guard. Runtime holt die aktivierten Einträge pro
Anfrage über ein eigenes Service-Token ab. Das Flow-Token liegt verschlüsselt
in Ingest und verlässt es nur über diese interne Route.

Der Grund für den Umzug: eine Änderung gilt ab der nächsten Anfrage, ohne dass
jemand eine Datei in einem Container bearbeitet oder einen Pod neu startet. Und
Gruppen- sowie Bereichsauswahl in der Maske sind, wie überall, reine
Einschränkungen — Runtime schneidet weiter mit den Rechten des Fragenden.

Ein aktiver Bot, den Runtime nicht geladen hat — typisch: eine n8n-Adresse
außerhalb der Allowlist —, steht seit v0.7.11 in der Bot-Verwaltung als
**Nicht im Chat** mit der wahrscheinlichen Ursache.

### Bots als Dokument: exportieren und importieren (ab v0.7.12)

Jeder Bot lässt sich als YAML oder JSON exportieren und in einer anderen
Umgebung wieder importieren; das Format ist
[`contracts/bot-document.md`](https://github.com/bl0rb/Weave/blob/main/contracts/bot-document.md).
Zwei Dinge stehen nie in der Datei: das n8n-Token (ersetzt durch einen
Platzhalter) und die Freigaben an Personen und Gruppen. Der Import ist
zweistufig — erst ein Probelauf, der Vorhaben, Hinweise und Fehler zeigt, dann
das Speichern — und überschreibt einen vorhandenen Bot nur auf ausdrücklichen
Wunsch. Eine unveränderte Exportdatei taugt nicht als YAML-Bot in `BOTS_DIR`:
Runtime lehnt sie wegen des Platzhalters ab
([betrieb.md, Abschnitt 9.1](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#91-bots-exportieren-und-importieren)).

### Was ein Bot außerdem festlegen kann (ab v0.7.14)

Im Bot-Editor und in Bot-Dokumenten gibt es vier Einstellungen, die das
Gespräch formen, ohne die Suche zu berühren:

- Eine **Einstiegsfrage** (`intake`) wird einmal vor der ersten Wissensantwort
  gestellt — als Auswahl oder Freitext — und ihre Antwort läuft danach in jedem
  Turn als Vorgabe mit. Der Chat zeigt die Optionen als Chips.
- **Gesperrte Themen** (`guard.blocked_topics`) mit fester Antwort, geprüft im
  Router vor jeder Suche (siehe oben).
- Ein **Hinweistext** (`disclaimer`) neben dem Eingabefeld, etwa „Testsystem —
  keine verbindlichen Aussagen“.
- **Quellenleiste an oder aus** (`show_sources`). Standardmäßig steht unter
  jeder belegten Antwort **Quellen anzeigen**; gelistet werden nur die Quellen,
  die die Antwort als `[N]` zitiert, und zu Website-Quellen Abrufdatum und
  gegebenenfalls Rechtsstand.

### Die Merge-Regel

Beide Quellen werden gegen dasselbe Bot-Schema und dieselbe Webhook-Allowlist
geprüft. Die Allowlist greift dabei **beim Zusammenstellen der Liste**, nicht erst
beim ersten Aufruf, und für beide Quellen gleich: weder eine Konfigurationsdatei
noch ein Administrator soll den Dienst in ein beliebiges Netz rufen lassen können.
Ist die Allowlist leer, sind n8n-Bots abgeschaltet.

Kollidieren die Quellen, gewinnt die Zentrale: **trägt ein zentraler Eintrag
dieselbe Bot-ID wie eine lokale YAML-Datei, überschreibt der zentrale Eintrag die
lokale Definition.** Eine lokale Datei ist damit ein Ausgangspunkt oder ein
Rückfall, nie ein stiller Konkurrent zur gepflegten Version.

### Der betriebliche Haken: ganz oder gar nicht

Die zentrale Anbindung ist bewusst nicht ausfalltolerant. Sind die beiden Werte
für Adresse und Service-Token gesetzt und Ingest antwortet nicht oder liefert
Unbrauchbares, **scheitert die gesamte Bot-Liste — auch die lokalen YAML-Bots.**
Runtime meldet sich `degraded`, `/v1/bots` antwortet über das Gateway mit `502`,
und jeder Chat-Turn hängt damit an der Erreichbarkeit von Ingest.

Das ist gewollt: eine halb alte, halb neue Bot-Definition wäre schlimmer als gar
keine. Es heisst aber, dass Ingest für den Chat kritisch wird, sobald die zentrale
Konfiguration eingeschaltet ist. Sind beide Werte leer, läuft Runtime eigenständig
weiter, nur mit den YAML-Bots. Die vollständige Begründung samt Symptomtabelle
steht in
[docs/betrieb.md, Abschnitt 9](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#9-n8n-und-mcp).

## Welches Sprachmodell antwortet (ab v0.7.1)

Unter **Administration → Bots & KI → Chat & LLM** gibt es neben dem zentralen Endpunkt
weitere, benannte LLM-Endpunkte, zum Beispiel ein Modell mit nativen
Tool-Aufrufen. Pro Bot gilt:

- **LLM-Endpunkt** legt fest, womit der Bot antwortet. Leer heisst: der
  zentrale Endpunkt.
- **Im Chat zur Auswahl anbieten** lässt Nutzer pro Chat zwischen diesem und
  weiteren Endpunkten wählen; im Chat erscheint dann neben dem Eingabefeld eine
  Auswahl **Sprachmodell**. *Alle aktiven Endpunkte* bietet jeden aktivierten
  an. Ohne Eintrag gibt es keine Auswahl.
- Ein **Subagent** im Agentenmodus kann einen eigenen Endpunkt nutzen. Zur Wahl
  stehen nur Endpunkte, die Tool-Aufrufe unterstützen.

YAML-Bots tragen dieselben Angaben als `model.endpoint` und `model.endpoints`
(`['*']` = alle aktiven). Runtime prüft jede Auswahl gegen die Liste des Bots;
einen Endpunkt, den ein Bot noch verwendet, lässt Ingest nicht löschen.
n8n-Bots bleiben bei dem Modell, das ihr Flow vorgibt.

## Weitere Wege zum Chat: Desktop-App, MCP, API

Der Web-Chat ist nicht der einzige Client des Gateways.

- **Die Desktop-App** ([clients/desktop](https://github.com/bl0rb/Weave/tree/main/clients/desktop),
  seit v0.7.8) sitzt in der Menüleiste oder im Infobereich: Tastenkürzel,
  Bot wählen, fragen — die Antwort wird gestreamt und mit Quellen gezeigt —
  oder alle lesbaren Wissensbereiche durchsuchen, ohne KI-Antwort. Sie nutzt
  dieselbe Anmeldung und sieht genau das, was dieselbe Person im Browser sieht.
  Aus dem Web-Chat heraus richtet **Desktop-App einrichten** sie mit einem
  Klick ein; dabei wandert nur die Serveradresse. „Im Web öffnen“ führt vom
  Desktop zurück in dasselbe Gespräch im Browser.
- **MCP-Clients** wie VS Code oder Claude Code bekommen über Weave-Tools die
  rechtegebundene Suche und die Bereichsliste, mit SSO der Organisation —
  [[MCP-mit-SSO-einrichten]].
- **Persönliche API-Tokens** für den OpenAI-kompatiblen Zugang und
  `POST /v1/search` gibt es weiterhin, aber die Selbstbedienung im Chat
  („API-Zugang“) ist seit v0.7.14 standardmäßig aus. Der Betreiber schaltet
  sie mit `SELF_SERVICE_API_TOKENS_ENABLED` wieder ein; früher ausgestellte
  Tokens bleiben gültig.

## Häufige Missverständnisse

- **"Der Bot findet unser Dokument nicht."** Meistens ist das keine Suchqualität,
  sondern eine Stufe davor: kein `knowledge`-Intent, kein Retrieval am Bot, keine
  gemeinsame Bereichs-Schnittmenge, oder das Dokument ist nie freigegeben worden.
  Siehe [[Wissensbereiche-und-Freigaben]].
- **"Die Auswahl in der Seitenleiste gibt mir Zugriff."** Sie schränkt nur ein.
  Rechte entstehen ausschliesslich über Gruppe und Wissensbereich.
- **"Bots konfiguriert man im Chat."** Die Oberfläche listet nur, was das Gateway
  zurückgibt. Gepflegt werden Bots in Runtimes Bot-Verzeichnis oder zentral in
  Ingest — in der Administration oder, für eigene Bots, unter **Meine Bots**.
- **"Der Bot kann keine Übersicht geben."** Kann er — wenn der Betreiber das
  Wissensgedächtnis eingeschaltet hat. Ohne `KNOWLEDGE_MEMORY_MODE` bleibt es
  bei der Trefferliste.
- **"Die Quellen fehlen unter der Antwort."** Die Leiste ist eingeklappt;
  **Quellen anzeigen** öffnet sie. Fehlt der Knopf ganz, hat der Bot
  `show_sources` abgeschaltet oder die Antwort war keine Wissensantwort.
- **"Antworten beginnen mit `[fake-llm]`, das ist ein Bug."** Das ist der
  Vorgabe-Provider für Entwicklung und Tests. Echte Provider einrichten:
  [[Betrieb]].

## Weiterlesen

- [[Wissensbereiche-und-Freigaben]] — wie Inhalte überhaupt durchsuchbar werden
- [[Rechte-und-Sichtbarkeit]] — woher die Schnittmenge kommt, die jeden Turn begrenzt
- [[Betrieb]] — Provider, Anmeldung, Selbsttest
- [`contracts/bot-document.md`](https://github.com/bl0rb/Weave/blob/main/contracts/bot-document.md) — das portable Bot-Format mit allen Feldern
- [ADR-0009](https://github.com/bl0rb/Weave/blob/main/docs/adr/0009-wissensgedaechtnis.md) — Wissensgedächtnis: Entwurf, Grenzen, Rechte
- [`contracts/internal-chat.md`](https://github.com/bl0rb/Weave/blob/main/contracts/internal-chat.md) — der Chat-Turn feldgenau, inklusive Trace und Guard-Gründen
- [`contracts/n8n-flow.md`](https://github.com/bl0rb/Weave/blob/main/contracts/n8n-flow.md) — Delegations-Token, Signaturprüfung, Quellenprüfung in der Gegenrichtung
