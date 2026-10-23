# ADR 0009: Wissensgedächtnis – verdichtete Zusammenfassungen pro Dokument und Wissensbereich

**Status:** angenommen

**Datum:** 2026-10-18

## Kontext

Bots beantworten Fragen heute ausschließlich über die Chunk-Suche (Weave-Retrieval, hybride Vektor- und Volltextsuche). Für Detailfragen passt das, für Überblicksfragen nicht:

- „Was weißt du?“ wird nach `router.py` als `conversational` geroutet und nur aus `_knowledge_overview_block` beantwortet, also mit Name und Beschreibung der lesbaren Wissensbereiche. Was *in* den Bereichen steht, weiß der Bot nicht.
- „Was ist in dem Wissensbereich X?“, „Welche Dokumente gibt es zu Y?“ und „Worum geht es in Dokument Z?“ laufen als `knowledge` in die Chunk-Suche. Die findet einzelne Textstellen, aber keinen Überblick über einen Bereich oder ein ganzes Dokument. Oft greift deshalb der Guard („keine Quellen, keine Antwort“), oder die Antwort beruht auf zufälligen Ausschnitten.

Gebraucht wird ein verdichtetes Wissen, das automatisch entsteht und sich **nach jeder Änderung am Wissen** selbst aktualisiert. Die Berechtigungen müssen dabei exakt denen der Quelldokumente bzw. Wissensbereiche entsprechen: Sie sind Teil der Abfrage und kein nachträglicher Filter (ADR-0005, ADR-0008).

## Entscheidung

### 1. Inhalt

- **Dokument-Zusammenfassung** (eine pro indiziertem Dokument in einem Wissensbereich): Titel, Dokumenttyp (z. B. Vertrag, Richtlinie, Anleitung, Protokoll, E-Mail), Kurzfassung (≤ 600 Zeichen) und bis zu 7 Kernpunkte.
- **Bereichs-Übersicht** (eine pro Wissensbereich): Kurzfassung (≤ 1200 Zeichen), bis zu 10 Hauptthemen und die Liste der enthaltenen Dokumente (Titel + Typ) mit Anzahl.

Dokumente ohne Wissensbereich (Altbestand, `collection_slug IS NULL`) erhalten in v1 kein Gedächtnis.

### 2. Ort: Weave-Knowledge, nach der Indizierung

Knowledge ist der einzige Schreiber des Chunk-Stores (ADR-0005) und kennt als Einziger `markdown_body`. Der Aufbau läuft dort als eigener Celery-Task `weave.knowledge.build_memory(collection_slug)` in der bestehenden Knowledge-Queue (ADR-0001) und nicht innerhalb von `index_document`. Eine langsame oder fehlerhafte LLM-Antwort darf die Indizierung nie verzögern oder scheitern lassen.

### 3. Speicherort: neue Tabelle `knowledge_memory` (additiv zum Chunk-Store-Vertrag)

| Spalte | Bedeutung |
|---|---|
| `id` | PK |
| `kind` | `document` \| `collection` |
| `collection_slug` | Wissensbereich, bei beiden Arten gesetzt |
| `document_id` | FK `documents.id` **ON DELETE CASCADE**, nur bei `document`; unique |
| `title`, `document_type`, `summary`, `key_points` (json), `topics` (json) | Inhalt |
| `source_document_ids` (json) | nur `collection`: die Dokumente, aus denen die Übersicht gebaut wurde |
| `source_sha256` | `document`: `_weave_markdown_sha256` des Snapshots; `collection`: Hash über Dokument-IDs und deren `source_sha256` |
| `generator`, `model` | `llm` oder `extractive`, verwendetes Modell |
| `built_at`, `dirty_since`, `building_since` | Zeitstempel für Aufbau und Entprellung |

Unique `(kind, collection_slug)` für `collection`. Keine eigenen Embeddings: Die Suche nach Dokumenten nutzt die bestehende Chunk-Suche (siehe 6). Eine neue Tabelle ist laut Versionierungsregel in `contracts/chunk-store.md` additiv. Retrieval liest sie über dieselbe Rolle `weave_retrieval_ro` (`ALTER DEFAULT PRIVILEGES … GRANT SELECT` deckt neue Tabellen bereits ab; sonst ein einmaliges `GRANT SELECT ON knowledge_memory`).

### 4. Berechtigungen: identisch, weil dieselbe Abfrage

