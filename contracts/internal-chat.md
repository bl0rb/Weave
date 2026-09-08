# Vertrag: `POST /internal/chat`

**Vertrag-Version:** 1
**Status:** angenommen
**Anbieter (Owner):** Weave-Runtime — implementiert die Route (`backend/app/api/internal.py`) und die dahinterliegende Pipeline (`backend/app/services/chat.py`); jede Feldbedeutung hier ist 1:1 aus `backend/app/schemas/chat.py`/`backend/app/schemas/bot.py` extrahiert, nicht aus README-Prosa.
**Konsument (Caller):** Weave-API — ausschliesslich über `backend/app/services/runtime_client.py:chat()` in jenem Repo, aufgerufen sowohl von `POST /v1/chat` (persistiert, mit `conversation_id`) als auch von `POST /v1/chat/completions` (zustandsloser OpenAI-kompatibler Shim, siehe dortiges `backend/app/api/openai_compat.py`).

## Zweck

Ein einzelner Chat-Turn: Weave-API schickt eine Nutzer-Nachricht plus bisherigen Verlauf; Weave-Runtime führt Intent-Routing, ggf. Retrieval gegen Weave-Retrieval, den LLM-Aufruf und den Response-Guard aus und liefert eine fertige Antwort samt Quellen und Debug-Trace zurück. Weave-Runtime selbst persistiert nichts — jeder Aufruf ist zustandslos, der komplette Verlauf wird bei jedem Turn erneut mitgeschickt (`history`).

## Auth

`Authorization: Bearer <RUNTIME_API_TOKEN>` — service-zu-service, geprüft von `require_service_token` (`backend/app/core/auth.py`), auf Router-Ebene vor JEDER `/internal/*`-Route (`backend/app/api/internal.py`). Ein leeres/unkonfiguriertes `RUNTIME_API_TOKEN` beantwortet **jede** Anfrage mit `503` — nie mit stillschweigend deaktivierter Auth (siehe Fehlerbilder unten). Es gibt keine zweite, endnutzerbezogene Auth-Schicht auf dieser Route: Authentifizierung/Autorisierung des einzelnen Endnutzers passiert am Gateway (Weave-API, ADR-0002); das `user`-Feld unten ist die propagierte Identität/Team-Zugehörigkeit aus jenem Schritt, keine erneute Prüfung von Credentials.

## Request

`ChatRequest` (`backend/app/schemas/chat.py`):

| Feld | Typ | Pflicht | Default | Bedeutung |
|---|---|---|---|---|
| `bot_id` | `string` | ja | — | ID eines Bots aus `BOTS_DIR` (`backend/app/services/botconfig.py`), z. B. `general-assistant`, `legal-support`. Unbekannt → `404`. |
| `message` | `string`, min. Länge 1 | ja | — | Die aktuelle Nutzer-Nachricht dieses Turns. Leer/fehlend → `422` (FastAPI-Validierung, nicht Teil dieses Vertrags-Fehlerbilds). |
| `history` | `list[ChatMessage]` | nein | `[]` | Bisheriger Gesprächsverlauf, älteste zuerst, **ohne** `message` selbst — Weave-Runtime hängt `message` intern als neuesten Turn an. |
| `user` | `ChatUser` | nein | `{}` (beide Felder `null`) | Propagierter Identitäts-/Team-Kontext (ADR-0002). |
| `collections` | `list[string] \| null` | nein | `null` | **Collection-Filter pro Anfrage** — reine EINSCHRÄNKUNG, niemals eine Rechtevergabe (siehe Collections-Vertrag unten). `null`/fehlend = kein Filter, exaktes Verhalten wie vor Einführung dieses Felds. Ein nicht-`null` Wert (auch `[]`, ein eigenständiger "matcht nichts"-Filter, unterscheidbar von "kein Filter") wird ERST NACH der bestehenden Rechte-Schnittmenge (`bot.retrieval.collections ∩ von user.team lesbare Collections`) angewendet und kann diese nur weiter einschränken, nie erweitern — ein hier genannter Slug ausserhalb dieser Schnittmenge wird still verworfen (kein Fehler, kein Hinweis auf seine Existenz). |

`ChatMessage`:

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `role` | `"user"` \| `"assistant"` | ja | **Kein** `"system"` — der System-Prompt gehört ausschliesslich zur Bot-YAML (`BotConfig.system_prompt`), wird nie pro Request mitgeschickt. |
| `content` | `string` | ja | |

`ChatUser` (Identität wird ausschließlich vom authentifizierten Gateway gesetzt):

Neue Gateway-Aufrufe senden immer `teams`. Eine vorhandene leere Liste bedeutet
keine Teamrechte und darf nie durch das alte `team` oder Botberechtigungen
ersetzt werden. Bei alten Aufrufern ohne Liste bleibt das Einzelteamformat
kompatibel. Botzugriff verlangt eine Überschneidung mit `permissions.teams`;
lesbare Collections sind die Vereinigung der für die Mitgliedschaften erlaubten
Collections, anschließend eingeschränkt durch Bot- und Anfragefilter.

