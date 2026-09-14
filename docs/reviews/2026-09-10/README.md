# Sicherheits- und Funktionsaudit · 10.09.2026

Geprüfter Commit: `7333479cbcff5395797fc93de66dc38a6ccb8f60`.
Der Working Tree war zu Beginn sauber. Dieses Audit ergänzt ausschließlich
Dokumentation; die beschriebenen Fehler sind noch nicht behoben.

## Ergebnis

Die vorhandenen Tests liefern eine gute Grundlage, decken aber mehrere
wichtige Fehler an Dienstgrenzen nicht ab. **14 konkrete Befunde** sind
dokumentiert: vier P1, neun P2 und ein P3. Härtungsideen und nicht bestätigte
Hypothesen sind davon getrennt.

Am dringendsten sind:

1. **Upload-Verarbeitung vor Authentifizierung und Größenprüfung:** Ohne
   Anmeldung wird der Dateiinhalt bereits vollständig in den Multipart-Spool
   geschrieben, bevor die App den Request abweist.
2. **Login-CSRF im letzten Chat-SSO-Schritt:** Der Übergabecode ist nicht an
   den Browser gebunden, der den Chat-Login gestartet hat.
3. **Modellwechsel erreicht den Indexierungs-Worker nicht:** Worker und
   Retrieval können unterschiedliche Embedding-Konfigurationen verwenden.
4. **Helm-Bootstrap mit `--wait`:** Bei frischen Datenbanken entsteht aus der
   Hook-Reihenfolge eine gegenseitige Startabhängigkeit.

## Dokumente

| Datei | Inhalt |
|---|---|
| [sicherheit.md](sicherheit.md) | Fünf Sicherheitsbefunde mit Voraussetzungen, Belegen, Gegenmaßnahmen und Abnahmetests |
| [funktion.md](funktion.md) | Neun Funktions-/Betriebsbefunde, einschließlich n8n, Recovery, Konfiguration und CI |
| [testprotokoll.md](testprotokoll.md) | Ausgeführte Kommandos, Ergebnisse, Umgebungen, Reproduktionen und Prüflücken |
| [verbesserungen.md](verbesserungen.md) | Priorisierte Arbeitspakete sowie zusätzliche Härtungs- und Qualitätsvorschläge |
| [ui-nutzerflows-2026-09-12.md](ui-nutzerflows-2026-09-12.md) | Nachträglicher Nutzerflow-Review mit Browserprüfung und zwei Luna-Agenten; acht zusätzliche Punkte, ohne Admin-Funktionen |

## Verifikation

- **2.126 Tests bestanden**, **1 fehlgeschlagen**, **10 übersprungen**:
  acht Python-Dienstsuiten, zentrale Skripte und beide Frontends.
- Beide Frontend-Builds und Typechecks erfolgreich, nach frischem `npm ci`
  in isolierten Kopien mit Node 26.3.1 und Next.js 16.3.4.
- Lint ohne Fehler; Ingest-Frontend mit sechs bestehenden Warnungen.
- Zentrales Helm-Lint/Rendering und Compose-Konfiguration erfolgreich.
- Der fehlgeschlagene Chart-Abgleich meldet zwölf nicht eingeordnete
  Variablenabweichungen. Diese sind nicht zwölf nachgewiesene Laufzeitfehler.
- `npm audit` für beide Lockfiles und `pip-audit` für neun Python-
  Requirements-Dateien einschließlich OCR-Worker: **keine bekannten
  Schwachstellen gemeldet**. Das ist ein Datenbankabgleich zum Prüfzeitpunkt,
  keine Aussage über vollständige Sicherheit.

## Vorgehen und Grenzen

Drei Subagenten mit **GPT-5.6 Luna** prüften Ingest, die weiteren Backend-
Dienste und beide Frontends parallel. Die Hauptprüfung übernahm Tests,
Deployment/CI, zusätzliche Reproduktionen und die kritische Zusammenführung.
Unbelegte oder zu breit formulierte Agentenbefunde wurden korrigiert oder
als Hypothesen eingeordnet.

Geprüft wurden Anmeldung, Sessions, ACLs, Uploads, ausgehende Requests,
Freigabe/Indexierung, Runtime/n8n/Tools, Frontend-Proxies und Konfiguration.
Der Quellcodereview war risikoorientiert und selektiv; nicht jede Zeile wurde
vollständig auditiert.

Die Tests liefen lokal mit synthetischen Daten und isolierten Testdatenbanken.
Produktionsdienste wurden weder verändert noch angegriffen. Externe Zugriffe
dienten Paketinstallation, öffentlichen Sicherheitsdatenbanken und der
Verifikation von Dokumentation. Es wurden keine Betriebsschlüssel in die
Berichte übernommen.

Nicht ausgeführt wurden ein Live-Penetrationstest, vollständige OCR- und
Realmodelltests, ein kompletter PostgreSQL/Redis/pgvector-Systemtest oder eine
frische Kubernetes-Installation. Ingest wurde lokal mit Python 3.14.6 geprüft;
der deklarierte CI-Lauf mit Python 3.13 wurde nicht wiederholt. Weitere Details
und die zehn konkreten Skip-Gründe stehen im Testprotokoll.
