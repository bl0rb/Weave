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
| `agents/escalation-reviewer.md` | L3-Subagent, analysiert und empfiehlt | bei Aufruf |

Die beiden Agent-Dateien wiederholen ihr Ein- und Ausgabeformat, weil sie mit
`omitClaudeMd: true` ohne `CLAUDE.md`, `AGENTS.md` und die Regeln starten.

`CLAUDE.md` importiert `AGENTS.md`, weil Claude Code `AGENTS.md` nur dann von
sich aus liest, wenn es keine `CLAUDE.md` gibt.

## Ablauf

1. Der Hauptagent (L2) bearbeitet die Aufgabe und behält sie bis zum Ende.
2. Für abgeschlossene Nachschlage-Aufgaben, die viele Tool-Aufrufe oder große
   Dateien und Logs bräuchten, kann er `scout` (L1) starten.
3. Greift ein Auslöser aus `escalation-policy.md` und besteht die
   Mehrwertprüfung, startet er `escalation-reviewer` (L3) mit einer Übergabe
   nach `context-handover.md`.
4. Der Subagent antwortet im festen Rückgabeformat; der Hauptagent prüft,
   integriert und setzt die ursprüngliche Aufgabe fort.
5. Reicht das nicht, empfiehlt der Hauptagent dem Nutzer den Wechsel des
   Hauptmodells mit `/model`.

## Modelle konfigurieren

Die Regeln sprechen von L1/L2/L3. Modellnamen stehen nur an diesen Stellen
und in der Familien-Reihenfolge in `rules/model-routing.md`:

| Stufe | Stellschraube | Wert bei Auslieferung |
|---|---|---|
| L1 | `model:` in `agents/scout.md` | `haiku` |
| L2 | Modell der Session: `/model`, `--model`, `ANTHROPIC_MODEL`, `model` in Settings | Wahl des Nutzers |
| L3 | `model:` in `agents/escalation-reviewer.md`, dazu `effort:` | `opus`, `high` |

