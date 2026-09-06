# Bereinigung der Integrationsgrenzen

Stand: 3. September 2026

Diese Prüfung trennt den produktiven Kern des Wissensportals von historischen, optionalen und nur für den Betrieb gedachten Funktionen. Bewertet wurde mit `(Nutzen + Risiko) × (6 − Aufwand)`, jeweils auf einer Skala von 1 bis 5. Ein hoher Wert bedeutet: früh erledigen.

## Verbindlicher Zielablauf

Die Dokumentstrecke und die Chatstrecke sind zwei getrennte Abläufe:

```text
Dokument / .eml / Confluence
  → Ingest → Verarbeitung → Nutzerfreigabe
  → Knowledge → Chunk-Store → Retrieval

Chat
  → API → Runtime → n8n-Agentenflow
  → Tools (MCP oder REST, Delegations-Token)
  → Retrieval → erlaubte Collections
  → n8n → Runtime → API → Chat
```

n8n liest also im Auftrag eines angemeldeten Menschen über Weave-Tools. Ingest sendet weder Collections noch ihre ACL an n8n. Die vom Nutzer gewählte Collection ist immer nur ein zusätzlicher Filter innerhalb des serverseitig ermittelten Berechtigungsumfangs.

## Jetzt entfernt

| Baustein | Entscheidung | Begründung | Wert |
|---|---|---|---:|
| Direkter OpenWebUI-Push aus Ingest | Entfernt | Die eigene Chat-Oberfläche und die zentrale RAG-Strecke ersetzen eine zweite Wissenssenke. Zwei Indizes würden Freigabe, Löschung, Versionen und Rechte auseinanderlaufen lassen. | 45 |
| OpenWebUI-Verbindungsverwaltung, Push-Dialog, API und Worker-Task | Entfernt | Diese Oberflächen und Endpunkte hatten nach Wegfall des Push-Ziels keine fachliche Aufgabe mehr. | 40 |
| `collection.updated` über benutzerverwaltete Webhooks | Entfernt | Der frühere Fan-out konnte die vollständige teamübergreifende ACL-Matrix an beliebige Ziele ausgeben. | 50 |
| Separates Mail-API-Schema | Entfernt | Einzelne `.eml`-Dateien laufen durch denselben Upload-, Prüf- und Freigabeprozess wie andere Dokumente. | 28 |

Die historischen Alembic-Schritte und alten OpenWebUI-Tabellen bleiben zunächst bestehen. Das hält ein Rollback über die aktuelle Release-Grenze möglich und vermeidet eine destruktive Datenmigration im selben Deployment wie die Laufzeitbereinigung. Nach Ablauf des vereinbarten Rollback- und Aufbewahrungsfensters kann eine eigene Migration zuerst `openwebui_pushes`, danach `openwebui_connections` löschen. Die Migration `0010_openwebui` bleibt dauerhaft in der Historie, weil sie außerdem weiterhin benötigte Import- und Refresh-Strukturen angelegt hat.

## Bewusst behalten

| Baustein | Rolle im Zielbild | Grenze |
|---|---|---|
| `.eml`-Dateiupload | Einzelne E-Mails als normale Quelle verarbeiten | Kein separates Postfach und keine Mail-API. |
| Confluence | Automatisierbare Quelle für Seiten und Anhänge | Import in Ingest; Veröffentlichung erst nach Freigabe. |
| Generische Export-Webhooks | Optionaler Adapter für ausdrücklich ausgewählte Jobs und Importläufe | Kein Bestandteil von Indexierung oder Agentenchat; keine Collection-ACL-Ereignisse. |
| OpenAI-kompatible Weave-API | Bestandsclients können Bots über `/v1/chat/completions` und `/v1/models` nutzen | Nur Protokollkompatibilität; kein eigener Index und kein produktspezifischer OpenWebUI-Code. |
| Eigene Chat-Oberfläche (`services/chat`) | Produktiver Dialogkanal mit Streaming, Quellen und Collection-Filter | Öffnet aus dem Wissensportal und spricht ausschließlich mit Weave-API; kein direkter Zugriff auf Retrieval oder Ingest. |
| n8n als Bot-Provider | Orchestriert Agenten, Suche und Aktionen | Runtime ruft den konfigurierten Flow auf; der Flow greift mit delegiertem Scope auf Tools zu. |
| Weave-Tools über MCP/REST | Durchsetzung des delegierten Collection-Umfangs | Tool-Argumente können Rechte nie erweitern. |
| Interne Collection-Benachrichtigung | Beschleunigt den Registry-Abgleich Ingest → Knowledge | Nur `event`, `timestamp`, `slug`; keine ACL. Periodischer Poll als Ausfallsicherung. |