- **Dokument-Zusammenfassungen** liest Retrieval nur per `JOIN documents` über **dieselbe Funktion `apply_filters()`** wie die Chunk-Suche: `status = indexed`, `collection_slug IN allowed_collections` usw. Ein zurückgezogenes Dokument verliert seine Zusammenfassung sofort per Cascade, ein abgelöstes (`superseded`) oder gesperrtes fällt sofort aus dem Filter.
- **Bereichs-Übersichten** liest Retrieval nur mit `collection_slug IN allowed_collections`. Das ist dasselbe Prädikat, das in `apply_filters()` für Dokumente innerhalb eines Bereichs gilt; laut ADR-0008 entscheidet dort ausschließlich die Bereichsfreigabe. Eine Übersicht enthält nur Inhalte aus Dokumenten genau dieses einen Bereichs und zeigt einem Leser daher nichts, was er nicht ohnehin lesen darf.
- **Lese-Frische-Prüfung:** Retrieval gibt den Übersichtstext nur aus, wenn alle `source_document_ids` noch `indexed` und im selben Bereich sind. Andernfalls (Zurückziehen, Ablösen, Sperren seit dem letzten Aufbau) liefert es nur die live ermittelte Dokumentliste ohne LLM-Text. Ein entfernter Inhalt kann so auch nicht für die Minuten bis zum Neuaufbau über die Übersicht weiterleben (fail closed).
- Der Gedächtnis-Endpunkt verlangt eine konkrete `allowed_collections`-Liste. Den Wert `None` („alles“) lehnt er mit 422 ab.
- Entzogene Rechte wirken sofort: Gespeichert wird keine Berechtigung, Runtime löst `allowed_collections` pro Turn frisch auf (wie bei der Suche).

### 5. Neuaufbau nach jeder Änderung, entprellt pro Wissensbereich

Jede Änderung setzt bei der Übersicht des Bereichs `dirty_since` (sofern noch nicht gesetzt):

| Änderung | Auslöser |
|---|---|
| Neues Dokument, neue Version, „Neu indizieren“ | Ende von `_run_pipeline` nach erfolgreichem Commit |
| Dokument zurückgezogen (`document.withdrawn`) | Widerrufspfad in `app/api/events.py` (die Zusammenfassung fällt per Cascade weg) |
| Wissensbereich umbenannt/neu beschrieben (`collection.updated` oder Tick) | Registry-Sync, wenn sich Name oder Beschreibung geändert hat |
| Wissensbereich gelöscht | Registry-Sync entfernt dessen Gedächtniszeilen |

Gestartet wird ausschließlich der bestehende `collection_sync_tick` (alle 60 s). Er reiht `build_memory` für jeden Bereich ein, dessen `dirty_since` älter als `MEMORY_DEBOUNCE_SECONDS` (Standard 120 s) ist und der nicht gerade gebaut wird. Ein Import von 300 Dateien löst damit einen Lauf aus und nicht 300. Kein Celery Beat, kein Redis-Zustand: Der Zustand liegt in der Datenbank und übersteht Neustarts. Als Sicherheitsnetz markiert derselbe Tick Bereiche, in denen ein indiziertes Dokument keine aktuelle Zusammenfassung hat. Das deckt verlorene Auslöser ab und füllt beim Einschalten des Features den Bestand nach.

`build_memory(slug)`: `dirty_since` leeren und `building_since` setzen. Dann Zusammenfassungen nur für Dokumente ohne oder mit veralteter Zusammenfassung erzeugen (`source_sha256` ≠ aktueller Snapshot); unveränderte Dokumente kosten kein LLM-Budget. Pro Lauf höchstens `MEMORY_MAX_DOCUMENTS_PER_RUN` (Standard 25), danach wird der Bereich wieder als `dirty` markiert und im nächsten Tick fortgesetzt. Zum Schluss die Übersicht neu bauen. Was während eines Laufs passiert, markiert den Bereich erneut. Bleibt ein Lauf hängen, wird `building_since` ignoriert, sobald es älter ist als ein voller Lauf dauern darf: (`MEMORY_MAX_DOCUMENTS_PER_RUN` + 1) × `MEMORY_LLM_TIMEOUT_SECONDS` plus fünf Minuten, mit den Standardwerten 57 Minuten. Ein langsames Modell lässt so nie zwei Läufe desselben Bereichs parallel laufen.

`touch()` prüft die Betriebsart nur, wenn es die Bereichszeile erst anlegen müsste: Gibt es sie schon, wird sie in jedem Prozess markiert, auch im API-Prozess, dessen Umgebung noch `off` sagt, während der Worker mit der zentral gespeicherten Betriebsart baut. Beim Nachfüllen zählen nur Bereiche, die in der Registry-Kopie stehen; ein gelöschter Bereich wird nicht wiederbelebt.

