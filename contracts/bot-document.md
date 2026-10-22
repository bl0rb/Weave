# Bot-Dokument (`bot-document.schema.json`)

Das portable, versionierte Format eines Bots — eine YAML- oder JSON-Datei, die
Weave-Runtime als Bot-Konfiguration lädt (`BOTS_DIR`) und die Weave-Ingest
über die Admin-Bot-API exportiert und importiert. Es ist die kanonische
Darstellung eines Bots; die flachen Spalten von `managed_bots` in Ingest sind
nur eine Speicherform davon.

Anbieter des Formats ist `services/runtime`
(`backend/app/schemas/bot.py`, Klasse `BotConfig` und ihre Teilmodelle); das
JSON Schema ist daraus erzeugt. Konsument ist `services/ingest`
(`backend/app/schemas/bot_document.py` als Spiegelmodell,
`backend/app/services/bot_document.py` für die Abbildung auf `managed_bots`).

## Format

- Eine Datei, ein Bot. YAML oder JSON (JSON ist gültiges YAML); Ingest nimmt
  beides an, höchstens 256 KiB, UTF-8.
- `schema_version: 1` (Pflicht im Schema, Default 1 beim Laden). Alle übrigen
  Felder sind die von `BotConfig`: `id`, `name`, `description`, `model`,
  `system_prompt`, `retrieval`, `permissions`, `guard`, `n8n`, `agent`,
  `show_sources` (Default `true`; `false` blendet im Chat Quellenleiste und
  Quellen-Knöpfe aus), `disclaimer` (optionaler Hinweistext, höchstens 500
  Zeichen, den der Chat beim Eingabefeld zeigt) und `intake` (optionale
  Einstiegsfrage: `question`, `options` (höchstens 10), `allow_free_text`
  (Default `true`), `label`; nicht für n8n-Bots). Die Einstiegsfrage stellt
  der Bot einmal vor der ersten Wissensfrage eines Gesprächs, merkt sich die
  Antwort (aus der History abgeleitet) und gibt sie dem LLM in jedem weiteren
  Turn als `<label>: <Antwort>` mit; danach beantwortet er die zuerst
  gestellte Frage. `guard.blocked_topics` (`[{name, description, keywords}]`)
  und `guard.blocked_topic_reply` sperren Themen technisch: Treffer (Router-
  Klassifikation bei `ROUTER_MODE=llm`, Stichwörter immer) erhalten den festen
  Text ohne Retrieval und ohne Antwort-LLM.
  Unbekannte Schlüssel sind überall ein Fehler (`additionalProperties: false`).
- Ein Export schreibt jedes gesetzte Feld inklusive der Runtime-Defaults aus
  (`exclude_none`); Felder mit dem Wert `null` fehlen.
- Dateinamen: `<id>.bot.yaml` bzw. `<id>.bot.json`.

## Platzhalter-Regel

`n8n.auth_token` verlässt Ingest nie. Ein Export schreibt statt des Tokens den
Platzhalter `${WEAVE_SECRET}`. Runtime lehnt eine Datei mit genau diesem Wert
beim Laden mit einer Meldung ab, die den Platzhalter nennt (ein exportiertes
Dokument darf nicht unverändert in `BOTS_DIR` landen). Ingest importiert den
Platzhalter als „kein Token“ und meldet `missing_secret`; beim Überschreiben
eines vorhandenen Bots mit unveränderter Webhook-URL bleibt dessen
gespeichertes Token erhalten. Ein echtes Token in einer Importdatei wird
übernommen und verschlüsselt gespeichert.

## Ausgeschlossene Felder

- `permissions.teams` und `permissions.users` werden beim Export immer
  weggelassen (leer) und beim Import ignoriert (`permissions_reset`): Freigaben
  sind lokale Zuordnungen zu Personen und Teams, kein Teil der Bot-Definition.
  `permissions.public` bleibt erhalten; ohne `public` gilt wie in Runtime:
  Teams oder Personen genannt = geschlossen, sonst offen.
- `model.provider`, `model.model` und `model.supports_tools` eines verwalteten
  Bots sind in Runtime fest (`fake`/`fake-chat` bzw. `n8n`/`n8n-agent-flow`);
  Ingest speichert sie nicht und meldet abweichende Werte als `dropped_field`.
  Welcher zentrale Chat-Provider antwortet, steht in `model.endpoint`.
- Für n8n-Bots gelten `system_prompt`, `model.temperature`, `model.endpoint(s)`
  und `agent` nicht (`dropped_field`).

Invariante: Der Export eines verwalteten Bots entspricht Runtimes
`_managed_bot`-Projektion (`backend/app/services/botconfig.py`) minus
`permissions.teams`/`users` minus Secret.

## Import-Report

`POST /api/v1/auth/admin/bots/import` (multipart: `file`, `dry_run`,
`on_conflict` = `fail`|`overwrite`, optional `bot_id`) antwortet mit
`BotImportReport`: `schema_version`, `bot_id`, `name`, `kind`,
`conflict` (`managed`|`runtime`|`tombstoned`|null), `action`
(`create`|`overwrite`|`blocked`), `warnings` (`code`: `missing_secret`,
`dropped_field`, `permissions_reset`; `detail`), `errors` (Parse-, Schema-
und Validierungsfehler, jeder blockiert), `applied`, `bot`. Der Dry-Run
antwortet immer `200`; ein angewendeter Import `409`, wenn ein vorhandener Bot
ohne `overwrite` blockiert, und `422`, wenn das Dokument selbst blockiert.
Unbekannte Wissensbereiche oder LLM-Endpoints sind Fehler, keine Warnungen.

## Versionspolitik

- Additiv (neues optionales Feld, neuer Default): `schema_version` bleibt 1,
  Schema neu erzeugen und committen; Ingests Spiegel nachziehen.
- Breaking (Pflichtfeld entfernt/umbenannt, Typ geändert): `schema_version`
  auf 2, beide Seiten im selben Fenster; Ingest lehnt eine unbekannte
  `schema_version` beim Import ab, Runtime beim Laden.

## Schema neu erzeugen

```bash
cd services/runtime/backend && python -m app.schemas.bot_document_schema > ../../../contracts/bot-document.schema.json
```

Prüfung der Parität auf beiden Seiten:
`services/runtime/backend/tests/test_bot_document_schema.py` und
`services/ingest/backend/tests/test_bot_document_schema.py`. Das committete
Schema enthält keine `description`-Texte; die Erläuterungen stehen bei den
Feldern in Runtimes `bot.py`.