| Feld | Typ | Bedeutung |
|---|---|---|
| `id` | `string \| null` | Anonyme/Systemaufrufe haben keine `id`. |
| `teams` | `list[string] \| null` | Vollständige verifizierte Teammitgliedschaften. `[]` erteilt keine Teamrechte; `null` ist ausschließlich das Legacy-Format. |
| `team` | `string \| null` | Wird gegen `BotConfig.permissions.teams` geprüft (siehe Fehlerbild 403) und bestimmt, welche Teams die anschliessende Weave-Retrieval-Anfrage sehen darf (`allowed_teams` — siehe `backend/app/services/chat.py:_allowed_teams`: bevorzugt `[user.team]`, fällt nur ohne `user.team` auf `bot.permissions.teams` zurück). |
| `username` | `string \| null` | Anzeigename, unabhängig von `id` propagierbar. Von KEINEM Teil dieser Pipeline für Routing/Retrieval/Permissions gelesen — ausschliesslich für den `n8n`-Bot-Provider relevant (`backend/app/services/delegation.py:mint_delegation_token`, siehe `contracts/n8n-flow.md`), der es in das Delegations-Token einbettet. |

## Response — `200 OK`

`ChatResponse`:

| Feld | Typ | Bedeutung |
|---|---|---|
| `answer` | `string` | Fertiger Antworttext — entweder die LLM-Antwort oder (bei ausgelöstem Guard bzw. einem in V1 noch nicht unterstützten Intent) ein fester Text, siehe unten. |
| `sources` | `list[Source]` | `[]` ausser bei einem erfolgreichen `knowledge`-Turn mit mindestens einem gefundenen Chunk. |
| `trace` | `ChatTrace` | Immer vollständig gefüllt, für jeden Intent. |

`Source` (ein tatsächlich an das LLM übergebener Chunk — nicht jeder von Weave-Retrieval zurückgegebene Kandidat, falls die Pipeline in einer späteren Version selektiv filtert):

| Feld | Typ | Bedeutung |
|---|---|---|
| `source` | `string` | Herkunftssystem (z. B. `confluence`); `""` falls Weave-Retrieval keins gemeldet hat. |
| `original_filename` | `string \| null` | |
| `page_start` | `int \| null` | |
| `page_end` | `int \| null` | |
| `document_version` | `int \| null` | |
| `document_id` | `string` | |
| `chunk_id` | `int` | |
| `score` | `float \| null` | `rerank`-Score falls vorhanden, sonst `rrf` — siehe `backend/app/services/chat.py:_score_for`. |

`ChatTrace`:

| Feld | Typ | Bedeutung |
|---|---|---|
| `intent` | `string` | Eines von `conversational`, `knowledge`, `document`, `action`, `complex` (`backend/app/services/router.py`). |
| `confidence` | `float` | Router-Konfidenz für `intent` (0.6–0.9 im RULES-Modus, frei im LLM-Modus). |
| `needs_retrieval` | `bool` | Router-Flag — **nicht** identisch damit, ob tatsächlich retrieved wurde (siehe unten: zusätzlich `bot.retrieval.enabled` nötig). |
| `needs_tool` | `bool` | Router-Flag; in V1 folgenlos (kein Tool-Aufruf implementiert). |
| `retrieval` | `RetrievalTrace \| null` | `null` ausser bei einem tatsächlich ausgeführten Retrieval-Aufruf (`knowledge`, oder `complex` mit `bot.retrieval.enabled`) — **bewusste Ausnahme:** bei den in V1 nicht unterstützten Intents `document`/`action`/`complex` ist dieses Feld immer `null`, selbst wenn `needs_retrieval=true` wäre, weil gar kein Retrieval-Aufruf gemacht wird (siehe `backend/app/services/chat.py`, Modul-Docstring). |
| `model` | `string \| null` | Vom LLM-Provider tatsächlich verwendetes Modell; `null`, wenn kein LLM-Aufruf stattfand (Guard ausgelöst, oder V1-nicht-unterstützter Intent). |
| `router_mode` | `string` | `"rules"` oder `"llm"` — Wert von `ROUTER_MODE` zum Zeitpunkt dieses Requests. |
| `timings_ms` | `dict[string, float]` | Mindestens `router_ms`, `total_ms`; zusätzlich `retrieval_ms`/`llm_ms`, wenn der jeweilige Schritt lief, oder `n8n_ms` anstelle von `llm_ms` für einen an n8n delegierten Turn (siehe `contracts/n8n-flow.md`) — `llm_ms` und `n8n_ms` schliessen sich für ein und denselben Turn gegenseitig aus. |
| `guard` | `GuardTrace \| null` | `null` ausser wenn der Guard eine Antwort ersetzt hat (siehe unten). |

