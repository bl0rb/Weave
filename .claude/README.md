# Model-Escalation für Claude Code

Kontextbewusste Modellwahl in drei Stufen. Diese Datei ist die Doku für
Menschen; sie wird von Claude Code **nicht** automatisch geladen. Die Regeln,
die der Agent befolgt, stehen in `rules/` und sind dort jeweils nur einmal
definiert.

## Aufbau

| Datei | Zuständig für | Geladen |
|---|---|---|
| `../CLAUDE.md` | Import von `AGENTS.md`, Verweis auf die Regeln | jede Session |
| `../AGENTS.md` | Allgemeine Arbeitsregeln (unverändert bis auf einen Verweis) | über `CLAUDE.md` |
| `rules/model-routing.md` | Stufen, Modellzuordnung, wann delegiert wird, Kostenregel | jede Session |
| `rules/context-continuity.md` | Task Affinity, Folgefragen, Themenwechsel, Ergebnisse erhalten | jede Session |
| `rules/escalation-policy.md` | Auslöser, Mehrwertprüfung, Stufenfolge, Fallback | jede Session |
| `rules/context-handover.md` | Übergabe- und Rückgabeformat, Integration | jede Session |
| `agents/scout.md` | L1-Subagent, nur lesend | bei Aufruf |
| `agents/escalation-reviewer.md` | L3-Subagent, analysiert und empfiehlt, ändert keine Dateien | bei Aufruf |

`CLAUDE.md` importiert `AGENTS.md`, weil Claude Code `AGENTS.md` nur dann von
sich aus liest, wenn es keine `CLAUDE.md` gibt.

## Ablauf

1. Der Hauptagent (L2) bearbeitet die Aufgabe und behält sie bis zum Ende.
2. Für breite, abgeschlossene Suchen kann er `scout` (L1) starten.
3. Greift ein Auslöser aus `escalation-policy.md` und besteht die
   Mehrwertprüfung, startet er `escalation-reviewer` (L3) mit einer Übergabe
   nach `context-handover.md`.
4. Der Subagent antwortet im festen Rückgabeformat; der Hauptagent prüft,
   integriert und setzt die ursprüngliche Aufgabe fort.
5. Reicht das nicht, empfiehlt der Hauptagent dem Nutzer den Wechsel des
   Hauptmodells mit `/model`.

## Modelle konfigurieren

Die Regeln sprechen nur von L1/L2/L3. Modellnamen stehen an genau diesen Stellen:

| Stufe | Stellschraube | Standard |
|---|---|---|
| L1 | `model:` in `agents/scout.md` | `haiku` |
| L2 | Modell der Session: `/model`, `--model`, `ANTHROPIC_MODEL`, `model` in Settings | Wahl des Nutzers |
| L3 | `model:` in `agents/escalation-reviewer.md`, dazu `effort:` | `opus`, `high` |

`model:` nimmt einen Alias (`haiku`, `sonnet`, `opus`, `fable`), eine volle
Modell-ID oder `inherit`. Auf welche Version ein Alias zeigt, lässt sich mit
`ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` und
`ANTHROPIC_DEFAULT_OPUS_MODEL` festlegen.

Bewusst nicht eingecheckt, weil es alle Sessions im Repo betrifft und ein
gespeichertes `/model` bei jedem Start überschreibt:

```json
{
  "model": "sonnet",
  "fallbackModel": ["sonnet", "haiku"]
}
```

Als `.claude/settings.json` (Team) oder `.claude/settings.local.json` (privat)
legt das L2 fest und gibt Claude Code eine Ausweichkette für überlastete oder
nicht verfügbare Modelle.

## Was technisch gilt und was nur Instruktion ist

**Von Claude Code umgesetzt**

- `CLAUDE.md` und alle Dateien in `rules/` werden beim Sessionstart geladen.
- Ein Subagent läuft auf dem Modell aus seinem Frontmatter, mit den dort
  erlaubten Tools und in einem eigenen, leeren Kontext.
- Ist ein Modell durch `availableModels` gesperrt, weicht Claude Code für den
  Subagenten auf ein erlaubtes Modell aus; mit `fallbackModel` auch bei
  Überlast oder Nichtverfügbarkeit.
- Ein früherer Subagent lässt sich mit seinem Verlauf fortsetzen (`SendMessage`).

**Nur Instruktion, nicht erzwungen**

- Ob und wann delegiert oder eskaliert wird, Task Affinity, die Erkennung eines
  Themenwechsels, die Mehrwertprüfung, die Obergrenzen für Agenten und das
  Übergabeformat. Das Modell liest diese Regeln als Kontext und hält sich in
  der Regel daran; garantiert ist es nicht.

