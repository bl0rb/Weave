# ADR 0007: Zentrale Chat-Provider-Konfiguration in Weave-Ingest

**Status:** angenommen

**Datum:** 2026-09-02

## Kontext

Administratoren verwalten Nutzer, OIDC, Teams, Wissensbereiche und Dokument-KI bereits in Weave-Ingest. Der OpenAI-kompatible Chat-Provider lag dagegen als Umgebungsvariable in Weave-Runtime. Eine Änderung erforderte Deploymentzugriff und einen Neustart; mehrere Runtime-Replikate konnten dabei auseinanderlaufen.

## Entscheidung

Weave-Ingest besitzt eine einzelne zentrale Chat-Provider-Konfiguration mit Endpoint, Modell, optionalem API-Key, Timeout und optionaler Temperatur. Der API-Key wird mit Fernet und einem eigenen HKDF-Zweck verschlüsselt. Die Admin-Oberfläche zeigt nur, ob ein Key vorhanden ist.

Weave-Runtime liest vor jedem direkten, nicht an n8n delegierten Chat-Turn einen frischen Snapshot über eine interne Bearer-geschützte Schnittstelle. Dadurch bleibt Runtime zustandslos und horizontal skalierbar; eine Änderung gilt ohne Cache-Invalidierung oder Pod-Neustart ab der nächsten Anfrage. Ist die zentrale Kette konfiguriert, aber nicht erreichbar oder ungültig, schlägt der Turn geschlossen fehl. Ohne konfigurierte Kette bleibt der bisherige Umgebungs-/YAML-Pfad für isolierte Entwicklung erhalten.

n8n-Flows sind nicht betroffen. Sie führen ihren Agentenlauf außerhalb der Runtime aus und verwalten ihr dort eingesetztes Modell in n8n. Collection- und Bot-Berechtigungen werden weiterhin vor jeder Delegation aufgelöst und im Delegations-Token begrenzt.

## Konsequenzen

- Providerwechsel benötigen keinen Runtime-Rollout.
- Alle Runtime-Replikate verwenden ab dem nächsten Turn denselben DB-Stand.
- `CHAT_CONFIG_SERVICE_TOKEN` ist ein neues geteiltes Secret zwischen Ingest und Runtime und muss in Kubernetes aus derselben Secret-Quelle stammen.
- Pro direktem Turn entsteht ein kleiner interner HTTP-Aufruf zu Ingest.
- Für private Providerziele pflegt der Betreiber `CHAT_LLM_PRIVATE_HOST_ALLOWLIST`; der Verbindungstest bleibt über den geschützten `safe_fetch`-Pfad an SSRF- und Redirect-Prüfungen gebunden.

## Alternativen

- **Nur Runtime-Umgebungsvariablen:** robust, aber keine Admin-Selbstbedienung und Änderungen brauchen einen Rollout.
- **Lokale Datei in Runtime schreiben:** verwirft Zustandslosigkeit und driftet bei mehreren Pods.
- **Eigene Konfigurationsdatenbank für Runtime:** unnötige zweite Verwaltungsoberfläche und zusätzliche Zustandskopplung.