`RetrievalTrace`: `{candidates: int, used: int, collections: list[string], requested_collections: list[string] | null}` — `used` ≤ `candidates`; in dieser Version stets `used == candidates` (jeder von Weave-Retrieval gelieferte Chunk wird verwendet). `collections` sind die Collection-Slugs, die für diesen Aufruf tatsächlich als `SearchRequest.allowed_collections` an Weave-Retrieval geschickt wurden (`backend/app/services/chat.py:resolve_collection_scope`) — nie `null`, und `[]` genau dann, wenn `guard.reason` `"no_collections"` ODER `"filter_excluded_all"` ist (dann `candidates`/`used` ebenfalls beide `0`, da gar kein Such-Aufruf gemacht wurde). Kann zusätzlich den Wert `"__none__"` enthalten (`backend/app/services/chat.py`s `NO_COLLECTION_SENTINEL`, spiegelt Weave-Retrievals gleichnamigen Sentinel aus `app/services/search.py` in jenem Service byte-für-byte) — nie ein echter Collection-Slug, sondern der sichtbare Beleg dafür, dass Dokumente ohne Collection (`Document.collection_slug IS NULL`, Altbestand von vor der Collections-Einführung) für diesen Aufruf mit einbezogen wurden, siehe Collections-Vertrag unten. `requested_collections` ist `ChatRequest.collections` unverändert gespiegelt — was der Aufrufer angefragt hat, im Unterschied zu `collections` (was effektiv gesucht wurde); `null` genau dann, wenn kein Filter gesendet wurde.

`GuardTrace`: `{triggered: bool, reason: string | null}` — drei mögliche Werte für `reason`:
- `"no_context"`: Retrieval lief, hat aber nichts Verwertbares gefunden (ausgelöst durch `bot.guard.require_sources` bei leerem Retrieval-Ergebnis). Antwort: `bot.guard.no_context_reply`.
- `"no_collections"`: `resolve_collection_scope` hat — bereits VOR Anwendung eines Anfrage-Filters — keine Collection gefunden, die der Aufrufer lesen darf UND die dieser Bot nutzen darf — Retrieval läuft in diesem Fall gar nicht erst (siehe `RetrievalTrace.collections` oben). Anders als bei `"no_context"` greift dieser Fall unabhängig von `bot.guard.require_sources`: ein collection-gebundener Bot darf nie ohne eine konkrete Collection-Grenze suchen. Antwort: ebenfalls `bot.guard.no_context_reply`.
- `"filter_excluded_all"`: Aufrufer/Bot HATTEN eine nicht-leere Rechte-Schnittmenge (der `"no_collections"`-Fall oben trat NICHT ein) — aber `ChatRequest.collections` (siehe oben) nannte ausschliesslich Collections ausserhalb dieser Schnittmenge, sodass der EIGENE Filter des Aufrufers das Ergebnis geleert hat, nicht fehlende Leserechte. Ein bewusst eigener, von `"no_collections"` unterscheidbarer Grund mit einer EIGENEN, festen Antwort (nicht `bot.guard.no_context_reply`) — "deine Auswahl passt nicht" ist eine andere, für den Aufrufer behebbare Situation als "du darfst nichts lesen".

### Verhalten je Intent

| Intent | Retrieval? | LLM-Aufruf? | `answer` | `sources` |
|---|---|---|---|---|
| `conversational` | nein | ja (`system_prompt` + `history` + `message`) | LLM-Antwort | `[]` |
| `knowledge` (oder `complex` mit `bot.retrieval.enabled`) | ja, falls `bot.retrieval.enabled` UND nach Anwendung von `ChatRequest.collections` (falls gesetzt) mindestens eine für den Aufrufer lesbare, vom Bot erlaubte Collection übrig bleibt | ja, ausser Guard greift | LLM-Antwort mit Kontext, **oder** ein fester Guard-Text falls (a) keine gemeinsame Collection existiert (`bot.guard.no_context_reply`, `guard.reason="no_collections"`, unabhängig von `require_sources`), (b) `require_sources=true` und 0 Chunks gefunden (`bot.guard.no_context_reply`, `guard.reason="no_context"`), oder (c) eine gemeinsame Collection zwar existiert, aber `ChatRequest.collections` sie vollständig ausfiltert (eigener, fester Text — NICHT `bot.guard.no_context_reply` —, `guard.reason="filter_excluded_all"`) | tatsächlich übergebene Chunks (`[]` bei Guard) |
| `knowledge`, aber `bot.retrieval.enabled=false` | nein (bewusst, siehe unten) | ja, wie `conversational` | LLM-Antwort ohne Kontext | `[]` |
| `document` / `action` / `complex` | nein | **nein** | fester Hinweistext ("noch nicht unterstützt, folgt in V2/V3") | `[]` |

