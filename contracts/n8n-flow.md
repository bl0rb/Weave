# Vertrag: n8n als Bot-Provider

**Vertrag-Version:** 1
**Status:** angenommen (Weave-Runtime und Weave-Tools sind in diesem Monorepo implementiert; konkrete n8n-Flows werden vom jeweiligen Betreiber in n8n gepflegt)
**Anbieter (Owner):** Weave-Runtime — ruft den Webhook auf (`backend/app/services/n8n_client.py`), entscheidet WELCHE Chat-Turns dorthin gehen (`backend/app/services/chat.py`s `_run_n8n_turn`), und stellt das Delegations-Token aus (`backend/app/services/delegation.py`).
**Konsument (Caller dieses Vertrags, in der Gegenrichtung Anbieter der Token-Pruefung):** ein n8n-Agentenflow, adressiert ueber `bot.n8n.webhook_url` (`backend/app/schemas/bot.py`s `N8nConfig`; der Bot kann aus einer lokalen YAML-Datei oder aus der zentralen Ingest-Administration stammen) — und, sobald jener Flow das Delegations-Token gegen Weave-Tools einloest, Weave-Tools selbst als Token-Verifizierer.

## Zweck

Ein Bot kann `model.provider: n8n` setzen, um seinen kompletten Chat-Turn an einen n8n-Agentenflow zu delegieren, statt ihn an ein direktes LLM zu schicken. Der n8n-Flow bekommt dabei — verpackt in ein kurzlebiges, signiertes **Delegations-Token** — GENAU den Lese-Umfang (Collections), den der gerade anfragende Mensch ohnehin schon hat, nie mehr. Damit kann der Flow selbststaendig gegen Weave-Tools (MCP oder REST) suchen und Aktionen ausfuehren, ohne dass Weave-Runtime selbst etwas ueber DIESE Suche/Aktion wissen muss — Weave-Runtime sieht am Ende nur `{answer, sources?}` zurueck.

Zwei getrennte kryptographische Pruefungen stecken in diesem Vertrag, mit unterschiedlichem Zweck — beide nutzen `WEAVE_DELEGATION_SECRET`, aber fuer verschiedene Dinge:

1. **Anfrage-Signatur** (`X-Weave-Signature`-Header): beweist n8n, dass DIESER Webhook-Aufruf tatsaechlich von Weave-Runtime kommt, nicht von irgendwem, der nur die `webhook_url` kennt.
2. **Delegations-Token** (`delegation_token`-Feld im Body): beweist Weave-Tools, WELCHEN Lese-Umfang der Aufrufer (der n8n-Flow, im Auftrag des urspruenglichen Menschen) fuer DIESEN einen Turn tatsaechlich hat.

## Grundregel Rechte (gilt fuer jede Stufe dieser Kette)

> Der erlaubte Collection-Umfang wird IMMER serverseitig aus der Identitaet abgeleitet (Personal-Token → Weave-API-Introspection → Team → lesbare Collections via Weave-Retrieval; ODER Delegations-Token → eingebetteter Umfang). Ein Collection-Name aus einem Tool-Argument, einem Anfrage-Body oder einer Modell-Ausgabe ist IMMER nur eine Einschraenkung INNERHALB des erlaubten Umfangs und niemals eine Erlaubnis. Fragt jemand nach einer Collection ausserhalb seines Umfangs: leeres Ergebnis, kein Fehler, kein Hinweis auf deren Existenz.

Konkret fuer diesen Vertrag: der n8n-Flow (und jedes Tool, das er aufruft) darf `allowed_collections`/eine vom LLM innerhalb des Flows vorgeschlagene Collection nur als FILTER innerhalb dessen verwenden, was das Delegations-Token tatsaechlich signiert — niemals als Nachweis, dass eine Collection ausserhalb davon zugaenglich sein sollte. Weave-Tools (der Token-Verifizierer) ist die Instanz, die das durchsetzt; ein n8n-Flow, der stattdessen dem `allowed_collections`-Feld im Webhook-Body selbst vertraut, verlaesst sich auf unsignierte Daten und untergraebt diese Grundregel.