### 6. LLM und Rückfall auf extraktiv

Knowledge liest die zentrale Chat-Provider-Konfiguration nach ADR-0007 pro Lauf frisch über `GET /api/v1/internal/chat-provider`, mit demselben `CHAT_CONFIG_SERVICE_TOKEN`, das es schon für `/internal/retrieval-provider` verwendet. Kein neues Secret, kein zweites Modell.

Die Betriebsart steuert eine Einstellung `KNOWLEDGE_MEMORY_MODE`:

- `off` (**Standard**): Es wird nichts gebaut, Runtime verhält sich exakt wie heute.
- `extractive`: ohne LLM-Budget. Titel = erste Überschrift oder Dateiname, Typ aus dem Frontmatter, Kurzfassung = erster Absatz, Kernpunkte = Überschriften. Die Übersicht besteht aus Beschreibung, Dokumentliste und häufigsten Überschriften bzw. Tags.
- `llm`: Das zentrale Modell erzeugt strikt JSON (Eingabe gekürzt auf `MEMORY_MAX_INPUT_CHARS`, Standard 12 000). **Fällt das LLM aus, liefert es kein gültiges JSON oder ist es nur ein Platzhalter** (keine zentrale Konfiguration, `enabled=false`, Fake-Provider), wird für genau diesen Eintrag extraktiv gebaut, mit `generator = extractive`. Dieser Eintrag wird beim nächsten Lauf erneut mit dem LLM versucht. Ein Fehler beim Gedächtnis ändert nie den Status eines Dokuments.

Dokumentinhalte sind beim Zusammenfassen **Daten, keine Anweisungen** (abgegrenzter Block im Prompt). Auch die erzeugten Zusammenfassungen behandelt Runtime später als Daten.

### 7. Nutzung in Weave-Runtime

- `route()` erhält `memory: bool`. Ist das Gedächtnis an, ergeben Überblicksfragen den neuen Intent **`overview`**: die bisherigen „Was weißt du?“-Muster sowie „Was ist in (dem|im) Wissensbereich …“, „Welche Dokumente gibt es zu …“ und „Worum geht es in …“. Detailfragen („Was steht im Dokument zur Kündigungsfrist?“, „Welche Dokumente sind für den Antrag nötig?“, „Worum geht es in Abschnitt 3?“) bleiben `knowledge`, weil eine Zusammenfassung sie nicht beantworten kann. Für den LLM-Router kommt `overview` in die Intent-Liste. Ist es aus, routet alles exakt wie heute. Ein neuer `intent`-Wert ist laut `contracts/internal-chat.md` additiv. n8n-Bots nutzen das Gedächtnis nicht; ihr Turn läuft im Flow.
- Ein `overview`-Turn löst den Scope genauso auf wie ein `knowledge`-Turn (Nutzerrechte ∩ Bot-Bereiche ∩ Auswahl im Chat) und ruft dann `POST /api/v1/memory` bei Retrieval auf, mit `query`, `allowed_teams`, `allowed_collections` und optional `collection`. Retrieval liefert:
  - `collections`: Übersichten der Bereiche im Scope; wird ein Bereich in der Frage namentlich genannt, nur dieser.
  - `documents`: Dokument-Zusammenfassungen. Die Reihenfolge ergibt sich aus der bestehenden hybriden Chunk-Suche (`search()`), gruppiert nach Dokument; Treffer im Dateinamen oder Titel kommen zuerst. Die Zusammenfassungen werden anschließend erneut über `apply_filters()` gelesen.
- Engt ein Bot seine Suche über `retrieval.filters` ein (z. B. `department`), die das Gedächtnis nicht abbilden kann, überspringt Runtime das Gedächtnis und sucht wie bisher. Die Bot-Auswahl darf Rechte nur einschränken, nie erweitern.
- Die Einträge kommen als nummerierte `[source N]`-Abschnitte in den Kontext, wie beim `_context_block`. Das LLM antwortet mit Quellenangaben, und der Guard bleibt unverändert: Sind beide Listen leer, läuft der Turn als normaler `knowledge`-Turn weiter (Chunk-Suche + Guard); war es eine der bisherigen „Was weißt du?“-Fragen, läuft er stattdessen wie bisher als `conversational` und antwortet aus dem Überblicksblock. Schlechter als heute wird es also nie.

### 8. Darstellung als Quelle