**n8n-Bot-Provider (`bot.model.provider == 'n8n'`):** die obige Tabelle beschreibt das direkte LLM-Pipeline-Verhalten dieses Vertrags; ein Bot mit `model.provider: n8n` läuft stattdessen über einen eigenen, separaten Vertrag (siehe [`contracts/n8n-flow.md`](n8n-flow.md)) — Permissions und Collections-Scope werden GENAUSO aufgelöst wie oben, aber der Chat-Turn selbst wird an einen n8n-Agentenflow delegiert statt an ein LLM. `document`/`action`/`complex` sind für einen solchen Bot NICHT die "V1 nicht unterstützt"-Zeile oben, sondern werden (bis auf `conversational`, siehe der andere Vertrag) an n8n weitergereicht. Auf `ChatRequest`/`ChatResponse` selbst ändert das nichts — dieselben Felder, derselbe Vertrag, nur eine andere interne Erzeugung der Antwort.

Wichtig: `needs_retrieval=true` allein löst **keinen** Weave-Retrieval-Aufruf aus — zusätzlich muss `bot.retrieval.enabled` (aus der Bot-YAML) wahr sein. Grund: im `llm`-Router-Modus klassifiziert das LLM allein anhand des Nachrichtentexts, unabhängig von der Retrieval-Konfiguration DIESES Bots; ein Bot ohne Wissensanbindung darf trotzdem nie einen Retrieval-Call auslösen (siehe `backend/app/services/chat.py`, Modul-Docstring, Schritt 5).

Collections-Vertrag (siehe `backend/app/services/chat.py:resolve_collection_scope`, Modul-Docstring Schritt 5a): bevor ein tatsächlicher Retrieval-Aufruf gemacht wird, schneidet Weave-Runtime `bot.retrieval.collections` (leer = jede vom Aufrufer lesbare Collection) mit den Collections, die `user.team` laut Weave-Retrieval (`GET /api/v1/collections`) überhaupt lesen darf — diese echte Schnittmenge (`real_scope`) wird zuerst berechnet, unabhängig von allem Folgenden. Ist `real_scope` leer, wird gar nicht erst gesucht, sondern sofort `bot.guard.no_context_reply` mit `guard.reason="no_collections"` zurückgegeben, unabhängig von `require_sources` — **es sei denn**, die eine unten dokumentierte Ausnahme greift.

Altbestand (`bot.retrieval.include_uncollected`, `backend/app/schemas/bot.py`, Default `true`): Dokumente ganz ohne Collection (`Document.collection_slug IS NULL` — jeder Bestand von vor der Einführung der Collections) sind für einen Bot standardmässig weiterhin sichtbar, zusätzlich zu `real_scope` oben. Technisch dadurch umgesetzt, dass `resolve_collection_scope` bei nicht-leerem `real_scope` den Sentinel-Wert `"__none__"` (`NO_COLLECTION_SENTINEL`, spiegelt Weave-Retrievals gleichnamige Konstante in `app/services/search.py`) an die Liste anhängt, BEVOR sie als `SearchRequest.allowed_collections` verschickt wird — sichtbar im Response-Trace als zusätzlicher Eintrag in `RetrievalTrace.collections` (siehe oben). Der Default ist bewusst `true`: ohne ihn würde das Anlegen der allerersten Collection im System schlagartig den gesamten Altbestand für jeden retrieval-fähigen Bot unsichtbar machen, sobald der Bot selbst gar keine Collections-Einschränkung konfiguriert hat — ein stiller Datenverlust, den keine Bot-YAML je angefordert hat. `include_uncollected=false` schaltet auf strikte Sichtbarkeit um: nur Dokumente, die explizit einer für diesen Bot erlaubten Collection zugeordnet sind, werden gefunden.

Die eine dokumentierte Ausnahme von der `real_scope`-leer-→-Guard-Regel oben: nennt der Bot selbst **keine** Collections-Einschränkung (`bot.retrieval.collections` leer) UND darf der Aufrufer **keine** Collection lesen (`readable` leer) UND ist `include_uncollected=true`, dann liefert `resolve_collection_scope` `["__none__"]` (nur der Sentinel, keine echten Slugs) statt `[]` — eine reine Altbestands-Suche ist in diesem Fall zulässig, da für diesen Aufruf gar keine echte Collections-Grenze existiert, die verletzt werden könnte. Ein Bot mit eigener Collections-Einschränkung ist von dieser Ausnahme nie erfasst: dort löst eine leere echte Schnittmenge immer den `no_collections`-Guard aus, unabhängig von `include_uncollected`.