## Wer entscheidet, ob ein Turn überhaupt hierher geht

Der Intent-Router (`backend/app/services/router.py`) läuft für einen n8n-Bot **unveraendert** wie für jeden anderen (siehe `backend/app/services/chat.py`s Modul-Docstring, Schritt 3/3a) — inklusive Permissions (`bot.permissions.teams`) und Collections-Scope-Aufloesung (`resolve_collection_scope`), die exakt dieselbe Funktion nutzt wie ein retrieval-gestuetzter Bot. Erst danach entscheidet der klassifizierte Intent, ob DIESER Turn ueberhaupt an n8n geht (`backend/app/services/chat.py`s `_run_n8n_turn`):

| Intent | Geht an n8n? | Warum |
|---|---|---|
| `conversational` | **Nein** — fester Antworttext, kein Webhook-Aufruf, kein Delegations-Token. | Ein Agentenflow existiert fuer Tool-/Suchaufgaben; einen vollen Webhook-Roundtrip (samt eigenem Fehlschlags-Risiko, `N8nUnavailable` eingeschlossen) fuer "Hallo" zu bezahlen, brächte niemandem etwas. |
| `knowledge`, `document`, `action`, `complex` | **Ja.** | Genau die Intents, fuer die ein Agentenflow mit Tool-Zugriff gedacht ist — insbesondere `document`/`action`/`complex`, die diese Pipeline fuer jeden ANDEREN Bot-Provider noch nicht unterstuetzt (V1-Platzhalter, siehe `contracts/internal-chat.md`), hier aber echt bedient werden. |

## Request: Weave-Runtime → n8n-Webhook

`POST bot.n8n.webhook_url` (siehe `backend/app/schemas/bot.py`s `N8nConfig`, bereits beim Zusammenstellen des Bot-Rosters gegen `N8N_ALLOWED_BASE_URLS` geprueft — fuer lokale YAML-Dateien ebenso wie fuer zentral verwaltete Bots, nie erst beim Aufruf; siehe `backend/app/services/botconfig.py`).

**Header:**

| Header | Wert |
|---|---|
| `Content-Type` | `application/json` |
| `X-Weave-Signature` | Hex-kodiertes HMAC-SHA256 ueber die ROHEN Body-Bytes, Schluessel `WEAVE_DELEGATION_SECRET` — siehe "Signatur-Pruefung" unten. |

**Body** (`backend/app/services/n8n_client.py`s `run_flow`, kanonisch mit `sort_keys=True, separators=(',',':')` serialisiert — die exakten Bytes, ueber die die Signatur oben berechnet wird):

| Feld | Typ | Bedeutung |
|---|---|---|
| `message` | `string` | Die aktuelle Nutzer-Nachricht (`ChatRequest.message`, unveraendert). |
| `history` | `list[{role, content}]` | Bisheriger Verlauf, aelteste zuerst, OHNE `message` selbst — identisch zu `ChatRequest.history` (`backend/app/schemas/chat.py`), nur zu Klartext-Dicts entpackt. |
| `user` | `{id, username, team}` | Propagierte Identitaet (`ChatUser`) — alle drei Felder koennen `null` sein (anonymer/System-Chat). |
| `bot_id` | `string` | `BotConfig.id` — der n8n-Flow kann darueber (falls ein Flow mehrere Bots bedient) sein eigenes Prompting/Verhalten adressieren; der Bot-eigene `system_prompt` wird NICHT separat mitgeschickt (siehe "Warum kein `system_prompt`-Feld" unten). |
| `allowed_collections` | `list[string]` | Der von `resolve_collection_scope` aufgeloeste Scope, UNVERAENDERT — kann den Sentinel `"__none__"` enthalten (Altbestand, siehe `contracts/internal-chat.md`). Eine bequeme, unsignierte Kopie desselben Scopes, der auch im `delegation_token` signiert steckt — siehe "Grundregel Rechte" oben: ein Flow, der SICH SELBST auf dieses Feld statt auf die Token-Pruefung verlaesst, verlaesst sich auf unsignierte Daten. |
| `delegation_token` | `string` | Frisch fuer GENAU diesen Aufruf ausgestellt (siehe "Delegations-Token" unten) — niemals wiederverwendet, niemals gecacht. |
| `tools_base_url` | `string` | `settings.tools_base_url` — wo Weave-Tools fuer den Flow erreichbar ist (MCP oder REST), damit der Flow keine eigene Konfiguration dafuer braucht. |

