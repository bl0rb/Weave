# Erste Schritte

Diese Seite beschreibt den Weg von einem leeren Rechner bis zur ersten Frage,
die der Chat mit Quellenangabe beantwortet. Sie ersetzt weder das
[Betriebshandbuch](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md)
noch die [Wissensportal-Dokumentation](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md)
— sie ordnet ein, in welcher Reihenfolge man beides liest und an welchen
Stellen Leute typischerweise hängenbleiben. Alle Werte, Variablennamen und
Fehlerbilder stehen im Betriebshandbuch und werden hier nicht wiederholt.

## Der Punkt, an dem die meisten hängenbleiben

**Verarbeiten bringt nichts in den Index. Erst die Freigabe tut das.**

Ein hochgeladenes und fertig verarbeitetes Dokument ist kein durchsuchbares
Dokument. Weave-Knowledge indiziert regulär ausschließlich das Ereignis
[`document.released`](https://github.com/bl0rb/Weave/blob/main/contracts/events/document.released.md);
das Ereignis
[`document.processed`](https://github.com/bl0rb/Weave/blob/main/contracts/events/document.processed.md)
wird nur mit `awaiting_release` quittiert. Wer einen Ordner hochlädt, das
Portal zuklappt und danach im Chat sucht, findet nichts — und zwar ohne
Fehlermeldung, weil technisch nichts kaputt ist. Auch die
[Symptomtabelle des Betriebshandbuchs](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest)
nennt zu „Dokumente erscheinen nicht im Index“ zuerst die fehlende Freigabe und
erst danach ein abweichendes Webhook-Secret.

Was eine Freigabe ist, wer sie auslösen darf und warum sie ein unveränderlicher
Snapshot ist: [[Wissensbereiche-und-Freigaben]].

## Was Sie vorher brauchen

- Docker mit Compose und Python für die beiden Konfigurationsbefehle.
- Die Pflicht-Geheimnisse als Umgebungsvariablen — über direnv, einen
  Passwort-Manager, einen CI-Secret-Store, wie ist gleichgültig. Fehlt eines,
  nennt `render` alle fehlenden Variablen auf einmal und bricht ab, statt eine
  halbe Datei zu schreiben. Welche das sind, steht in
  [betrieb.md, Abschnitt 11](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#11-deklarative-konfiguration-weaveyaml).
- Ausgehende Verbindungen für die Image-Pulls und, falls Sie den OCR-Worker
  starten, für die PaddleOCR-Gewichte. Fehlt dieser Ausgang, fällt die
  Verarbeitung still auf reine Textextraktion zurück
  ([README, Outbound connections](https://github.com/bl0rb/Weave/blob/main/README.md#outbound-connections)).

Ein LLM-Zugang wird für den ersten Durchlauf **nicht** gebraucht. Der Stack
startet mit Attrappen-Providern und erreicht keinen externen Dienst; siehe
Schritt 7.

## 1. Konfiguration erzeugen

[`weave.yaml`](https://github.com/bl0rb/Weave/blob/main/weave.yaml) im
Wurzelverzeichnis ist die einzige Konfigurationsquelle. Sie
enthält keine Geheimnisse im Klartext, sondern nur die Namen der
Umgebungsvariablen, aus denen die Werte gelesen werden. Die Datei
`deploy/.env` wird daraus erzeugt und nicht von Hand gepflegt.

```bash
python scripts/weave_config.py render   # schreibt deploy/.env
python scripts/weave_config.py check    # prüft die Datei auf Widersprüche
```

`check` findet genau die Fehler, die sonst erst später als unerklärliches 401
oder 503 an einer Dienstgrenze auftauchen — geteilte Werte, die auseinander
laufen, fehlende Pflichtwerte, unpassende Embedding-Dimensionen. Führen Sie
ihn nach jeder Änderung an einem Geheimnis oder einer Adresse erneut aus.

Hinweis zur Lesereihenfolge:
[Abschnitt 2](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#2-lokal-starten)
des Betriebshandbuchs zeigt noch den älteren Weg über `cp .env.example .env`.
[Abschnitt 11](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#11-deklarative-konfiguration-weaveyaml)
derselben Datei hält fest, dass Handarbeit an der `.env` vollständig entfällt —
dieser Weg ist der aktuelle.

## 2. Stack starten

```bash
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.weave.yml \
  -f deploy/docker-compose.local.yml \
  up -d --build
```

Die Compose-Datei allein zeigt auf Registry-Images, die nie veröffentlicht
wurden; das lokale Override baut die Dienste aus diesem Monorepo. Deshalb
immer beide Dateien angeben.

Danach ist das Wissensportal unter `http://localhost:3000` erreichbar und die
Chat-Oberfläche unter `http://localhost:3001`. Ein Punkt, der spätestens beim
Veröffentlichen hinter einem Reverse Proxy zählt: Die Portal-Oberfläche leitet
Anfragen **nicht** weiter, der Browser spricht die Ingest-API direkt an. Port
3000 und Port 8000 müssen also beide vom Arbeitsplatz aus erreichbar sein.

Ist Port 3000 belegt, setzen Sie `FRONTEND_PORT` **und** `CORS_ORIGINS`
gemeinsam, bevor Sie neu rendern. Wer nur den Port verschiebt, bekommt im
Browser einen Access-Control-Fehler und hält daraufhin CORS für das Problem —
tatsächlich ist es der Port. `CORS_ORIGINS` steuert nämlich nicht nur CORS,
sondern zugleich die Herkunftsprüfung gegen CSRF, an der dann jede schreibende
Anfrage scheitert, die Anmeldung eingeschlossen. `check` meldet diesen
Widerspruch.

## 3. OCR-Worker starten

Derselbe Aufruf noch einmal, nur endet er auf
`--profile ocr up -d weave-ingest-worker` statt auf `up -d --build`.

Der Worker bringt PaddleOCR mit und ist mehrere Gigabyte groß. Er hängt hinter
einem eigenen Profil, damit der übrige Stack in Minuten hochkommt. Ohne ihn
bleiben hochgeladene Aufträge in `PENDING` stehen — auch das ohne
Fehlermeldung.

## 4. Ersten Administrator anlegen

Öffnen Sie das Portal und legen Sie über die Setup-Seite (`/setup`) das erste
Konto an. Der erste Nutzer wird automatisch Administrator. Wer das lieber
unbeaufsichtigt erledigt, setzt vor dem Rendern die Bootstrap-Variablen für
Benutzername, E-Mail und Passwort; Ingest legt den ersten Administrator dann
nach den Migrationen selbst an, sofern noch kein Benutzer existiert
([betrieb.md, Abschnitt 3](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#3-erstinbetriebnahme)).
Weitere Nutzer, Gruppen und Anmeldeverfahren verwaltet dieser Administrator
anschließend unter **Administration → Konten & Zugriffe**; Weave-Ingest ist der
Identitätsanbieter der Plattform
([ADR-0006](https://github.com/bl0rb/Weave/blob/main/docs/adr/0006-foederierte-anmeldung.md)).
Wer sich hier anmelden kann, kann auch chatten — eine zweite Benutzerverwaltung
im Gateway gibt es nicht. Wie Gruppen, Wissensbereiche und Sichtbarkeit
zusammenhängen, steht in [[Rechte-und-Sichtbarkeit]].

## 5. Der Registry-Sync: ein Service-Credential, kein persönlicher Token

Weave-Knowledge spiegelt die Liste der Wissensbereiche und ihrer Leserechte
aus Weave-Ingest. Dafür braucht es `WEAVE_KNOWLEDGE_INGEST_API_TOKEN` — ein
dediziertes Service-Credential, das Sie wie jedes andere Geheimnis **vor** dem
Rendern erzeugen (etwa mit `openssl rand -hex 32`). `render` spiegelt den Wert
auf beide Seiten: Ingest kennt ihn als `KNOWLEDGE_INGEST_API_TOKEN`, Knowledge
im Container als `WEAVE_INGEST_API_TOKEN`. Das Credential darf ausschließlich
die Registry lesen und freigegebene Snapshots holen, keine Benutzer verwalten
und keine Entwürfe sehen.

Frühere Versionen verlangten an dieser Stelle einen persönlichen API-Token
eines Administrators, der erst nach Schritt 4 existieren konnte — das war der
einzige zyklische Punkt der Erstinbetriebnahme. Er ist entfallen; alte
Admin-Tokens, die noch als Service-Secret dienen, sollten nach der Umstellung
widerrufen werden.

Bleiben die Wissensbereiche in Chat und Suche trotzdem leer, laufen die beiden
Seiten des Werts auseinander (Knowledge-Log zeigt 401) — `check` findet genau
das. Geänderte `.env`-Werte gelangen erst durch `up -d` in die Container, ein
`restart` übernimmt sie nicht.

## 6. Wissensbereich anlegen, Quelle hinzufügen, freigeben

Ab hier arbeiten Sie nur noch in der Oberfläche. Der Ablauf ist im
Wissensportal geführt: Wissensbereich anlegen, dabei die berechtigten Gruppen
wählen, dann Quellen hinzufügen — Dateien (auch einzelne `.eml`-E-Mails und
`.vtt`-Meeting-Transkripte), eine Confluence-Startseite, eine Website oder
Intranetseite oder ein Eintrag aus dem Quellenkatalog — und ein
Verarbeitungsprofil auswählen.

![Quellen hinzufügen: Dateien hochladen, Confluence-Seite auswählen, Verarbeitungsprofil wählen](https://raw.githubusercontent.com/bl0rb/Weave/main/docs/screenshots/user-wiki/04-add-source.png)

Wenn die Verarbeitung durch ist, steht das Dokument unter **Dokumente** in
der Voreinstellung **Zu prüfen**; die Seitenleiste zählt die wartenden
Dokumente mit. Lesen Sie den aufbereiteten Text in der Prüfansicht; reicht die
Qualität nicht, wählen Sie **Erneut verarbeiten** mit einem anderen Profil.
Erst die ausdrückliche Bestätigung speichert die Freigabe. Danach läuft die Anzeige selbsttätig durch
**Freigegeben → Wird indiziert → Für KI verfügbar** und nennt am Ende Zeitpunkt
und Anzahl der durchsuchbaren Textabschnitte.

![Prüfansicht: aufbereiteter Text, Freigabe eines geprüften Standes und die Anzeige der KI-Verfügbarkeit](https://raw.githubusercontent.com/bl0rb/Weave/main/docs/screenshots/user-wiki/18-review-detail.png)

Warten Sie diesen letzten Zustand ab, bevor Sie in den Chat wechseln.
„Verarbeitet“ heißt weder „freigegeben“ noch „indiziert“, und der
Zustellstatus eines Webhooks sagt nichts über den Abschluss der Indexierung.
Die Details zu Zuständen, Berechtigungen und Sonderfällen wie Confluence
stehen in [[Wissensbereiche-und-Freigaben]].

## 7. Die erste Frage stellen

Der Chat ist eine eigene Oberfläche, die aus dem Portal heraus verlinkt ist,
und er nutzt dieselbe Anmeldung. Wählen Sie einen Bot, stellen Sie die Frage,
und schränken Sie das Gespräch bei Bedarf auf einzelne Ihrer Wissensbereiche
ein. **Quellen anzeigen** unter der Antwort öffnet die zitierten Belege.
Dieselben Fragen lassen sich auch aus der Desktop-App oder per MCP aus VS Code
stellen — siehe [[Chat-und-Bots]].

![Chat-Oberfläche mit Bot-Auswahl und laufendem Gespräch](https://raw.githubusercontent.com/bl0rb/Weave/main/docs/screenshots/user-wiki/31-chat.png)

Zwei Erwartungen sind hier wichtig:

- **Findet die Suche nichts, sagt der Bot das.** Er formuliert keine flüssige,
  unbelegte Antwort. Eine leere Antwort ist bei frisch aufgesetztem Stack fast
  immer ein Hinweis auf eine fehlende Freigabe oder eine fehlende Gruppe,
  nicht auf einen Defekt.
- **Antworten, die mit `[fake-llm]` beginnen, sind korrekt und erwartbar.**
  `LLM_PROVIDER` steht bis zur Umstellung auf `fake`, ebenso arbeiten
  Embeddings und Reranking zunächst mit Attrappen. Der erste Durchlauf zeigt
  damit den vollständigen Weg vom Dokument bis zur Antwort, ohne dass Daten
  oder Budget das Haus verlassen. Wie man auf echte Modelle umstellt — und
  warum eine Umstellung der Embeddings bei bereits gefüllter Datenbank eine
  Migration und ein Reindex nach sich zieht — steht in
  [Abschnitt 7 des Betriebshandbuchs](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#7-echte-provider).

Bleibt eine Antwort aus, ist der
[Selbsttest in Abschnitt 10](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md#10-selbsttest)
der schnellste Weg: Er prüft der Reihe nach Erreichbarkeit,
Bot-Liste, lesbare Collections und eine echte Antwort samt `trace`, und die
zugehörige Symptomtabelle nennt zu jedem Fehlerbild die wahrscheinliche
Ursache. Die häufigsten Fälle sind auch in [[Haeufige-Fragen]] eingeordnet.

## Bildstrecke zum Ablauf

Unter
[docs/screenshots/user-wiki/](https://github.com/bl0rb/Weave/tree/main/docs/screenshots/user-wiki)
liegen 31 Screenshots der aktuellen Oberfläche, geordnet nach Anmeldung und
Wissensablauf, persönlichen Einstellungen und Chat, Verbindungen und
Fachwerkzeugen sowie Administration. Die
[Galerie](https://github.com/bl0rb/Weave/blob/main/docs/screenshots/user-wiki/index.html)
zeigt sie zusammen mit einer Beispielanimation, die den fachlichen Teil dieser
Seite in fünf Schritten durchläuft: anmelden, Quelle zu einem Wissensbereich
hinzufügen, Verarbeitung verfolgen, prüfen und freigeben, im Chat fragen.

Die Animation ist ausdrücklich eine Demonstration mit simulierten
Oberflächenzuständen und erfundenen Beispieldaten; die gezeigte Chat-Antwort
ist als Attrappe gekennzeichnet, und die Zeitverhältnisse entsprechen weder
echter OCR- noch Indexierungs- oder Modelllaufzeit. Die Einzelbilder dagegen
sind echte Browser-Renderings der laufenden Anwendung mit ersetzten Daten.
Herkunft und Aufnahmeverfahren stehen im
[README der Bildstrecke](https://github.com/bl0rb/Weave/blob/main/docs/screenshots/user-wiki/README.md).

## Bevor daraus mehr als eine Testinstallation wird

Der oben beschriebene Stand ist eine lauffähige Bewertungsumgebung, kein
Betriebszustand. Drei Dinge sind vorher zu klären, und alle drei sind in der
ausgelieferten Konfiguration bewusst offen gelassen:

- Der Stack veröffentlicht zehn Host-Ports, aber nur fünf davon sind für
  jemanden außerhalb des Stacks gedacht. Kein Eintrag bindet an eine bestimmte
  Adresse.
- Es gibt an keiner Stelle TLS — weder Reverse Proxy noch Ingress noch
  Zertifikatsverwaltung liegen bei.
- Die Modelle sind Attrappen, solange sie nicht umgestellt sind.

Welche Ports das im Einzelnen sind, welcher Dienst welchen Token verlangt und
wie die Umstellung auf echte Modelle abläuft: [[Betrieb]] ordnet es ein,
[„Network design“ in der README](https://github.com/bl0rb/Weave/blob/main/README.md#network-design)
und die Abschnitte 6 bis 10 des
[Betriebshandbuchs](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md)
sind die maßgeblichen Fassungen.

## Weiterlesen

- [[Wissensbereiche-und-Freigaben]] — warum Verarbeiten und Veröffentlichen
  getrennt sind und was eine Freigabe genau festschreibt
- [[Rechte-und-Sichtbarkeit]] — Gruppen, Wissensbereiche und warum die Rechte
  Teil der Suchanfrage sind
- [[Was-ist-neu]] — was sich seit v0.7.1 geändert hat
- [[Chat-und-Bots]] — Bots, Quellenangaben und der Umgang mit leeren Treffern
- [[Betrieb]] — Netzaufbau, Tokens, Modelldienste