**Nicht möglich ohne Zusatztechnik**

- Der Hauptagent kann sein eigenes Modell nicht wechseln. Das geht nur über
  den Nutzer (`/model`) oder beim Start.
- Es gibt kein automatisches Routing einzelner Aufgaben auf ein Hauptmodell.
- Der Hauptagent erfährt nicht zuverlässig, auf welchem Modell ein Subagent
  tatsächlich lief, etwa nach einem Fallback.
- Der Aufgabenstand überlebt die Session nur als Transkript (`--resume`) und
  kann durch Kompaktierung verkürzt werden; eine eigene Task-Verwaltung gibt
  es nicht.

Kosten, die man kennen sollte: Ein Wechsel des Hauptmodells liest den gesamten
Verlauf einmal ohne Cache neu. Ein Subagent startet mit eigenem Systemprompt
und ohne Cache des Hauptagenten; der Cache des Hauptagenten bleibt erhalten.
In der Prüfsession hat allein der Start eines Subagenten rund 64.000 Token
gekostet, deshalb erledigt der Hauptagent Kleinigkeiten selbst.

## Mögliche technische Ausbaustufen

Nicht umgesetzt; nur nötig, wenn die Instruktionen nicht reichen.

| Ziel | Umsetzung |
|---|---|
| Übergabeformat erzwingen | `PreToolUse`-Hook auf das Agent-Tool, der Aufrufe von `escalation-reviewer` ohne die acht Überschriften ablehnt. Vorher prüfen, welche Felder `tool_input` enthält. |
| Eskalationen begrenzen und zählen | Derselbe Hook mit Zähler je Session; `SubagentStop` zum Protokollieren. |
| Wechsel des Hauptmodells bestätigen lassen | `PreModelSwitch`-Hook. |
| Aufgabenstand über Sessions erhalten | Statusdatei, die ein `SessionStart`-Hook als Kontext einspielt und der Agent am Aufgabenende schreibt. |
| Echtes automatisches Routing | Externe Orchestrierung über Agent SDK oder `claude -p --model …` je Aufgabe. Hooks können kein Modell wählen. |

## Testmatrix

Das Verhalten der Szenarien 1 bis 7 beruht auf Instruktionen. Sie sind gegen
die Regeln durchgespielt, aber nicht in einer laufenden Session gemessen.
Zum Nachprüfen: neue Session starten, Szenario ausführen, mit `/usage` und der
Agentenliste (`/agents`) kontrollieren, welche Modelle liefen.

| # | Szenario | Erwartetes Verhalten | Regel | Stand |
|---|---|---|---|---|
| 1 | Mehrere einfache Folgefragen | Hauptagent antwortet selbst, kein Subagent, kein Modellwechsel | `context-continuity.md`, Task affinity | durchgespielt |
| 2 | Aufgabe wird schwieriger | Aufgabe bleibt beim Hauptagenten; bei Auslöser plus Mehrwertprüfung eine gezielte L3-Frage | `escalation-policy.md`, Triggers und Value check | durchgespielt |
| 3 | Fehler nach zwei gescheiterten Versuchen | Auslöser 1, Übergabe mit „Failed attempts“, L3 wiederholt sie nicht | `escalation-policy.md`, `agents/escalation-reviewer.md` | durchgespielt |
| 4 | Themenwechsel | Neue Routingentscheidung; im Zweifel Fortsetzung | `context-continuity.md` | durchgespielt |
| 5 | Subagent-Eskalation | Übergabe mit allen acht Abschnitten; fehlt etwas, antwortet L3 mit `NEEDS_CONTEXT:` statt selbst zu suchen | `context-handover.md`, `agents/escalation-reviewer.md` | durchgespielt |
| 6 | Rückkehr zum Hauptagenten | Ergebnis prüfen, in wenigen Zeilen festhalten, Aufgabe auf dem Hauptmodell fortsetzen | `context-handover.md`, Integration | durchgespielt |
| 7 | Unnötige Eskalation | Keine Eskalation wegen Länge oder Zahl der Nachfragen; keine, wenn L3 nicht stärker ist als das Hauptmodell | `escalation-policy.md`, Not triggers und Value check | durchgespielt |
| 8 | Modell fehlt oder ist nicht verfügbar | Kein Wiederholen in Schleife; L3 fällt auf Hauptmodell mit Hinweis an den Nutzer zurück, L1 auf Inline-Arbeit | `escalation-policy.md`, Fallback | durchgespielt; Aliasse `haiku` und `opus` in der Prüfsession gestartet und verfügbar |