`Source` bekommt das optionale Feld `memory_kind: 'document' | 'collection' | null` (additiv). Dokument-Zusammenfassung: `document_id` des Dokuments, `chunk_id = 0`, Dateiname, Version, Bereich und Herkunft wie bei einem Chunk. Bereichs-Übersicht: `document_id = "collection:<slug>"`, `chunk_id = 0`, `original_filename = "Übersicht: <Bereichsname>"`, `collection = <slug>`. Weave-Chat (Quellenkarten und Quellenleiste) und die Desktop-App kennzeichnen sie mit einem Abzeichen „Zusammenfassung“ bzw. „Bereichs-Übersicht“; ältere Clients zeigen sie als normale Quellen.

### 9. Konfiguration

`weave.yaml` bekommt den geteilten Wert `KNOWLEDGE_MEMORY_MODE` (Ziele: knowledge, runtime; Standard `off`). Runtime schaltet das Gedächtnis nur bei `extractive` oder `llm` ein; jeder andere Wert gilt als `off`. Nur Knowledge kennt als Umgebungsvorgaben `MEMORY_DEBOUNCE_SECONDS`, `MEMORY_MAX_DOCUMENTS_PER_RUN`, `MEMORY_MAX_INPUT_CHARS`, `MEMORY_LLM_ENDPOINT` und `MEMORY_LLM_TIMEOUT_SECONDS` (`app/core/config.py`); sie stehen bewusst nicht in `weave.yaml`, weil die Administration unten sie überschreibt.

**Administration (nach ADR-0007):** Unter *Administration › KI › Wissensgedächtnis* stellt ein Administrator Betriebsart, LLM-Endpunkt (aus „Chat & LLM“), Wartezeit, Dokumente pro Lauf und Höchstlänge pro Dokument ein. Weave-Ingest speichert das als Singleton `knowledge_memory_config` (Migration `0051`). Knowledge liest es bei jedem Tick und Lauf über `GET /api/v1/internal/knowledge-memory` (gleiches `CHAT_CONFIG_SERVICE_TOKEN` wie `/internal/retrieval-provider`). Runtime erhält den Modus als `knowledge_memory_mode` im ohnehin pro Turn gelesenen Chat-Provider-Snapshot, also ohne zusätzlichen Aufruf. Solange niemand gespeichert hat (`managed: false`), gelten die Umgebungswerte oben.

**Kennzahlen:** Die Bereichszeile in `knowledge_memory` führt pro Lauf `last_run_at`, `last_duration_ms` und `last_error` sowie kumulativ `llm_calls` und `llm_failures`. Knowledge liefert unter `GET /api/v1/internal/memory/status` Abdeckung (zusammengefasst / indiziert, davon per LLM), Übersichten (gebaut, wartend, im Aufbau), LLM-Aufrufe und Fehlschläge, gesamt und pro Bereich. `POST /api/v1/internal/memory/rebuild` plant einen vollständigen Neuaufbau (etwa nach einem Modellwechsel). Beide sind mit demselben Header `X-Weave-Reindex-Token` geschützt wie die Reindex-Routen und werden nur über Weave-Ingest (`/api/v1/admin/knowledge-memory/status|rebuild`, nur Administratoren) aufgerufen; der Browser spricht nie direkt mit Knowledge.

### 10. Gedächtnis als Suchunterstützung

Das Gedächtnis hilft auch bei Wissensfragen, nicht nur bei Überblicksfragen: Der Bot soll vor der Chunk-Suche wissen, in welchen Dokumenten was steht. Beides unten gilt nur bei Betriebsart `extractive` oder `llm` und nur für Bots ohne `retrieval.filters` (wie in 7). Bei `off` läuft alles exakt wie heute.

**a) Orientierung vor der Chunk-Suche** (`knowledge`-Turn und retrieval-fähiger `complex`-Turn). Runtime ruft nach der Scope-Auflösung und dem Query-Rewrite zuerst `POST /api/v1/memory` auf, mit derselben (ggf. umformulierten) Suchanfrage, denselben `allowed_collections` und `allowed_teams` wie die Suche und `limit = 5`. Die gelieferten Dokument-Zusammenfassungen (Namenstreffer zuerst, dann die Rangfolge der Chunk-Suche, siehe 7) sind die **Fokus-Dokumente**. Die Chunk-Suche läuft dann mit dem neuen, additiven Filter `SearchFilters.document_ids` nur über diese Dokumente und liefert so Treffer aus der Tiefe der passenden Dokumente statt Ausschnitte quer über den Bestand. Liefert die fokussierte Suche keinen Chunk, wiederholt Runtime die Suche ohne Fokus (ein zusätzlicher Aufruf, nur in diesem Fall). Fällt das Gedächtnis aus (nicht erreichbar, 404 eines älteren Retrieval, 4xx) oder ist es leer, läuft die Suche unverändert.