## Aus dem Fachbereichsportal herausnehmen

Diese Funktionen bleiben technisch vorhanden, gehören aber unter **Administration → Betrieb** und nicht in den normalen 1–2–3-Ablauf:

| Funktion | Ziel | Wert |
|---|---|---:|
| Rohansicht aller Jobs und detaillierte Queue-Steuerung | Admin-/Supportansicht „Verarbeitung“ | 30 |
| VL-Benchmark und technische Profilparameter | Admin-/Qualitätssicherung | 24 |
| Generische Webhook-Verbindungen und Zustellprotokoll | Admin-/Integrationsverwaltung | 28 |
| Worker-, Login- und Signaturdiagnose | Admin-/Betriebsprotokolle | 35 |

Fachanwender sehen stattdessen den Status direkt am Wissensbereich und Dokument: **hochgeladen**, **wird verarbeitet**, **bereit zur Prüfung**, **freigegeben**, **wird indiziert**, **in der Suche verfügbar** oder **Aktion erforderlich**.

## Später entfernen

| Kandidat | Bedingung | Wert |
|---|---|---:|
| Weiterleitungen `/documents`, `/search`, `/mail*` | Entfernen, sobald Telemetrie und Release-Hinweise ein vollständiges Kompatibilitätsfenster ohne Nutzung zeigen. | 20 |
| Historische OpenWebUI-Tabellen | Eigene Migration nach Rollback-/Aufbewahrungsfenster und geprüftem Backup. | 24 |
| Historische Mail-API-Daten (`mail_messages`, `jobs.mail_message_id`) | Erst entfernen, wenn vorhandene Datensätze exportiert oder abgelaufen sind und kein Rollback mehr auf die frühere Postfach-API nötig ist. Der `.eml`-Dateiupload benötigt diese API-Entitäten nicht. | 20 |
| Nicht mehr erreichbare Frontend-Komponenten und Kommentare | Laufend durch statische Suche und CI verhindern. | 18 |

## Nach Nutzung entscheiden

| Kandidat | Entscheidungskriterium |
|---|---|
| Standalone-LLM-Konfiguration in Runtime | Nach vollständigem Rollout der zentralen Chat-Konfiguration entweder als klar dokumentierten Break-glass-Fallback behalten oder entfernen. Zwei gleichrangige Konfigurationsquellen sollen nicht dauerhaft bestehen. |
| Generische Export-Webhooks | Nur behalten, wenn ein benannter fachlicher Exportfall und ein verantwortlicher Betreiber existieren. Sie sind bereits per Feature-Flag abschaltbar und gehören nicht zum n8n-Agentenpfad. |
| Separates Deployment der Chat-Oberfläche | Beibehalten, solange es den produktiven Chat bereitstellt. Erst bei einer echten Integration in denselben Frontend-Build wie das Wissensportal entfällt `services/chat`; ein bloßer Link aus dem Portal ist noch keine Doppelimplementierung. |

## Abnahmekriterien

- Im gebauten Ingest-Frontend und in der aktuellen OpenAPI gibt es keine OpenWebUI-Oberfläche und keinen OpenWebUI-Endpunkt.
- Eine Collection-Änderung erzeugt genau eine interne, signierte Benachrichtigung an Knowledge ohne `read_teams`.
- Eine generische Webhook-Verbindung kann `collection.updated` weder anlegen noch abonnieren.
- Ein n8n-Agent sucht ausschließlich über Weave-Tools mit einem gültigen Delegations-Token; fremde Collections liefern keine Treffer und keine Existenzinformation.
- `.eml` bleibt im normalen Quellen-Upload nutzbar.
- Die OpenAI-kompatiblen Gateway-Routen bleiben unabhängig von einem bestimmten Client erhalten.
- Alle Service-, Frontend-, Contract-, Compose- und Helm-Prüfungen sind grün.