**Collection-Filter pro Anfrage (`ChatRequest.collections`):** alles oben (`real_scope`, die Altbestand-Ausnahme, der Sentinel) beschreibt ausschliesslich, was dieser Aufrufer/Bot laut Rechte-Lage überhaupt lesen DARF — davon vollständig unabhängig berechnet. Erst NACHDEM dieser Rechte-Umfang vollständig feststeht, wird er — falls `ChatRequest.collections` nicht `null` ist — ein zweites Mal geschnitten, diesmal mit dem, was der Aufrufer für DIESE eine Anfrage tatsächlich angefragt hat (effektiver Umfang = `(bot.retrieval.collections ∩ readable) ∩ collections`, inklusive Sentinel-Anteil auf beiden Seiten). Diese Reihenfolge ist strikt: der Filter wirkt nie vor der Rechte-Schnittmenge und fügt ihr nie etwas hinzu — ein im Filter genannter Slug, der ausserhalb des Rechte-Umfangs liegt, wird still verworfen, exakt wie ein von `bot.retrieval.collections` genannter, aber für den Aufrufer nicht lesbarer Slug schon immer verworfen wurde. `null`/fehlendes `collections` verhält sich exakt wie vor Einführung dieses Felds (Regressionsgarantie); `[]` ist ein eigenständiger, gültiger "matcht nichts"-Filter.

Sentinel-Sonderfall des Filters: nennt `ChatRequest.collections` ausschliesslich echte Slugs, fällt `"__none__"` aus dem Ergebnis heraus, selbst wenn `include_uncollected=true` den Sentinel dem Rechte-Umfang hinzugefügt hätte — der Aufrufer wollte erkennbar genau diese Collections, nicht zusätzlich Altbestand. Nennt der Filter `"__none__"` explizit, bleibt er nur erhalten, wenn er bereits Teil des Rechte-Umfangs war (der Filter kann Altbestands-Zugriff genauso wenig verleihen wie jede andere Collection, die die Rechte-Schnittmenge nicht bereits gewährt hat).

Wird der resolvierte Umfang **erst durch diesen Filter** leer — der Rechte-Umfang selbst war nicht leer —, ist das ein eigener, von `"no_collections"` unterscheidbarer Guard-Grund: `guard.reason="filter_excluded_all"` (siehe `GuardTrace` oben). War der Rechte-Umfang bereits ohne jeden Filter leer, bleibt es bei `"no_collections"`, unabhängig davon, was `ChatRequest.collections` enthielt. `RetrievalTrace` weist in jedem Fall beide Seiten aus: `requested_collections` (die rohe Anfrage) und `collections` (was effektiv gesucht wurde bzw. `[]` bei einem der beiden Guard-Fälle).

Für den `n8n`-Bot-Provider (siehe unten) gilt dieselbe Filter-Anwendung identisch: der gefilterte, nie erweiterte Umfang ist exakt das, was in das an den Agentenflow ausgehändigte Delegations-Token einfliesst (`contracts/n8n-flow.md`) — ein Filter gibt einem Agentenflow also ebenfalls nur weniger, nie mehr, als dessen ungefilterte Rechte-Lage ohnehin erlaubt hätte. Anders als beim direkten LLM-Pfad löst ein durch den Filter geleerter Umfang für einen `n8n`-Bot aber KEINEN Guard aus (siehe `contracts/n8n-flow.md` für die eigene Begründung, warum ein leerer Scope dort grundsätzlich nie selbst guardet).

## Fehlerbilder

| Status | Bedingung | `detail` |
|---|---|---|
| `401` | `Authorization`-Header fehlt, ist nicht `Bearer ...`, oder das Token stimmt nicht (`require_service_token`). | `"missing or malformed bearer token"` bzw. `"invalid service token"`. |
| `403` | `bot.permissions.teams` ist nicht leer und `user.team` ist nicht darin enthalten (`BotPermissionDenied`). Eine leere `permissions.teams`-Liste bedeutet "jedes Team darf" — dann tritt dieser Fall nie ein. | Freitext mit Bot-ID und erlaubten Teams. |
| `404` | `bot_id` existiert nicht unter `BOTS_DIR` (`BotNotFoundError`). | `"unknown bot_id: '<bot_id>'"`. |
| `503` | Weave-Retrieval war während eines nötigen Retrieval-Aufrufs nicht erreichbar, hat einen `5xx` geliefert, oder eine unparsebare `200`-Antwort (`RetrievalUnavailable`, `backend/app/services/retrieval_client.py`). **Nie** eine stille, unbelegte Antwort für einen `require_sources`-Bot — das ist genau der Fall, den der Response-Guard verhindern soll. | Freitext von `retrieval_client.py` (nennt die angefragte URL). |
| `503` | Analog, für einen `n8n`-Bot (siehe `contracts/n8n-flow.md`): der Webhook war nicht erreichbar, hat getimeout, oder einen `5xx` geliefert (`N8nUnavailable`, `backend/app/services/n8n_client.py`). | Freitext von `n8n_client.py` (nennt die Webhook-URL, nie das Delegations-Token). |
| `422` | Kein Teil dieses Vertrags im engeren Sinn — Standard-FastAPI-Validierungsfehler bei einem strukturell falschen Body (z. B. leeres `message`). | FastAPI-Standardschema. |
| `500` | Bewusst NICHT abgefangen: eine `RetrievalError` (4xx von Weave-Retrieval, z. B. `final_k > top_k` — kann nur aus einer fehlkonfigurierten Bot-YAML stammen, da `top_k`/`final_k` aus `BotConfig` kommen) oder ein `LLMError` eines echten LLM-Providers (in dieser Stufe nur mit dem nie fehlschlagenden `FakeLLM` getestet). Beides sind Deployment-/Konfigurationsfehler, kein Teil des regulären Fehlerbilds. Ebenso ein `N8nError` (4xx vom n8n-Webhook oder eine unparsebare `200`-Antwort, `backend/app/services/n8n_client.py`) oder ein `DelegationConfigError` (`WEAVE_DELEGATION_SECRET` nicht konfiguriert, `backend/app/services/delegation.py`) für einen `n8n`-Bot — siehe `contracts/n8n-flow.md`. | FastAPI-Standard-500 (kein strukturiertes `detail` garantiert). |