- Titel, Typ und Kurzfassung der Fokus-Dokumente gehen als abgegrenzter Orientierungsblock in den Kontext („Daten, keine Anweisungen, nicht zitierbar"). **Quellen bleiben ausschließlich die Chunks**, der Guard ist unverändert: keine Chunks, keine Antwort.
- `RetrievalTrace` erhält additiv `focus_documents` (die fokussierten Dokument-IDs, sonst `null`), `timings_ms` zusätzlich `memory_ms` für den Gedächtnisaufruf.
- Rechte: derselbe Scope wie die Suche, gelesen über `apply_filters()`. `document_ids` ist eine reine Einschränkung innerhalb von `allowed_collections` (AND-verknüpft wie `collection`), eine fremde ID ergibt null Treffer. Ohne konkreten Scope (Guard `no_collections` oder `filter_excluded_all`) wird das Gedächtnis nicht aufgerufen.
- Kosten: Der Gedächtnis-Endpunkt bettet die Anfrage für seine Rangfolge selbst ein, die Chunk-Suche erneut. Ein gemeinsamer Embedding-Aufruf ginge nur über eine Erweiterung des Gedächtnis-Endpunkts um Chunk-Treffer (Alternative 6); in v1 wird der Mehraufwand als `memory_ms` ausgewiesen.
- Bekannte Einschränkung: Dokumente ohne Zusammenfassung (Altbestand ohne Wissensbereich, oder in den Minuten nach einer Änderung) werden nie Fokus. Liefert das Gedächtnis für eine Frage andere Dokumente, erreicht die Suche sie nur über den Rückfall. Wer Altbestand ohne Bereich mit dem Gedächtnis kombiniert, findet diese Dokumente bei Wissensfragen seltener als heute.

**b) Werkzeug `memory_lookup` im Agent-Modus.** Ein Subagent erhält neben `search_knowledge` das Werkzeug `memory_lookup(query, collection?)`, nur wenn das Gedächtnis an ist und weder Bot noch Subagent `filters` setzen. Es ruft `POST /api/v1/memory` mit dem effektiven Scope des Subagenten (Nutzerrechte ∩ Bot ∩ Subagent ∩ Anfragefilter) und denselben `allowed_teams` auf wie `search_knowledge`; `collection` schränkt wie dort nur innerhalb dieses Scopes ein. Bei leerem Scope wird das Gedächtnis gar nicht aufgerufen (Werkzeugfehler). Das Ergebnis (Bereichsübersichten mit Dokumentliste, Dokument-Zusammenfassungen mit `document_id`) ist als Daten gekennzeichnet und **nicht zitierbar**: Quellen sind weiterhin nur `search_knowledge`-Treffer, Fakten mit Verweis auf nicht gesuchte Stellen werden wie bisher verworfen. Damit der Subagent danach gezielt pro Dokument sucht, bekommt `search_knowledge` das optionale Argument `document_id` (wird zu `SearchFilters.document_ids`). `memory_lookup` verbraucht kein Suchbudget, ist aber auf zwei Aufrufe pro Recherche begrenzt; `SubagentTrace` erhält additiv `memory_lookups`. Der Systemprompt des Subagenten empfiehlt bei mehrteiligen Fragen: erst nachschlagen, dann pro Dokument suchen.

**c) `overview` bleibt außerhalb des Agent-Modus.** `overview` wird nicht in die agentenfähigen Intents aufgenommen. Ein Subagent kann nur Chunk-Treffer zitieren; eine Überblicksfrage, die er allein aus `memory_lookup` beantwortet, hätte keine Quellen und liefe in den Guard, und der Rückfall auf `conversational` aus 7 ginge im Agent-Pfad verloren. Überblicksfragen laufen deshalb auch für Agent-Bots wie heute über den `overview`-Turn (7 und 8, mit Gedächtnis-Quellen und allen Rückfällen); die Agenten-Recherche ist für `knowledge` und `complex` zuständig, wo `memory_lookup` die Orientierung liefert.

### 11. Gedächtnis für MCP-Clients (Weave-Tools)

