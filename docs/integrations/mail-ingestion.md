# E-Mail-Dateien hochladen

E-Mails werden im Wissensportal als einzelne `.eml`-Dateien behandelt. Die frühere separate Mail-API unter `/api/v1/mail/*` ist entfernt und liefert HTTP 404. Ein automatischer Postfachabruf ist nicht Bestandteil dieses Uploadwegs.

## Verwendung

1. **Wissensbereich anlegen** oder einen vorhandenen eigenen Bereich öffnen.
2. Unter **Quelle hinzufügen → Dateien hochladen** eine `.eml`-Datei auswählen.
3. Für enthaltene unterstützte Anhänge **Standard – schnell**, **Gründlich – komplexe Dokumente** oder eine aktivierte KI-Verbindung wählen.
4. **Hochladen und verarbeiten** starten. Danach **Weitere Quellen hinzufügen** oder **Verarbeitung ansehen** wählen. Die Verarbeitung kann einige Minuten dauern.
5. Nach der Verarbeitung das vollständige Ergebnis unter **Prüfen und freigeben** prüfen. Erst die ausdrückliche Freigabe übergibt den unveränderlichen Dokumentstand zur Indexierung.

Der Nachrichtentext wird direkt aus dem RFC-822-/MIME-Dokument übernommen. Unterstützte Anhänge durchlaufen dasselbe gewählte Verarbeitungsprofil und werden als Abschnitte in das gemeinsame Markdown-Ergebnis eingefügt. Nicht unterstützte oder fehlgeschlagene Anhänge werden im Ergebnis als übersprungen gekennzeichnet. Inline-Inhalte werden nicht als eigene Dokumentaufträge angelegt. Für einen Anhang als separat verwaltbares Dokument muss dieser zusätzlich als eigene Datei hochgeladen werden.

## Technischer Ablauf

Der Browser nutzt dieselben authentifizierten Routen wie für PDF- und Office-Dateien:

- `POST /api/v1/collections/{collection_id}/upload` mit Multipart-Feld `file` (`message/rfc822`, Dateiendung `.eml`).
- `POST /api/v1/jobs/{job_id}/restart` mit `{ "profile_id": "ppocrv6_tiny_structurev3" }` startet ausschließlich den neu angelegten Auftrag.
- `GET /api/v1/portal/activity` zeigt den Fortschritt; `GET /api/v1/portal/documents/{job_id}` liefert die Vorschau.
- `POST /api/v1/portal/documents/{job_id}/release` mit dem Vorschau-Hash speichert die manuelle Freigabe.

Die normale Uploadgrößengrenze `MAX_UPLOAD_BYTES` gilt. Der EML-Konverter setzt `engine: mail-eml`; Versionierung, Eigentümer-/Teamrechte, Qualitätsprüfung und Freigabegrenzen entsprechen dem normalen Dokumentfluss. Die ehemalige Mail-API hatte zusätzliche Mail-spezifische Größen- und Detail-Endpunkte; diese gelten hier nicht.

## Bestehende Installationen

Es gibt keine Datenlöschung oder neue Migration für diese Entfernung. Vorhandene `MailMessage`-Zeilen und zugehörige Jobs bleiben erhalten; bestehende Anhang-Aufträge sind über die Auftragsverwaltung erreichbar. Das alte Mail-Postfach mit Raw-/Parts-/Export-Endpunkten steht nicht mehr zur Verfügung. Alte UI-Lesezeichen `/mail` und `/mail/{id}` leiten zur Quellenauswahl bzw. Verarbeitung weiter.