`model:` nimmt einen Alias (`haiku`, `sonnet`, `opus`, `fable`), eine volle
Modell-ID oder `inherit`. Auf welche Version ein Alias zeigt, lässt sich mit
`ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
`ANTHROPIC_DEFAULT_OPUS_MODEL` und `ANTHROPIC_DEFAULT_FABLE_MODEL` festlegen.

Die Mehrwertprüfung vergleicht Modellfamilien. Die Reihenfolge steht in
`rules/model-routing.md` („Capability order“) und muss angepasst werden, wenn
eine Familie dazukommt. Welche Modelle ein Konto oder Provider tatsächlich
anbietet, zeigt `/model`; die Standards oben sind nur in der Prüfsession
bestätigt.

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
  erlaubten Tools und ohne den Gesprächsverlauf. Zwei Ausnahmen beim Modell:
  Ein `model`-Parameter im Agent-Aufruf hat Vorrang, und ein Alias aus der
  Familie des Hauptmodells läuft auf genau dem Hauptmodell. `CLAUDE.md`, `AGENTS.md` und
  `rules/` bekäme er normalerweise mit; `omitClaudeMd: true` schaltet das für
  beide Agents ab (ab Claude Code v2.1.271).
- Ist ein Modell durch `availableModels` gesperrt, weicht Claude Code für den
  Subagenten auf ein erlaubtes Modell aus; mit `fallbackModel` auch bei
  Überlast oder Nichtverfügbarkeit.
- Ein früherer Subagent lässt sich mit seinem Verlauf fortsetzen (`SendMessage`).

**Nur Instruktion, nicht erzwungen**

- Ob und wann delegiert oder eskaliert wird, Task Affinity, die Erkennung eines
  Themenwechsels, die Mehrwertprüfung, die Obergrenzen für Agenten, das
  Übergabeformat und das Fallback-Verhalten. Das Modell liest diese Regeln als
  Kontext und hält sich in der Regel daran; garantiert ist es nicht.
- Dass `escalation-reviewer` keine Dateien ändert. Er hat Bash, um Tests
  laufen zu lassen; nur `scout` ist schon durch seine Tool-Liste rein lesend.

**Nicht möglich ohne Zusatztechnik**

- Der Hauptagent kann das Sessionmodell nicht wechseln. Das geht dauerhaft
  nur über den Nutzer (`/model`) oder beim Start; Claude Code selbst wechselt
  nur für Fallbacks und in den Fällen unter „Eingebaute Alternativen“.
- Es gibt kein automatisches Routing einzelner Aufgaben auf ein Hauptmodell.
- Der Hauptagent erfährt nicht zuverlässig, auf welchem Modell ein Subagent
  tatsächlich lief. Ersetzt Claude Code das Modell, sieht er keinen Fehler.
  Nach einem Wechsel über `fallbackModel` beginnt der Bericht des Subagenten
  mit einer Zeile, dass er das Modell gewechselt hat; eine Ersetzung wegen
  `availableModels` sieht nur der Nutzer als Warnung. `escalation-reviewer`
  nennt zusätzlich am Ende sein Modell aus dem eigenen Systemprompt; das ist
  ein Hinweis, kein Beweis.
- Der Aufgabenstand überlebt die Session als Transkript (`--resume`) und kann
  durch Kompaktierung verkürzt werden. Dieses Setup bringt keine eigene
  Task-Verwaltung mit; die eingebaute Task-Liste lässt sich mit
  `CLAUDE_CODE_TASK_LIST_ID` über Sessions teilen.

Kosten, die man kennen sollte: Ein Wechsel des Hauptmodells liest den gesamten
Verlauf einmal ohne Cache neu. Ein Subagent startet mit eigenem Systemprompt
und ohne Cache des Hauptagenten; der Cache des Hauptagenten bleibt erhalten.
In der Prüfsession hat allein der Start eines Subagenten rund 64.000 Token
gekostet, deshalb erledigt der Hauptagent Kleinigkeiten selbst.

## Eingebaute Alternativen

Claude Code bringt drei Mechanismen mit, die ein stärkeres Modell ohne
Subagent einbinden. Keiner ist hier eingeschaltet oder getestet.

| Mechanismus | Einschalten | Stärkeres Modell läuft | Kosten und Grenzen |
|---|---|---|---|
| Advisor-Tool | `/advisor opus` oder `"advisorModel": "opus"` | an Entscheidungspunkten; Claude entscheidet selbst, wann | Der Advisor liest jedes Mal das ganze Gespräch ungecacht, dafür ohne Übergabe und ohne Cache-Verlust beim Hauptmodell. Experimentell, nur über die Anthropic-API, nicht begrenzbar. |
| `opusplan` | `/model opusplan` | im Plan-Modus | Jeder Wechsel in den Plan-Modus und zurück ist ein Modellwechsel mit frischem Cache. |
| Skill oder Command mit `model:` | Frontmatter der Skill-Datei | für genau einen Turn, mit vollem Kontext | Der Turn liest das Gespräch ungecacht; danach läuft wieder das Sessionmodell. |

Ist der Advisor eingeschaltet, nutzt `rules/escalation-policy.md` ihn
anstelle des Subagenten, wenn die Antwort am bisherigen Verlauf hängt und
keine neuen Lese- oder Prüfschritte braucht. Er zählt als Runde, und für ein
Problem läuft nie beides. Bei langen Gesprächen mit einer abgrenzbaren Frage
ist der Subagent mit knapper Übergabe günstiger.

## Mögliche technische Ausbaustufen

Nicht umgesetzt; nur nötig, wenn die Instruktionen nicht reichen.

| Ziel | Umsetzung |
|---|---|
| Übergabeformat erzwingen | `PreToolUse`-Hook auf das Agent-Tool, der Aufrufe von `escalation-reviewer` ohne die acht Überschriften ablehnt. |
| Eskalationen begrenzen und zählen | Derselbe Hook mit Zähler je Session; `SubagentStop` zum Protokollieren. |
| Wechsel des Hauptmodells bestätigen lassen | `PreModelSwitch`-Hook. |
| Aufgabenstand über Sessions erhalten | Statusdatei, die ein `SessionStart`-Hook als Kontext einspielt und der Agent am Aufgabenende schreibt. |
| Echtes automatisches Routing | Externe Orchestrierung über Agent SDK oder `claude -p --model …` je Aufgabe. Hooks können das Hauptmodell nicht wählen; ein `PreToolUse`-Hook kann laut Doku nur das Modell eines Subagent-Aufrufs umschreiben (`updatedInput`). |

## Testmatrix

Geprüft in einer Session mit Claude Code 2.1.322 über zwei Workflows.
„Live“ heißt: der Agent wurde wirklich gestartet. „Blind“ heißt: ein Modell
bekam nur die Regeldateien und den Fall und musste die nächste Aktion wählen;
die erwartete Antwort kannte es nicht. Ein vollständiger Ablauf mit echtem
Hauptagenten über mehrere Turns ist damit nicht gemessen.

| # | Szenario | Erwartetes Verhalten | Regel | Ergebnis |
|---|---|---|---|---|
| 1 | Mehrere einfache Folgefragen | Hauptagent antwortet selbst, kein Subagent, kein Modellwechsel | `context-continuity.md`, Task affinity | blind bestanden |
| 2 | Aufgabe wird schwieriger | Aufgabe bleibt beim Hauptagenten; bei Auslöser plus Mehrwertprüfung eine gezielte L3-Frage | `context-continuity.md`, Task affinity; `escalation-policy.md`, Triggers und Value check | blind bestanden |
| 3 | Fehler nach zwei gescheiterten Versuchen | Auslöser 1, Übergabe mit „Failed attempts“, L3 wiederholt sie nicht | `escalation-policy.md`, `context-handover.md`, `agents/escalation-reviewer.md` | blind bestanden für Auslöser und Übergabe; dass L3 gescheiterte Versuche nicht wiederholt, ist nicht getestet |
| 4 | Themenwechsel | Neue Aufgabe, neu beurteilt; im Zweifel Fortsetzung | `context-continuity.md` | blind bestanden |
| 5 | Subagent-Eskalation | Übergabe mit allen acht Abschnitten; fehlt etwas, antwortet L3 mit `NEEDS_CONTEXT:` statt selbst zu suchen | `context-handover.md`, `agents/escalation-reviewer.md` | live bestanden |
| 6 | Rückkehr zum Hauptagenten | Ergebnis prüfen, festhalten, Aufgabe auf dem Hauptmodell fortsetzen | `context-handover.md`, Integration | blind bestanden; Rückgabeformat live bestanden |
| 7 | Unnötige Eskalation | Keine Eskalation wegen Länge oder Zahl der Nachfragen; keine, wenn L3 nicht stärker ist als das Hauptmodell (Ausnahme: Auslöser 4) | `escalation-policy.md`, Triggers und Value check | blind bestanden |
| 8 | Modell fehlt oder ist nicht verfügbar | Kein Wiederholen in Schleife; L3 fällt auf Hauptmodell mit Hinweis an den Nutzer zurück, L1 auf Inline-Arbeit | `escalation-policy.md`, Fallback | blind bestanden; ein Ausfall selbst wurde nicht ausgelöst |

Einzelergebnisse:

- `scout`, Faktenfrage: elf CI-Jobs mit `path:line`, gegen `grep` geprüft und
  korrekt; lief auf `claude-haiku-5-5`.
- `scout`, Bewertungsfrage: lieferte die Fakten und gab die Bewertung mit
  `NEEDS_L2:` zurück.
- `escalation-reviewer`, unvollständige Übergabe: antwortete mit
  `NEEDS_CONTEXT:` und las keine Dateien.
- `escalation-reviewer`, vollständige Übergabe: alle fünf Abschnitte, konkrete
  Ersatztexte, keine Dateiänderung; lief auf `claude-opus-5-5`.
- Routing-Entscheidungen: 14 Fälle, je einmal auf `sonnet` und auf dem
  Sessionmodell, beide 14 von 14. Ein als mehrdeutig gemeldeter Fall führte zur
  schärferen Abgrenzung von L1 in `rules/model-routing.md`.
- Drei Reviews (Konsistenz, Fakten gegen die Doku, Abdeckung der
  Anforderungen) fanden zehn Mängel; alle sind in den Dateien korrigiert.

Zum Nachprüfen in einer eigenen Session: Szenario ausführen und mit `/tasks`
kontrollieren, auf welchem Modell und Effort ein Subagent lief (ab v2.1.242);
`/usage` zeigt den Verbrauch je Modell.