**Für Weave-API als Konsument:** `runtime_client._raise_for_status` klassifiziert jeden `>=500`-Status (also auch dieses `503`) als `RuntimeUnavailable`, was `POST /v1/chat` bzw. `POST /v1/chat/completions` wiederum als `502` an ihren eigenen Aufrufer weiterreichen. `401`/`403`/`404` sind `<500` und werden als `RuntimeRejected` 1:1 (Status **und** `detail`) durchgereicht.

## Streaming: `POST /internal/chat/stream`

Die streamende Variante desselben Chat-Turns — identischer `ChatRequest`-Body, identische Auth (siehe oben), aber `text/event-stream` statt einer einzelnen JSON-Antwort: `backend/app/api/internal.py`s `chat_stream()`-Route liefert ein SSE-Ereignis pro Pipeline-Schritt (`'data: <json>\n\n'`, ein `data:`-Feld pro Zeile, keine weiteren SSE-Felder wie `event:`/`id:` — jedes Ereignis trägt seine Art bereits selbst in seinem eigenen `type`-Feld). Erzeugt von `backend/app/services/chat.py`s `handle_chat_stream()`; die Pipeline selbst (Bot laden, Permissions, Routing, Retrieval, Collections-Scope, Response-Guard) ist exakt dieselbe wie bei `POST /internal/chat` oben — `handle_chat` und `handle_chat_stream` teilen sich diese komplette Vorbereitung über eine gemeinsame interne Funktion (`_prepare_turn`), nur der letzte Schritt (der eigentliche LLM-Aufruf) unterscheidet sich technisch (blockierend vs. inkrementell). Für ein und denselben Request ist die im Stream übertragene Antwort — alle `delta.text` aneinandergehängt, in Reihenfolge — byte-identisch mit dem `answer`-Feld, das `POST /internal/chat` für denselben Request geliefert hätte; ebenso für `sources`.

### Ereignistypen

| `type` | Wann | Payload |
|---|---|---|
| `trace` | Genau einmal, als ALLERERSTES Ereignis — sobald Routing, Permissions, Collection-Scope-Auflösung und Response-Guard vollständig abgeschlossen sind, also **bevor** das erste `delta`-Ereignis rausgeht. | `{trace: ChatTrace}` — dieselbe Form wie `ChatResponse.trace` oben, mit einer bewussten Abweichung: `timings_ms` enthält hier nur, was zu DIESEM Zeitpunkt schon feststeht (`router_ms`, plus `retrieval_ms` falls ein Knowledge-Turn tatsächlich lief) — `llm_ms`/`total_ms` fehlen hier immer, weil beide erst nach Abschluss der Generierung feststehen und das `trace`-Ereignis nicht bis dahin verzögert werden darf (siehe Reihenfolge-Garantie unten). `model` ist das für diesen Turn angefragte Modell (`bot.model.model`), nicht notwendigerweise das vom Provider am Ende tatsächlich gemeldete — bei einem Guard-Treffer oder einem V1-nicht-unterstützten Intent bleibt `model` wie gewohnt `null`, da gar kein LLM-Aufruf stattfindet. |
| `delta` | Null- bis mehrfach, nach `trace` und vor `sources`. | `{text: string}` — ein Fragment des Antworttexts; Konkatenation aller `text`-Werte in Empfangsreihenfolge ergibt exakt denselben String, den `ChatResponse.answer` für einen identischen, nicht-streamenden Request geliefert hätte. Auch der feste Guard-Text (`bot.guard.no_context_reply`) und der V1-Platzhaltertext werden auf diesem Weg — als normale `delta`-Ereignisse — ausgeliefert, nicht als ein einzelnes Sonderereignis: die Quellenpflicht des Response-Guards wird durch Streaming nicht aufgeweicht, sie bestimmt lediglich, WELCHER Text gestreamt wird, nicht WIE. |
| `sources` | Genau einmal, unmittelbar nach dem letzten `delta`. | `{sources: Source[]}` — dieselbe Liste, die `ChatResponse.sources` für einen identischen Request getragen hätte (`[]` bei Guard-Treffer oder V1-nicht-unterstütztem Intent, genau wie dort). |
| `done` | Genau einmal, als letztes Ereignis eines fehlerfrei abgeschlossenen Streams. | Keine Payload (`{type: "done"}`). |
| `error` | Höchstens einmal, ANSTELLE von `sources`+`done` — siehe Fehlerverhalten unten. | `{detail: string}`. |