**Warum kein `system_prompt`-Feld:** ein n8n-Flow ist bereits eindeutig ueber `webhook_url` (und, falls ein Flow mehrere Bots bedient, zusaetzlich `bot_id`) adressiert — sein Verhalten/Prompting lebt als Teil DIESES Flows in n8n selbst, nicht als Text, den Weave-Runtime bei jedem Aufruf erneut mitschicken muesste. `BotConfig.system_prompt` bleibt trotzdem ein Pflichtfeld (rein dokumentarisch fuer einen Operator, der die Bot-YAML liest, ohne den n8n-Flow selbst zu oeffnen — siehe `bots/n8n-agent.yaml.example`).

## Delegations-Token

Ausgestellt von `backend/app/services/delegation.py`s `mint_delegation_token(user, collections, bot_id)`, einmal PRO Webhook-Aufruf (nie gecacht). Format bewusst ohne neue Abhaengigkeit — nur `hashlib`/`hmac`/`json`/`base64` der Standardbibliothek:

```
token = b64url(json(payload)) + "." + b64url(hmac_sha256(secret, b64url(json(payload))))
```

`b64url` = URL-safe Base64 OHNE Padding. `json(payload)` = `json.dumps(payload, sort_keys=True, separators=(',', ':'))` — deterministisch, damit ein Verifizierer dieselben Bytes exakt rekonstruieren kann.

**`payload`** (jedes Feld immer vorhanden):

| Feld | Typ | Bedeutung |
|---|---|---|
| `v` | `int` | Token-Format-Version, aktuell immer `1`. |
| `sub` | `string` | `user.id`, oder `""` wenn nicht propagiert. |
| `username` | `string` | `user.username`, faellt auf `user.id` zurueck, dann auf `""` — nie `null` (anders als `team`/`bot`). |
| `team` | `string \| null` | `user.team`, unveraendert. |
| `collections` | `list[string]` | Der von `resolve_collection_scope` aufgeloeste Scope — kann `"__none__"` (Altbestand-Sentinel) enthalten. **Das ist der eigentliche Umfang, den dieses Token gewaehrt.** |
| `bot` | `string \| null` | `BotConfig.id` — bei jedem heutigen Aufrufer (`_run_n8n_turn`) immer gesetzt. |
| `iat` | `int` | Ausstellungszeitpunkt, Unix-Sekunden. |
| `exp` | `int` | `iat + DELEGATION_TOKEN_TTL_SECONDS` (Default 300). |

**Ausstellung (Weave-Runtime, `mint_delegation_token`):** `WEAVE_DELEGATION_SECRET` nicht konfiguriert → **harter Fehler** (`DelegationConfigError`), kein unsigniertes Token, kein Fallback. `collections` kommt unveraendert von `resolve_collection_scope` — diese Funktion selbst trifft KEINE eigene Zugriffsentscheidung, sie signiert nur, was ihr Aufrufer bereits aufgeloest hat (siehe `contracts/internal-chat.md`s Collections-Abschnitt fuer die volle `resolve_collection_scope`-Logik, hier unveraendert wiederverwendet).

**Pruefung (Weave-Tools, der Verifizierer in `services/tools`):**

1. Signatur pruefen mit `hmac.compare_digest` (konstante Zeit) — Schluessel: dasselbe `WEAVE_DELEGATION_SECRET`.
2. `payload.v == 1`.
3. `payload.exp > jetzt` — KEIN Toleranzfenster groesser als 60 Sekunden.
4. Bei IRGENDEINEM Fehler in 1-3: EIN generischer Fehler zurueckgeben. Niemals unterscheiden, WORAN es lag (falsche Signatur vs. abgelaufen vs. falsche Version) — das wuerde einem Angreifer verraten, welcher Teil eines gefaelschten/manipulierten Tokens als naechstes zu reparieren waere.
5. Der derart verifizierte `payload.collections` (plus `payload.team`, falls fuer eine feinere Pruefung noetig) ist der GESAMTE erlaubte Umfang fuer diesen Aufruf — jede Collection, die ein MCP-Tool-Argument oder ein REST-Body-Feld zusaetzlich nennt, ist wie in der Grundregel oben nur eine Einschraenkung DAVON, nie eine Erweiterung.