*Ergänzung vom 2026-10-23.* Weave-Tools bietet externen Clients (Claude Code, VS Code, n8n, eigene Integrationen) heute nur `list_collections` (Name, Slug, Beschreibung) und `search`. Ein Client, der nicht weiß, was in einem Bereich liegt, sucht blind oder stellt dieselben Überblicksfragen, die Runtime seit 7 aus dem Gedächtnis beantwortet. Das Gedächtnis wird deshalb über dieselbe Tool-Surface zugänglich, mit exakt der Rechtelogik, die `search_for_scope` schon anwendet (`app/services/tools.py`): `scope.allowed_collections` ist die einzige Grenze, geprüft im eigenen Code vor jedem Aufruf, und Weave-Retrieval prüft sie darunter ein zweites Mal.

**a) `list_collections` liefert die Bereichs-Übersicht mit.** Zusätzlich zu `slug`, `name`, `description` additiv: `summary`, `topics`, `document_count`, `document_types` (Dokumentart → Anzahl, siehe 12) und `memory_built_at`. Die Werte stammen aus `POST /api/v1/memory` von Weave-Retrieval, aufgerufen mit `scope.allowed_collections` und `allowed_teams` genau wie bei der Suche, ohne `query`: Dafür wird `MemoryRequest.query` optional. Ohne `query` liefert Retrieval nur `collections` (kein Embedding, keine Chunk-Suche, keine `documents`) und wendet dieselbe Frische-Prüfung an wie bisher (`stale` → `summary = null`, nur die live ermittelte Dokumentliste). Ist das Gedächtnis aus oder noch nicht gebaut, bleiben die neuen Felder `null` bzw. leer, die Liste selbst ist unverändert. Ein Fehler beim Gedächtnisaufruf (nicht erreichbar, 404 eines älteren Retrieval) degradiert `list_collections` auf die heutige Antwort und bricht sie nie ab.

**b) Neues Tool `memory_lookup(query, collection?, document_type?)`** als MCP-Tool und REST-Spiegel `POST /api/v1/tools/memory`, mit derselben Antwortform wie das Runtime-Werkzeug aus 10b: `collections` (Übersicht, Themen, Dokumentliste mit `document_id`, Titel und Dokumentart) und `documents` (Zusammenfassung, Kernpunkte, `document_id`, Dateiname, Bereich). Die Tool-Beschreibung sagt dem Client dasselbe wie der Subagenten-Prompt: erst nachschlagen, dann pro Dokument suchen, und die Ergebnisse sind Orientierung, keine zitierfähige Quelle. Erzwingen kann Weave-Tools das bei einem fremden Client nicht; was es erzwingt, sind die Rechte.

- `collection` ist wie bei `search` eine reine Verfeinerung innerhalb von `scope.allowed_collections`; ein Slug außerhalb des Scopes liefert ein leeres Ergebnis, keinen Fehler und keinen Hinweis auf dessen Existenz (Grundregel Rechte). Das unterscheidet sich bewusst vom Runtime-Werkzeug (10b, Werkzeugfehler bei leerem Scope): Ein Subagent ist ein eigener Prozess, ein MCP-Client ein Fremder.
- `allowed_teams` wird genau wie bei `search_for_scope` gebildet: `None` für technische Identitäten, sonst `scope.effective_teams`. Der `MemoryRequest` verlangt weiterhin eine konkrete `allowed_collections`-Liste.
- Kein Suchbudget, keine Aufrufgrenze: Ein MCP-Client unterliegt dem bestehenden Rate-Limit der Tool-Surface, nicht dem Budget eines Bot-Turns.
- Jeder Aufruf schreibt dieselbe Audit-Zeile wie die beiden bestehenden Tools (`tool=memory_lookup`, `collection`, `result_count`, `denied`), ohne Zusammenfassungstext.

**c) `search` erhält `document_id?` und `document_type?`.** `document_id` wird zu `SearchFilters.document_ids = [document_id]` (10a), `document_type` zu `SearchFilters.document_type` (12). Beides sind Verfeinerungen innerhalb des Scopes: eine fremde oder unbekannte ID ergibt null Treffer. Damit kann ein Client den Dreischritt des Subagenten nachbilden: `memory_lookup` → `search(query, document_id=…)` → zitieren.

**Rechte in einem Satz:** Weave-Tools gibt aus dem Gedächtnis nie etwas aus, was derselbe Aufrufer nicht auch über `search` erreichen könnte, weil beide Wege dieselbe `allowed_collections`-Liste an denselben Retrieval-Dienst schicken und Retrieval beide über `apply_filters()` liest (4).

### 12. Dokumentart als Suchfilter