### Reihenfolge-Garantie

`trace` → (`delta`)\* → `sources` → `done`, strikt in dieser Reihenfolge — **oder** `trace` → (`delta`)\* → `error`, wenn die Generierung mittendrin fehlschlägt (siehe unten). Nach `done` oder `error` folgt nie ein weiteres Ereignis auf demselben Stream. Es gibt kein Ereignis vor `trace`.

### Fehlerverhalten — bewusst zweigeteilt

**Vor dem Stream** (`_prepare_turn` — Bot laden, Permissions, Routing, Retrieval, Collection-Scope, Guard-Auswertung — schlägt fehl, BEVOR auch nur ein Byte der Antwort geschrieben wurde): identisches Verhalten wie `POST /internal/chat` oben — ein normaler HTTP-Status (`401`/`403`/`404`/`503`/`422`/`500`, siehe die Fehlerbild-Tabelle oben) mit strukturiertem `detail`, **keine** SSE-Antwort, kein `Content-Type: text/event-stream`. `handle_chat_stream()` ist absichtlich KEINE Generator-Funktion (siehe deren eigenes Docstring) — sie führt die gesamte Vorbereitung synchron aus und gibt erst danach den eigentlichen (noch nicht gestarteten) Ereignis-Generator zurück, damit `backend/app/api/internal.py` diese drei Ausnahmen exakt wie bei der nicht-streamenden Route abfangen kann, bevor die `StreamingResponse` — und damit deren `200 OK` — überhaupt existiert.

**Mitten im Stream** (ein echter LLM-Provider wirft `LLMError` während der Generierung — mit `FakeLLM` in dieser Stufe nie beobachtbar, siehe die `500`-Zeile der Fehlerbild-Tabelle oben für dieselbe Einschränkung bei `POST /internal/chat`): Die Antwort hat zu diesem Zeitpunkt bereits `200 OK` und `Content-Type: text/event-stream` committed — der HTTP-Status kann nicht mehr rückwirkend geändert werden. Der Fehler wird stattdessen als einzelnes, abschließendes `error`-Ereignis in den Stream geschrieben (`{type: "error", detail: "<Freitext>"}`); danach folgt garantiert **kein** `sources`- oder `done`-Ereignis mehr, und die Verbindung endet. **Für Weave-API als Konsument bedeutet das:** ein `200`-Status allein beweist NICHT, dass der Turn vollständig gelungen ist — ein Konsument dieses Streams muss jedes Ereignis bis `done` (Erfolg) oder `error` (Fehlschlag mittendrin) auswerten, darf sich für diese Unterscheidung nicht auf den HTTP-Status verlassen.

## Versionierungsregel

- **Additiv (kompatibel):** ein neues optionales Response-Feld, ein neuer möglicher `intent`-Wert, eine neue `timings_ms`-Kennzahl. Weave-API darf unbekannte Felder ignorieren (sein eigenes `ChatResponse`/`schemas/chat.py` ist bereits lose typisiert — `sources`/`trace` sind generische `list | dict | None`).
- **Breaking (erfordert Koordination):** ein Pflichtfeld entfernen/umbenennen, einen bestehenden Feldtyp ändern, ein bestehendes Fehlerbild auf einen anderen Status ändern, oder `ChatMessage.role`/`intent` um einen Wert erweitern, den Weave-API aktiv anders behandeln müsste. Ablauf: Vertrag-Version hier erhöhen → Änderung in Weave-Runtime UND die entsprechende Anpassung in Weave-APIs `runtime_client.py`/`openai_compat.py` im selben Koordinationsfenster → erst dann deployen.
- Eine Erweiterung von `BotConfig` (neue Bot-YAML-Felder) ist für sich genommen nie Teil dieses Vertrags, solange sie `ChatRequest`/`ChatResponse` nicht verändert — sie ist rein internes Weave-Runtime-Verhalten.

## Änderungsprotokoll