**Geheimhaltung:** das Token ist ein Bearer-Geheimnis, exakt wie `RUNTIME_API_TOKEN`/`RETRIEVAL_API_TOKEN`. NIE in Logs, NIE in einer Fehlermeldung, NIE in einer Trace-Ausgabe — weder auf Weave-Runtime- noch auf n8n-/Weave-Tools-Seite. `backend/app/services/n8n_client.py` und `backend/app/services/delegation.py` halten sich beide explizit daran (siehe deren eigene Docstrings); ein n8n-Flow, der das Token in seinem eigenen Execution-Log mitschreibt (n8n tut das standardmaessig fuer JEDEN Node-Input/Output!), verletzt diesen Vertrag — ein produktiver Flow MUSS das Token-Feld vor dem Logging redigieren oder n8ns "Save Manual Executions"/Log-Level entsprechend einschraenken.

## Wie der n8n-Flow Weave-Tools damit aufruft (REST vs. MCP)

Das `delegation_token` allein genuegt NICHT, um Weave-Tools' REST-Oberflaeche zu erreichen. Diese Repo-Grenze wird in der Praxis leicht uebersehen, weil der Rest dieses Vertrags nur den einen Header dokumentiert, den der Flow selbst AUSSTELLT (den `Authorization`-Header seines eigenen Weave-Tools-Aufrufs, mit dem `delegation_token` als Bearer-Wert) — tatsaechlich verlangt Weave-Tools' REST-Router (`services/tools/app/api/tools.py`) auf JEDER `/api/v1/tools/*`-Route zusaetzlich einen ZWEITEN, unabhaengigen Header, erzwungen von dessen `backend/app/api/deps.py`s `require_tools_service_token`:

| Header | Wert | Beantwortet welche Frage | Wer stellt ihn aus |
|---|---|---|---|
| `X-Tools-Service-Token` | `TOOLS_API_TOKEN` (Weave-Tools' eigenes Deployment-Geheimnis) | **Darf DIESER AUFRUFER (diese n8n-Instanz) ueberhaupt mit Weave-Tools reden?** Ein Service-zu-Service-Geheimnis, unabhaengig von jedem einzelnen Chat-Turn — dieselbe Rolle wie `RUNTIME_API_TOKEN`/`RETRIEVAL_API_TOKEN` an ihrer jeweiligen Dienstgrenze. | Der n8n-Flow selbst, als EIGENE, fest hinterlegte Zugangsdaten (z. B. ein n8n "Header Auth"-Credential am HTTP-Request-Node) — NIEMALS von Weave-Runtime im Webhook-Body mitgeschickt. |
| `Authorization` | `Bearer <delegation_token>` | **Mit wessen Rechten laeuft DIESER eine Aufruf?** Der kurzlebige, pro-Turn signierte Umfang, unveraendert aus dem Webhook-Body weitergereicht (siehe "Delegations-Token" oben). | Kommt aus dem Webhook-Body, den Weave-Runtime pro Turn frisch mitschickt — der Flow reicht ihn 1:1 an Weave-Tools weiter. |

Fehlt `X-Tools-Service-Token`, oder ist `TOOLS_API_TOKEN` bei Weave-Tools selbst nicht konfiguriert: `503`. Ist er gesetzt, aber falsch: `401` — BEIDES, bevor Weave-Tools ueberhaupt das `Authorization`-Delegations-Token anschaut (`require_tools_service_token` laeuft an dieser REST-Oberflaeche vor `get_scope`). Ein Flow, der nur den `Authorization`-Header nach diesem Vertrag setzt — wie eine Lektuere allein des Beispiel-Ablaufs unten nahelegen koennte — bekommt gegen Weave-Tools' REST-Endpunkte zuverlaessig `401`/`503` statt einer Antwort, nie ein stilles Falsch-Verhalten.

**Wichtig — wo `TOOLS_API_TOKEN` lebt:** es ist ein Service-Geheimnis VON Weave-Tools, kein Feld dieses Vertrags und kein Feld des Webhook-Bodys. Weave-Runtime kennt es nicht, besitzt es nicht und schickt es nirgends mit — ein Service-Geheimnis gehoert nicht in einen Payload, den ein anderer Dienst pro Turn zusammenstellt. Der Operator, der den n8n-Flow baut, hinterlegt `TOOLS_API_TOKEN` EIGENSTAENDIG als n8n-Zugangsdaten fuer den HTTP-Request-Node, der Weave-Tools' REST-Endpunkte aufruft — komplett ausserhalb dessen, was Weave-Runtime pro Chat-Turn kontrolliert oder auch nur sehen kann.

**MCP-Weg zum Vergleich:** ruft der Flow stattdessen Weave-Tools' MCP-Server auf (`services/tools/app/mcp_server.py`, streamable-HTTP), genuegt EIN einziger Header — `Authorization: Bearer <delegation_token>`. Der MCP-Server hat keine zu `require_tools_service_token` aequivalente Pruefung; jedes MCP-Tool (`list_collections`, `search`) loest seinen Scope ausschliesslich aus diesem einen Header auf. Ein Flow, der ueber MCP statt REST spricht, braucht `TOOLS_API_TOKEN` also gar nicht erst zu konfigurieren — das ist ein bewusster Unterschied zwischen den beiden Transportwegen, keine Luecke in einem von beiden.

## Signatur-Pruefung (n8n prueft Weave-Runtime)

Beim EMPFANG des Webhook-Aufrufs prueft der n8n-Flow selbst (typischerweise als erster Schritt, vor jeder weiteren Verarbeitung):

1. Rohen Request-Body als Bytes lesen (nicht erst JSON-parsen und neu serialisieren — das koennte eine andere Byte-Reihenfolge erzeugen und die Signatur faelschlich als ungueltig erscheinen lassen).
2. `hmac_sha256(WEAVE_DELEGATION_SECRET, <rohe Body-Bytes>)` berechnen, hex-kodieren.
3. Mit dem `X-Weave-Signature`-Header vergleichen — konstante Zeit (`hmac.compare_digest`-Aequivalent in der jeweiligen n8n-Node-Umgebung, z. B. ueber eine Function-Node mit Node.js' `crypto.timingSafeEqual`).
4. Bei Nichtuebereinstimmung: Aufruf ablehnen (z. B. HTTP 401 als Webhook-Antwort), NIE weiterverarbeiten.

## Response: n8n-Webhook → Weave-Runtime

`200 OK`, JSON-Body:

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `answer` | `string` | ja | Fertiger Antworttext. |
| `sources` | `list[Source]` | nein | Wie `backend/app/schemas/chat.py`s `Source` — `source` (`string`, leer erlaubt — `""` ist die gleiche Konvention wie bei einem retrieval-gestuetzten Treffer ohne Herkunftssystem, siehe dortiges `_to_source`), `document_id` (`string`) und `chunk_id` (`int`) sind PFLICHT auf jedem Eintrag; alle anderen Felder (`original_filename`, `page_start`, `page_end`, `document_version`, `score`, `collection`) optional. `collection` (`string \| null`) benennt, welcher Collection dieser Fund laut Flow entstammt — `null`/fehlend bedeutet Altbestand ohne Collection, identische Bedeutung wie bei einem retrieval-gestuetzten Treffer (`Weave-Retrievals SearchResult.collection`). Fehlt das `sources`-Feld komplett, wird `[]` angenommen — das ist ein normaler, unauffaelliger Fall (siehe unten), kein Fehler. |

**Fehlerbilder** (`backend/app/services/n8n_client.py`):

| Bedingung | Weave-Runtime-Verhalten |
|---|---|
| Timeout, Verbindungsfehler, oder `5xx` | `N8nUnavailable` → `503` (identisch zu Weave-Retrievals `RetrievalUnavailable`, README's Response Guard). |
| Beliebiger anderer `4xx` | `N8nError` → bewusst NICHT abgefangen, Standard-`500` (Deployment-Fehler im Flow selbst, kein Teil des regulaeren Fehlerbilds — identisch zu Weave-Retrievals `RetrievalError`-Behandlung). |
| `200`, aber Body ohne string `answer`, oder `sources` weder fehlend/`null` noch eine Liste, oder ein `sources`-Eintrag ohne `source`/`document_id`/`chunk_id` | `N8nError` (klare Fehlermeldung, nennt `webhook_url`) → ebenfalls `500`. |

### Quellen sind eine Behauptung, keine Berechtigung

Die von n8n zurueckgemeldeten `sources` sind unbelegter Text aus einer EXTERNEN Quelle — dieser Vertrag verifiziert nur, DASS der Webhook-Aufruf von Weave-Runtime kam (Signatur-Pruefung oben) und dass die ANTWORT dem Schema entspricht (Tabelle oben), NIE, ob der Flow selbst innerhalb seines eigenen, durch das Delegations-Token belegten Umfangs geblieben ist. Ein fehlerhafter, falsch konfigurierter oder kompromittierter Flow koennte hier grundsaetzlich eine `collection` eintragen, auf die der urspruenglich fragende Mensch gar keinen Zugriff hat — und ohne eigene Pruefung wuerde das niemand bemerken.

Weave-Runtime vertraut deshalb NICHT blind auf `sources[].collection`. Nach jedem n8n-Aufruf prueft `backend/app/services/chat.py`s `_run_n8n_turn` (via `_filter_n8n_sources_by_scope`) JEDE gemeldete Quelle einzeln gegen genau den Umfang, der in DIESEM Aufruf ins Delegations-Token signiert wurde (denselben, der auch als `allowed_collections` im Request-Body oben stand) — dieselbe "Grundregel Rechte" wie ueberall sonst in diesem Vertrag, hier auf die RUECKRICHTUNG angewendet:

- Traegt eine Quelle eine `collection`, die NICHT im signierten Umfang liegt: die Quelle wird VERWORFEN.
- Traegt eine Quelle GAR KEINE `collection` (`null`/fehlend): sie gilt als Altbestand-Behauptung und wird nur behalten, wenn der signierte Umfang selbst den Sentinel `"__none__"` enthaelt — dieselbe Sentinel-Regel, mit der `resolve_collection_scope` entscheidet, ob dieser Sentinel ueberhaupt in den Umfang aufgenommen wird (siehe `contracts/internal-chat.md`), hier nur in umgekehrter Richtung angewendet.
- Verworfen wird IMMER nur die einzelne Quelle, nie der ganze Aufruf — eine Antwort mit teils gueltigen, teils verworfenen Quellen liefert die gueltigen trotzdem aus, mit `answer` unveraendert.
- Die Anzahl verworfener Quellen landet in `trace.n8n.dropped_sources` (`0` im Normalfall); Weave-Runtime loggt zusaetzlich eine Warnung mit `bot_id` und dieser Anzahl — NIE mit Quelleninhalten (kein Dokument, keine Collection, kein Text).
- Bleiben nach dieser Pruefung keine Quellen mehr uebrig UND verlangt der Bot Quellen (`bot.guard.require_sources`): der normale Guard greift, exakt wie im Absatz "Guard nach dem n8n-Aufruf" unten beschrieben (`no_context`) — unabhaengig davon, ob n8n urspruenglich ueberhaupt Quellen gemeldet hatte.

Ein n8n-Flow, der eine Collection ausserhalb seines eigenen, durch das Token belegten Umfangs meldet, bekommt also KEINEN Fehler zurueck — die betroffene Quelle verschwindet einfach, unauffaellig, aus der Antwort. Das ist bewusst dieselbe "leeres Ergebnis, kein Fehler, kein Hinweis auf deren Existenz"-Haltung wie bei jeder anderen Umfangs-Ueberschreitung in diesem Vertrag (siehe "Grundregel Rechte" oben).

**Guard nach dem n8n-Aufruf:** verlangt der Bot Quellen (`bot.guard.require_sources`, Default an) und `sources` ist NACH der obigen Umfangs-Pruefung leer/fehlt, ersetzt Weave-Runtime `answer` durch `bot.guard.no_context_reply` (`trace.guard = {triggered: true, reason: "no_context"}`) — exakt dasselbe Guard-Verhalten wie bei einer retrieval-gestuetzten Wissensfrage ohne Treffer, nur ausgeloest durch n8n's eigene (gefilterte) Antwort statt durch Weave-Retrieval. Liefert n8n Quellen, die die Umfangs-Pruefung ueberstehen, werden GENAU diese als `ChatResponse.sources` uebernommen.

## Streaming

`POST /internal/chat/stream` behandelt einen n8n-Bot GENAUSO wie jeden anderen (`trace` → `delta`* → `sources`+`done`/`error`), mit EINER dokumentierten Abweichung: n8n antwortet blockierend, in einem Stueck — es gibt keinen inkrementellen n8n-eigenen Antwort-Kanal. Eine ERFOLGREICHE n8n-Antwort wird deshalb als **genau EIN** `delta`-Ereignis gestreamt (nicht wortweise zerlegt wie ein LLM-Antworttext oder ein fester Platzhalter/Guard-Text) — ein Konsument sieht damit ehrlich, WIE die Antwort tatsaechlich ankam, statt eine Granularitaet vorzutaeuschen, die n8n nie geliefert hat. Ein durch den Guard ersetzter Text (`no_context_reply`) oder die feste `conversational`-Antwort (siehe oben) werden dagegen wie gewohnt wortweise gestreamt — nur eine ECHTE n8n-Antwort bekommt die Ein-Delta-Behandlung.

## Beispiel-Ablauf

1. Ein Mitarbeiter im Team `legal` fragt den Bot `legal-agent` (`model.provider: n8n`): *"Welche Vertraege laufen naechsten Monat aus, und lege dafuer ein Erinnerungs-Ticket an."*
2. Weave-API loest Identitaet/Team auf (`{id: "u-42", username: "j.schmidt", team: "legal"}`) und ruft `POST /internal/chat` (oder `/stream`) bei Weave-Runtime auf.
3. Weave-Runtime: `load_bot("legal-agent")` → Permissions-Check (`legal` ist erlaubt) → Router klassifiziert `complex` (Suche UND Aktion in einer Nachricht) → `bot.model.provider == 'n8n'` → `_run_n8n_turn`.
4. `_run_n8n_turn` ruft `resolve_collection_scope(bot, user)` auf — exakt wie ein retrieval-gestuetzter Bot es taete — und erhaelt z. B. `["vertraege", "__none__"]` (das Team `legal` darf `vertraege` lesen, plus Altbestand).
5. `mint_delegation_token(user, ["vertraege", "__none__"], "legal-agent")` signiert genau diesen Scope, mit `sub="u-42", username="j.schmidt", team="legal"`, `exp` 300 Sekunden in der Zukunft.
6. Weave-Runtime POSTet an `bot.n8n.webhook_url` mit dem oben spezifizierten Body + `X-Weave-Signature`.
7. Der n8n-Flow: prueft die Signatur (Abschnitt "Signatur-Pruefung" oben) → extrahiert `delegation_token`/`tools_base_url` → ruft Weave-Tools' Such-Tool auf (MCP: nur `Authorization: Bearer <delegation_token>`; REST: ZUSAETZLICH `X-Tools-Service-Token` aus den eigenen n8n-Zugangsdaten, siehe "Wie der n8n-Flow Weave-Tools damit aufruft" oben), mit einem Suchbegriff wie "Vertraege Laufzeit naechster Monat" und optional `collection: "vertraege"` als zusaetzlichem FILTER (siehe Grundregel Rechte: das grenzt nur innerhalb des Tokens ein, es erweitert nichts).
8. Weave-Tools verifiziert das Token (Abschnitt "Delegations-Token" oben) und fuehrt die Suche NUR innerhalb `["vertraege", "__none__"]` aus — eine vom Flow (oder einem darin laufenden LLM) versehentlich oder absichtlich angefragte andere Collection liefert ein leeres Ergebnis, keinen Fehler, keinen Hinweis auf deren Existenz.
9. Der n8n-Flow erhaelt die Treffer, ruft anschliessend ein zweites Tool auf, um das Erinnerungs-Ticket anzulegen (ausserhalb des Collections-Umfangs, aber innerhalb dessen, wofuer dieser Flow ueberhaupt gebaut wurde), und formuliert eine Antwort.
10. Der Flow antwortet `200 OK` mit `{"answer": "Die folgenden drei Vertraege laufen im naechsten Monat aus: ... Ich habe dafuer Ticket LEGAL-482 angelegt.", "sources": [{"document_id": "doc-17", "chunk_id": 3, "source": "sharepoint", "score": 0.91}, ...]}`.
11. Weave-Runtime validiert die Form, prueft jede gemeldete Quelle gegen den signierten Umfang `["vertraege", "__none__"]` (Abschnitt "Quellen sind eine Behauptung, keine Berechtigung" oben — hier bleiben alle Quellen erhalten, da der Flow sich an seinen eigenen Umfang gehalten hat), prueft den Guard (Quellen vorhanden → kein Guard-Treffer), und liefert `ChatResponse`/den SSE-Stream mit dieser Antwort und diesen Quellen zurueck — fuer den Aufrufer nicht unterscheidbar von einer retrieval-gestuetzten Antwort, ausser dass `trace.model` `null` bleibt und `trace.timings_ms` ein `n8n_ms` statt `llm_ms` traegt.

## Versionierungsregel

Dieselbe Disziplin wie `contracts/internal-chat.md`: additiv (ein neues optionales Feld im Webhook-Body/in der Response, ein neuer `timings_ms`-Schluessel) erfordert keine Versionserhoehung; ein bestehendes Pflichtfeld aendern/entfernen, das Token-Format aendern, oder die Signatur-Berechnung aendern ist breaking und erfordert ein koordiniertes Deployment-Fenster ueber Weave-Runtime UND jeden existierenden n8n-Flow UND Weave-Tools' Verifizierer gleichzeitig.

## Änderungsprotokoll

- **v1 (2026-08-31):** Initialer Vertrag — n8n als Bot-Provider, Delegations-Token-Ausstellung (`backend/app/services/delegation.py`), Anfrage-Signatur und Webhook-Aufruf (`backend/app/services/n8n_client.py`), Routing-Entscheidung und Guard-Integration (`backend/app/services/chat.py`s `_run_n8n_turn`), SSRF-Allowlist fuer `webhook_url` (`backend/app/services/botconfig.py`). Die Weave-Tools-seitige Token-Pruefung ist in `services/tools` implementiert; nur der konkrete n8n-Flow liegt außerhalb von Weave.
- **v1, Haertung (2026-08-31, Sicherheits-Review):** additiv, keine Versionserhoehung. (1) `sources[].collection` neu dokumentiert; Weave-Runtime prueft jede von n8n gemeldete Quelle nach dem Aufruf gegen den im Delegations-Token signierten Umfang und verwirft, was nicht passt (`_filter_n8n_sources_by_scope`, Abschnitt "Quellen sind eine Behauptung, keine Berechtigung") — `trace.n8n.dropped_sources` zaehlt mit. (2) Abschnitt "Wie der n8n-Flow Weave-Tools damit aufruft" ergaenzt: Weave-Tools' REST-Oberflaeche verlangt zusaetzlich zum `Authorization`-Delegations-Token einen `X-Tools-Service-Token`-Header (eigene n8n-Zugangsdaten, NIE von Weave-Runtime mitgeschickt); der MCP-Weg braucht diesen zweiten Header nicht. (3) `_validate_n8n_webhook_allowlist` (`backend/app/services/botconfig.py`) vergleicht `N8N_ALLOWED_BASE_URLS`-Eintraege jetzt scheme/host/port-EXAKT (`urllib.parse`) statt per rohem String-Praefix — schliesst Bypass-Varianten ueber angehaengte Domains/Ports oder `userinfo@`-Host-Smuggling.