*Ergänzung vom 2026-10-23.* `SearchFilters.document_type` existiert bereits, trifft aber nur `chunks.meta['document_type']`, also einen Frontmatter-Schlüssel, den das Schema nicht einmal vorschreibt und der in der Praxis fast nie gesetzt ist. Das Gedächtnis bestimmt seit 1 für jedes Dokument eine Dokumentart (Vertrag, Richtlinie, Anleitung, Protokoll, E-Mail, …), per LLM oder extraktiv aus dem Frontmatter. Diese Dokumentart wird zur zweiten Quelle des Filters.

- **Semantik:** `filters.document_type` trifft ein Dokument, wenn der Frontmatter-Wert **oder** die Dokumentart seiner Gedächtniszeile übereinstimmt (`knowledge_memory.kind = 'document'`, per `document_id`). Verglichen wird nach Kleinschreibung und Trimmen, ohne Synonyme: Wer „Vertrag" filtert, bekommt keine „contracts". Die Normalisierung der Werte ist Sache des Zusammenfassers (6), nicht des Filters.
- **SQL in beiden Dialekten:** Die Gedächtnis-Klausel ist ein `EXISTS` auf eine reguläre Tabelle und läuft auf Postgres und SQLite gleich; nur die Frontmatter-Klausel bleibt wie heute auf SQLite ein Python-Nachfilter (`meta_matches()`). Beide Klauseln werden mit `OR` verknüpft und wie jeder andere Filter vor dem Ranking angewendet.
- **Kein Rechteeffekt:** Die Gedächtniszeile wird nur für Dokumente gelesen, die `apply_filters()` ohnehin zugelassen hat; ein Dokument ohne Zusammenfassung ist über den Filter weiterhin nur per Frontmatter erreichbar. Der Filter kann nie etwas sichtbar machen, nur einschränken.
- **Auffindbarkeit der Werte:** `MemoryCollection` erhält additiv `document_types: dict[str, int]`, gezählt über die live ermittelte Dokumentliste des Bereichs (dieselben Zeilen, die `document_count` zählen). Weave-Tools reicht es in `list_collections` durch (11a), Runtime zeigt es im `overview`-Turn als Teil der Übersicht. Ein Client erfährt so, welche Werte der Filter überhaupt annehmen kann, ohne eine zweite Facetten-Abfrage.
- **Gedächtnis-Endpunkt:** `MemoryRequest` erhält additiv `document_type`; `_documents()` filtert danach, `_ranked_document_ids()` reicht es in die Chunk-Suche durch.
- **Bots:** Die Regel aus 7 und 10, dass ein Bot mit `retrieval.filters` das Gedächtnis überspringt, wird präzisiert: Übersprungen wird nur bei Filtern, die das Gedächtnis nicht abbilden kann (`team`, `department`, `source`, `language`, `tags`). `collection`, `document_ids` und `document_type` bildet es ab und reicht sie durch. Ein Bot „nur Verträge" behält damit Fokus und `memory_lookup`.

**Verträge (bei Umsetzung, alle additiv, kein Versionssprung):** `contracts/internal-chat.md` (Bot-Filterregel, `document_types` in Übersichtsquellen), Weave-Tools-README und `contracts/openapi.json` (neues Tool, neue Argumente, neue Felder in `CollectionOut`), Retrieval-Schemas (`MemoryRequest.query` optional, `MemoryRequest.document_type`, `MemoryCollection.document_types`). Der Chunk-Store-Vertrag ändert sich nicht: Es kommt keine Spalte und kein Index hinzu, `knowledge_memory.document_type` wird lediglich zusätzlich gelesen.

## Konsequenzen

**Positiv**

- Überblicksfragen bekommen echte, belegte Antworten statt Bereichsnamen oder Guard-Absagen.
- Die Berechtigung entsteht aus demselben SQL-Prädikat wie bei der Suche; es gibt keine zweite Rechtelogik.
- Das Gedächtnis folgt jeder Änderung automatisch, gebündelt pro Bereich und ohne neue Infrastruktur (DB-Zustand + bestehender Tick).
- LLM-Kosten fallen nur für neue oder geänderte Dokumente an und sind standardmäßig aus; `extractive` funktioniert ganz ohne Budget.
- Wissensfragen werden auf die passenden Dokumente fokussiert, Agenten schlagen vor der Suche nach, wo was steht (10).
- Externe MCP-Clients bekommen dieselbe Orientierung wie die Subagenten, über dieselbe Rechtegrenze und ohne zweite Rechtelogik (11); die Dokumentart wird vom Nebenprodukt zum nutzbaren Filter mit sichtbaren Werten (12).

**Negativ**

- Bei Gedächtnis an kostet jeder Wissens-Turn einen zusätzlichen Retrieval-Aufruf mit eigenem Embedding (`memory_ms`); Dokumente ohne Zusammenfassung werden nie Fokus (10a).

