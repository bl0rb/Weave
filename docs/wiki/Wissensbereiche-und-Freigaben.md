# Wissensbereiche und Freigaben

Wissensbereiche und Freigaben sind das zentrale Konzept von Weave. Wer beides
verstanden hat, versteht auch, warum ein hochgeladenes Dokument nicht sofort im
Chat auftaucht — und warum das so gewollt ist.

Diese Seite ordnet ein. Die maßgeblichen Beschreibungen stehen im Repository:
[docs/wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md)
für den Ablauf im Portal, die Verträge unter
[contracts/](https://github.com/bl0rb/Weave/tree/main/contracts) für das,
was zwischen den Diensten tatsächlich zugesichert ist.

## Was ein Wissensbereich ist

Ein Wissensbereich ist eine benannte Sammlung von Dokumenten mit einer Liste von
Gruppen, die sie lesen dürfen. Technisch heißt dasselbe Objekt *Collection*; in
der Oberfläche von Weave-Ingest heißt es *Wissensbereich*. Beide Begriffe meinen
das Gleiche, sie tauchen nur in unterschiedlichen Schichten auf — in den
Verträgen, den API-Pfaden und den Chunk-Metadaten steht die Collection, im
Portal der Wissensbereich. Dasselbe gilt für die Gruppen: das Portal nennt sie
seit v0.7.3 so, in API-Feldern und Verträgen heißen sie weiterhin `team`.

Ein Wissensbereich ist kein Ordner. Er ist die Einheit, in der Zugriff vergeben
wird und auf die eine Chat-Anfrage eingeschränkt werden kann. Zwei Eigenschaften
folgen daraus und erklären das meiste:

- Der **Slug** ist die stabile Kennung. Er wandert bis in die Metadaten jedes
  einzelnen Textabschnitts, damit die Suche danach filtern kann.
- Die **Leseberechtigung** (`read_teams`) wandert bewusst *nicht* mit in den
  Index. Sie liegt in einer Registry, die Weave-Knowledge von Weave-Ingest
  spiegelt. Deshalb kostet ein Rechteentzug kein Neuindizieren und wirkt sofort.
  Details dazu in [[Rechte-und-Sichtbarkeit]] sowie im Vertrag
  [collection.updated](https://github.com/bl0rb/Weave/blob/main/contracts/events/collection.updated.md).

Beim Anlegen sind die Mitglieder der eigenen Gruppe als Berechtigte
vorausgewählt. Zugriff für alle Gruppen ist möglich, verlangt aber eine
ausdrückliche Bestätigung — eine bewusste Reibung an genau der Stelle, an der
ein Klick den Kreis der Mitlesenden am stärksten vergrößert. Seit ADR-0008 hat
ein Wissensbereich außerdem einen Eigentümer, der ihn pflegt, und Bots können
ihrerseits an Personen und Gruppen freigegeben sein
([ADR-0008](https://github.com/bl0rb/Weave/blob/main/docs/adr/0008-besitz-freigaben-metadaten.md)).

Der geöffnete Wissensbereich ist seit v0.7.14 eine Verwaltungsseite: seine
Quellen mit Herkunft, Umfang, Zeitplan und letztem erfolgreichen Abruf, ein
„Jetzt synchronisieren“ für die Confluence- und Website-Importe des Bereichs,
der Zugriff, die Wartung (neu indizieren, ZIP-Export) und ein Link auf die
gefilterte Dokumentliste unter **Dokumente**.

Als Quellen kommen in Frage: hochgeladene Dateien (auch `.eml`-E-Mails und
`.vtt`-Meeting-Transkripte), Confluence-Seiten samt Unterseiten, Websites und
Intranetseiten sowie Einträge aus dem Quellenkatalog — kuratierte Vorlagen wie
einzelne Gesetze, die man in einen eigenen Bereich übernimmt. Für Websites
gelten zwei Grenzen, die man kennen sollte: Links auf andere Hosts werden nie
verfolgt, und Adressen im privaten Netz bleiben gesperrt, bis ein Administrator
sie an genau dieser Verbindung freigibt. Alles Weitere steht in
[wissensportal.md, „Websites, Intranet und Quellenkatalog“](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#websites-intranet-und-quellenkatalog).

Ein Wissensbereich wird angelegt, *bevor* die Quelle hinzugefügt wird. Für
Dokumente, die ohne Wissensbereich verarbeitet wurden, sieht diese Ausbaustufe
keine nachträgliche Zuordnung vor: die Quelle wird im gewünschten Bereich erneut
hinzugefügt.

## Warum Verarbeiten und Veröffentlichen getrennt sind

Verarbeiten ist eine Maschinenleistung: OCR, Extraktion, Markdown-Rendering,
Qualitätsprüfung. Das Ergebnis ist ein Entwurf. Ob dieser Entwurf den
Originalinhalt korrekt wiedergibt und ob er einer ganzen Gruppe zur Verfügung
stehen soll, sind zwei Fragen, die keine Pipeline beantworten kann.

Deshalb trennt das Portal beides. Verarbeitet, freigegeben und indexiert sind
drei verschiedene Zustände; die Seite **Verarbeitung** sagt das ausdrücklich.
Ohne diese Trennung wäre jeder erfolgreich verarbeitete Upload unmittelbar für
alle Leseberechtigten des Bereichs auffindbar — einschließlich schlecht erkannter
Seiten und einschließlich Material, das jemand nur schnell durch die Konvertierung
schicken wollte.

Entscheidend ist, dass die Trennung nicht nur eine Bildschirmmaske ist, sondern im
Protokoll zwischen den Diensten steht. Weave-Knowledge indexiert regulär
ausschließlich `document.released`. Ein signiertes `document.processed` wird mit
`200 {"status":"awaiting_release"}` quittiert — und zwar so, dass dabei weder ein
Ereignis noch ein Dokument noch ein Index-Auftrag entsteht. Ein Bedienfehler, eine
falsch verdrahtete Webhook-Verbindung oder ein zu großzügig konfigurierter
Konsument kann unfreigegebenes Material also nicht versehentlich veröffentlichen.

Die beiden Events haben auch unterschiedliche Wege: `document.processed` ist eine
Benachrichtigung an benutzerverwaltete Webhook-Verbindungen und wird nur für
Aufträge ausgelöst, an denen eine solche Verbindung ausdrücklich hinterlegt ist.
`document.released` läuft über den internen Veröffentlichungskanal von
Weave-Ingest; dafür ist keine Webhook-Verbindung in der Oberfläche nötig.

## Was bei einer Freigabe passiert

Eine Freigabe ist ein unveränderlicher Snapshot. Pro Auftrag gibt es genau eine.
Auslösen darf sie nur der Dokument- oder Wissensbereichseigentümer oder ein
Administrator; ein Qualitätsblock verweigert sie; bei Confluence muss der gesamte
Import abgeschlossen sein, damit Links und Hierarchiemetadaten vollständig sind.

Mit der Bestätigung passiert Folgendes:

1. Der geprüfte Markdown-Stand wird mit serverseitig canonicalisiertem Frontmatter
   festgeschrieben und bleibt danach byteweise unverändert.
2. Weave-Ingest sendet das signierte `document.released` mit `release_id` und
   `markdown_sha256`, dem SHA-256 genau dieses Snapshots.
3. Weave-Knowledge holt den Snapshot über die aus der eigenen, vertrauenswürdigen
   Basisadresse gebaute Download-Route und prüft den Hash, **bevor** gechunkt
   wird. Weicht er ab, wird nicht indexiert.
4. Dedupliziert wird über `release:<release_id>`. Eine erneute Zustellung
   indexiert nicht neu; eine zweite, abweichende Freigabe für denselben Auftrag
   wird konservativ abgewiesen.

Die vollständigen Feld- und Fehlerdefinitionen stehen im Vertrag
[document.released](https://github.com/bl0rb/Weave/blob/main/contracts/events/document.released.md).

Aus der Unveränderlichkeit folgt der Rest des Verhaltens, das im Alltag
gelegentlich überrascht:

- Ein freigegebener Auftrag lässt sich nicht mehr bearbeiten oder neu verarbeiten
  (HTTP 409). Geänderter Inhalt braucht eine neue Dokumentversion.
- Ein freigegebener Auftrag lässt sich auch nicht einfach löschen (ebenfalls
  409). Den Weg zurück gibt es nur als nachvollziehbare **Rücknahme**: Seiten,
  die ein Confluence- oder Website-Sync nicht mehr findet, kann der Eigentümer
  im Laufdetail mit **Aus Wissen entfernen** zurückziehen; Dokumente, deren
  Auftrag oder Bereich nicht mehr existiert, zieht ein Admin-Job zurück.
  Knowledge löscht dann Text und Textabschnitte und behält nur den
  Widerrufsbeleg, damit ein verspätetes Freigabe-Ereignis nichts
  wiederherstellt. Importhistorie und Freigabe-Snapshot bleiben erhalten.
- **Neu indizieren** ist etwas anderes als neu verarbeiten: es liefert
  denselben freigegebenen Stand noch einmal an Knowledge aus und erzwingt dort
  den Ersatz der gespeicherten Textabschnitte — nach einem Embedding-Wechsel
  oder wenn der Index sichtbar veraltet ist. Für den ganzen Bereich gibt es
  **Wissensbereich neu indizieren** in dessen Wartung.
- Jeder Export eines freigegebenen Dokuments, einzeln oder als ZIP, liefert den
  Snapshot. Spätere lokale Änderungen können ihn nicht ersetzen. Der Export
  ändert weder Freigabe noch Indexierungsstatus.

Vor der Freigabe ist dagegen alles offen: Reicht die Qualität nicht,
**Erneut verarbeiten** mit einem anderen Profil wählen. Das ersetzt den
Entwurf, erzeugt aber keine Freigabe — geprüft und bestätigt wird anschließend
erneut. Die Prüfansicht markiert seit v0.7.15 erkannte Schwächen im Text und
holt auf Wunsch eine Empfehlung vom Chat-Provider; wer noch nicht entscheiden
will, **parkt** das Dokument, wer es nicht in den Index will, markiert es mit
**Nicht freigeben**.

## Warum `document.processed` trotzdem existiert

`document.processed` ist kein Übergangsartefakt. Es trägt Frontmatter, die
Qualitätssignale und einen Verweis auf das Markdown und geht an die
benutzerverwaltete Webhook-Verbindung, die am jeweiligen Auftrag hinterlegt ist —
also an Konsumenten, die über ein fertig verarbeitetes Dokument Bescheid wissen
wollen, ohne es zu veröffentlichen.

Nur für den Index taugt es nicht, und zwar aus einem Grund, der nichts mit
Bequemlichkeit zu tun hat: Der Index ist ein Schreibzugriff mit Reichweite. Was
dort steht, gilt der Suche als wahr und kann von jedem gefunden werden, der den
Bereich lesen darf. Ein solcher Schritt hängt an einer benannten Person, die einen
konkreten Stand angesehen und bestätigt hat — nicht daran, dass eine Pipeline
fertig geworden ist.

Der Vertrag zu
[document.processed](https://github.com/bl0rb/Weave/blob/main/contracts/events/document.processed.md)
beschreibt Auslösebedingungen, Payload, Signaturverfahren und Idempotenz im Detail.

## Was die Statusanzeigen bedeuten

Ein Fachanwender sieht drei Statusarten, die leicht verwechselt werden, weil sie
ähnlich klingen. Sie beantworten unterschiedliche Fragen:

**Verarbeitungsstatus** — wartend, laufend, verarbeitet, fehlgeschlagen. Beantwortet
nur: Ist die Pipeline fertig? „Verarbeitet“ bedeutet weder „freigegeben“ noch
„indexiert“.

**Zustellstatus** — `pending`, `sent`, `failed`. Beantwortet nur: Wurde der Webhook
angenommen? `sent` heißt, dass die Gegenstelle die Zustellung bestätigt hat, und
sonst nichts. Es ist keine Aussage über den Index.

**Indexierungsstatus** — die Kette **Freigegeben → Wird indiziert → Für KI
verfügbar**. Sie erscheint im Dokument, in der Dokumentliste unter
**Dokumente** und unter **Verarbeitung**. „Für KI verfügbar“ erscheint erst, wenn Weave-Knowledge
genau diese Freigabe samt ihrem Snapshot-Hash bestätigt und die Textabschnitte mit
ihren Embeddings vollständig gespeichert sind; dann stehen auch Abschlusszeit und
Anzahl der Textabschnitte dabei. Leere, ersetzte und fehlerhafte Indexstände werden
gesondert angezeigt.

Zwei Anzeigen werden regelmäßig missverstanden:

- **Indexstatus nicht verfügbar** ist weder Erfolg noch Fehlschlag. Es bedeutet,
  dass gerade keine belastbare Rückmeldung vorliegt — etwa bei Zeitüberschreitung
  oder fehlender Konfiguration. Seit v0.7.3 überbrückt das Portal kurze
  Aussetzer von Knowledge: bis zu fünf Minuten zeigt es den zuletzt bestätigten
  Stand mit Zeitstempel, danach ein neutrales „Status wird aktualisiert“, und
  erst nach zehn Minuten die Warnung. Eine ältere grüne Bestätigung bleibt
  darüber hinaus bewusst nicht stehen: die Anzeige zeigt lieber Unwissen als
  eine Zusage, die sie im Moment nicht prüfen kann.
- **Für KI verfügbar** heißt: dieser Snapshot ist aktiv indexiert. Es ist keine
  Zusage, dass jeder Bot ihn auch verwendet. Was ein Chat tatsächlich durchsucht,
  hängt weiterhin an Berechtigungen, Bot-Konfiguration und der Frage selbst — siehe
  [[Chat-und-Bots]].

Die Anzeige aktualisiert sich bei ausstehender Indexierung alle fünf Sekunden, bei
stabilen Zuständen alle 30 Sekunden; Hintergrund-Tabs pausieren und prüfen beim
Öffnen erneut. Die einzelnen Zustände, die Weave-Knowledge zurückmeldet, und ihre
Abbildung im Portal stehen vollständig im Vertrag
[indexing-status.md](https://github.com/bl0rb/Weave/blob/main/contracts/indexing-status.md).

## Dokumente ohne Wissensbereich (Altbestand)

Was Weave-Knowledge vor den Wissensbereichen oder über den alten
`document.processed`-Weg indiziert hat, trägt keinen Bereich und hat damit keine
Freigaben. Ein Bot findet solche Dokumente nur, wenn er sie ausdrücklich
einbezieht („Dokumente ohne Wissensbereich einbeziehen“, in YAML
`include_uncollected`), begrenzt auf die Hauptgruppe des Hochladenden. Seit v0.7.1
ist das für **neue** Bots aus; bestehende Bots und YAML-Bots ohne eigene Angabe
beziehen sie weiter ein, damit nach dem Update keine Antworten wegfallen.

Der Betreiber kann den Altbestand einmal nachträglich zuordnen
(`reconcile-collections`, siehe [[Betrieb]]). Danach gelten für diese Dokumente
die Freigaben ihres Bereichs.

## Grenzen dieser Ausbaustufe

Nicht implementiert sind bisher: vollständiges ACL-Mapping, das Zurückziehen
eines beliebigen einzelnen freigegebenen Dokuments aus der Oberfläche (die
Rücknahme oben gilt für verschwundene Confluence- und Website-Seiten und für
verwaiste Dokumente) und das Umschalten zwischen publizierten Versionen. Alte
Aufträge ohne Bereich ordnet nur der einmalige Abgleich zu (siehe oben).
Individuelle Confluence-Seitenrechte werden nicht automatisch übernommen, und
neue Confluence- oder Website-Dokumentversionen müssen erneut geprüft und
freigegeben werden.

Zu Confluence noch ein Punkt, der sich mit v0.7.7 geändert hat: Ein Import ist
jetzt eine eigene Konfiguration aus Umfang, Ziel-Wissensbereich, Optionen und
Zeitplan, und eine Verbindung kann mehrere davon haben. Vorher hatte eine
Verbindung genau einen Auto-Sync, der ihren jüngsten Lauf wiederholte — ein
zweiter Import änderte damit still, was synchronisiert wurde. Beim Hinzufügen
einer Confluence-Quelle ist die tägliche Synchronisation vorausgewählt;
abgebrochene Läufe nehmen seit v0.7.12 von selbst wieder auf
([wissensportal.md, „Confluence synchronisieren“](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md#confluence-synchronisieren-und-fehlende-seiten-entfernen)).

## Weiterlesen

- [[Erste-Schritte]] — der Ablauf von der Anmeldung bis zur ersten Freigabe
- [[Rechte-und-Sichtbarkeit]] — wer welchen Wissensbereich sieht, und warum ein
  Entzug sofort wirkt
- [docs/wissensportal.md](https://github.com/bl0rb/Weave/blob/main/docs/wissensportal.md)
  — maßgeblicher Ablauf, Profilauswahl, Administration, Betriebskonfiguration
- [docs/betrieb.md](https://github.com/bl0rb/Weave/blob/main/docs/betrieb.md)
  — Abschnitt 3 erklärt, warum die erste Freigabe zur Erstinbetriebnahme gehört
- [docs/glossar.html](https://github.com/bl0rb/Weave/blob/main/docs/glossar.html)
  — die Begriffe einzeln, jeweils allgemein und in ihrer Weave-Rolle