- **v1 (2026-08-31):** Initialer Vertrag, extrahiert aus der ersten vollständigen Implementierung der Pipeline (`backend/app/services/chat.py`, `backend/app/api/internal.py`, `backend/app/schemas/chat.py`, `backend/app/schemas/bot.py`). Ersetzt den vorherigen `501`-Platzhalter.
- **v1, additiv (2026-08-31):** Collections-Unterstützung. Neues optionales `RetrievalTrace.collections`-Feld und ein zweiter möglicher `GuardTrace.reason`-Wert (`"no_collections"`) — beides additiv nach der Versionierungsregel oben, keine Vertrag-Versionserhöhung nötig. `ChatRequest`/`ChatResponse`s übrige Felder sind unverändert; die eigentliche Collections-Zugriffslogik (`bot.retrieval.collections`, `resolve_collection_scope`, `GET /api/v1/collections`) ist rein internes Weave-Runtime-/Weave-Retrieval-Verhalten, siehe oben.
- **v1, additiv (2026-08-31):** Fix eines Vertragsbruchs mit echter Datenverlust-Wirkung: `resolve_collection_scope` gab bis dahin nie den Sentinel `"__none__"` zurück, wodurch jedes Dokument ohne Collection (`Document.collection_slug IS NULL`, Altbestand von vor der Collections-Einführung) für jeden retrieval-fähigen Bot unsichtbar wurde, sobald irgendeine Collection im System existierte. Neues Bot-YAML-Feld `retrieval.include_uncollected` (`backend/app/schemas/bot.py`, Default `true`) steuert das jetzt explizit; `RetrievalTrace.collections` kann nun zusätzlich den Wert `"__none__"` enthalten (siehe oben). Additiv: keine bestehenden Felder/Typen geändert, nur ein neuer möglicher Wert innerhalb eines bestehenden `list[string]`-Felds und ein rein internes, optionales Bot-YAML-Feld — keine Vertrag-Versionserhöhung nötig.
- **v1, additiv (2026-08-31):** Neuer Endpoint `POST /internal/chat/stream` (siehe "Streaming" oben) — dieselbe Pipeline, dieselbe Auth, derselbe `ChatRequest`-Body wie `POST /internal/chat`, aber als `text/event-stream` statt einer einzelnen JSON-Antwort ausgeliefert. Additiv: `POST /internal/chat` selbst ist byte-für-byte unverändert (siehe `backend/app/services/chat.py`s `handle_chat`, das jetzt auf derselben `_prepare_turn`-Vorbereitung wie `handle_chat_stream` aufbaut, aber sein eigenes Verhalten dabei nicht ändert), und ein Konsument, der den neuen Endpoint nie aufruft, ist von dieser Änderung überhaupt nicht betroffen — keine Vertrag-Versionserhöhung nötig.
- **v1, additiv (2026-08-31):** n8n als Bot-Provider (`model.provider: 'n8n'`, siehe neuer [`contracts/n8n-flow.md`](n8n-flow.md)). Neues optionales `ChatUser.username`-Feld und ein neuer möglicher `timings_ms`-Schlüssel (`n8n_ms`, exklusiv zu `llm_ms`) sowie ein neues `503`-Fehlerbild (`N8nUnavailable`) — alle additiv nach der Versionierungsregel oben. `ChatRequest`/`ChatResponse`s übrige Felder und jedes bestehende Fehlerbild sind unverändert; ein Bot ohne `model.provider: 'n8n'` ist von dieser Änderung überhaupt nicht betroffen — keine Vertrag-Versionserhöhung nötig.
- **v1, additiv (2026-09-01):** Collection-Filter pro Anfrage. Neues optionales `ChatRequest.collections`-Feld (`list[string] | null`, Default `null` = kein Filter, siehe oben) — eine reine Einschränkung, angewendet strikt NACH der bestehenden Rechte-Schnittmenge (`bot.retrieval.collections ∩ vom Aufrufer lesbare Collections`), niemals eine Rechtevergabe: ein darin genannter, ausserhalb dieser Schnittmenge liegender Slug wird still verworfen. Neues optionales `RetrievalTrace.requested_collections`-Feld (spiegelt `ChatRequest.collections` unverändert, im Unterschied zum bestehenden `collections`-Feld) und ein dritter möglicher `GuardTrace.reason`-Wert (`"filter_excluded_all"`, mit eigenem festen Antworttext statt `bot.guard.no_context_reply` — siehe oben) für den Fall, dass eine an sich nicht-leere Rechte-Schnittmenge erst durch diesen Filter geleert wird. Gilt identisch für den `n8n`-Bot-Provider: derselbe gefilterte Umfang fliesst in dessen Delegations-Token ein (siehe `contracts/n8n-flow.md`), ohne dort selbst einen Guard auszulösen (unverändert gegenüber dem bisherigen Verhalten bei leerem Scope). Alles additiv nach der Versionierungsregel oben: kein bestehendes Feld/Typ geändert, jedes bestehende Fehlerbild unverändert, und ein Aufrufer, der `collections` nie sendet, ist von dieser Änderung überhaupt nicht betroffen (Regressionstests bestätigen das) — keine Vertrag-Versionserhöhung nötig.