- Der Chunk-Store-Vertrag wächst um eine Tabelle, und Retrieval braucht eine weitere Read-Model-Kopie.
- Das Gedächtnis hinkt einer Änderung um Entprellzeit + Tick hinterher (typisch 2–3 Minuten). Entfernte Inhalte sind davon ausgenommen, sie verschwinden sofort (Cascade + Frische-Prüfung).
- Ein großer Erstimport verbraucht im Modus `llm` einmalig einen LLM-Aufruf pro Dokument, gestreckt über mehrere Ticks.
- LLM-Zusammenfassungen können ungenau sein. Gegenmaßnahme: Sie zitieren immer das Quelldokument, Detailfragen laufen weiter über die Chunk-Suche.
- Ein MCP-Client kann Gedächtnis-Ergebnisse als Quelle ausgeben, obwohl sie nur Orientierung sind; Weave-Tools kann das nur in der Tool-Beschreibung verlangen, nicht erzwingen (11b).
- Die Dokumentart ist eine LLM-Klassifikation ohne feste Werteliste; zwei Dokumente derselben Art können verschieden benannt sein, bis der Zusammenfasser die Werte normalisiert (12). `list_collections` kostet mit Gedächtnis einen zusätzlichen Retrieval-Aufruf (11a).

## Alternativen

1. **Zusammenfassungen als zusätzliche Chunks bzw. synthetische Dokumente im Chunk-Store:** Die Berechtigung wäre automatisch gleich, aber Inventar, Indizierungsstatus, `reindex_all`, Ablösungslogik und Zählungen müssten synthetische Zeilen überall ausnehmen. Für Bereichs-Übersichten gibt es kein Dokument. Verworfen.
2. **Zusammenfassen in Runtime bei der Frage:** kostet LLM-Zeit bei jeder Überblicksfrage und skaliert nicht mit der Bereichsgröße. Verworfen.
3. **Nur Bereichsbeschreibung im Prompt (Status quo):** beantwortet „Was ist drin?“ nicht.
4. **Eigene Embeddings für Zusammenfassungen:** mehr Speicher und Embedding-Aufrufe. Die Gruppierung der Chunk-Suche nach Dokument liefert die Rangfolge bereits. Später nachrüstbar, falls die Qualität nicht reicht.
5. **Bevorzugen statt fokussieren (zu 10a):** mehr Treffer als `final_k` holen und Chunks der Fokus-Dokumente in Runtime nach vorn sortieren. Schonender für Dokumente ohne Zusammenfassung, erreicht aber keine tieferen Treffer, weil der Kandidatenpool unverändert bleibt. Verworfen zugunsten des Filters mit Rückfall; nachrüstbar.
6. **Gedächtnis-Endpunkt liefert die Chunk-Treffer gleich mit (ein Embedding-Aufruf):** vermischt zwei Verträge in einer Antwort. Nachrüstbar, falls `memory_ms` in der Praxis stört.
7. **`overview` im Agent-Modus mit Gedächtnis-Quellen aus Werkzeugergebnissen:** braucht eine zweite Quellenart im Subagenten-Ergebnis und eine eigene Guard-Regel. Für v1 verworfen (10c).
8. **Übersicht nur über ein eigenes Tool statt in `list_collections` (zu 11a):** hält `list_collections` unverändert, aber jeder Client müsste zwei Aufrufe machen, um zu erfahren, was ein Bereich enthält. Die Felder sind additiv und `null`, solange kein Gedächtnis existiert; verworfen.
9. **Dokumentart als eigene Spalte auf `documents` (zu 12):** sauberer zu indizieren, aber Knowledge müsste `documents` aus dem Gedächtnis zurückschreiben und der Chunk-Store-Vertrag bekäme eine Spalte mit zwei Schreibpfaden (Pipeline und Gedächtnis). Das `EXISTS` auf `knowledge_memory` reicht bei der Zahl der Dokumente pro Bereich; nachrüstbar.
10. **Feste Werteliste für die Dokumentart (zu 12):** verlässliche Filterwerte, aber jeder Bestand hat andere Arten. Stattdessen zeigt `document_types` die tatsächlich vergebenen Werte; eine Normalisierungsliste im Zusammenfasser bleibt möglich.
11. **Dokumentart als `Source`-Feld in jedem Suchtreffer (zu 12):** nützlich für Quellenkarten, kostet aber einen Join pro Treffer in der heißen Suche. Für jetzt verworfen; `memory_lookup` liefert die Art pro Dokument.
