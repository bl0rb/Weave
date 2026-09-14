# UI-Nutzerflows · Nachprüfung vom 12.09.2026

Ergänzung zum Review vom 10.09.2026, ausschließlich für normale
Nutzerabläufe. Quellcodestand: `7333479cbcff5395797fc93de66dc38a6ccb8f60`.
Zwei Agenten mit GPT-5.6 Luna prüften getrennt Import/Dokumente und
Navigation/Chat. Die Hauptprüfung glich ihre Hinweise mit dem Code ab und
bediente das laufende Portal unter `http://localhost:3002` im Browser.

Das vom Nutzer angemeldete Konto hatte die Rolle **Admin**. Geprüft wurden
nur Nutzerseiten; Administration und administrative Verbindungsbereiche
wurden nicht geprüft. Diese Sitzung belegt keine korrekte Rollenabgrenzung
für reine Mitglieder. Der Buildstand der laufenden Container wurde nicht
mit dem Quellcodestand abgeglichen.

## Ergebnis und Umfang

**6/10 für die geprüften Nutzerabläufe:** Einstieg, Quellenformular und
Freigabestatus sind überwiegend verständlich. Wechsel in ältere technische
Seiten, verlorene Eingaben und der hängen gebliebene Chat-Übergang unterbrechen
den Arbeitsfluss. Acht zusätzliche Punkte: vier Browserbeobachtungen und
vier Codebefunde. P1 bedeutet wichtig für den Arbeitsablauf, P2 nachrangig.
Die drei bereits gemeldeten Punkte UI-01 bis UI-03 bleiben separat erhalten.

| Geprüfter Ablauf | Ergebnis |
|---|---|
| Übersicht → Quelle hinzufügen | Formular mit Wissensbereich, Quelle und Profil erreichbar |
| Confluence → Verbindung prüfen → Browser zurück | Eingaben verloren; UI-04 |
| Verarbeitung → Dokument ohne Wissensbereich | Angekündigte Zuordnung nicht erreichbar; UI-05 |
| Prüfung → Alle Dokumente → freigegebener Stand | Bestehender Stand und KI-Verfügbarkeit verständlich sichtbar |
| Konto und Confluence-Verbindungen | Sprach-/Begriffsbruch; UI-06 |
| Chat → Mit Weave anmelden | Anhaltender Ladezustand, auch nach Neuladen; UI-07 |

Keine Dokumente verändert, veröffentlicht oder gelöscht, keine Tokens oder
Verbindungen angelegt und keine externen Confluence-Importe gestartet.
Die für UI-04 eingegebene Beispiel-URL wurde nicht abgesendet. Vorhandene
Dokumentinhalte und Zugangsdaten werden hier nicht wiedergegeben.

## Browserbeobachtungen

### UI-04 · P1 · Umweg zur Verbindungseinrichtung verwirft den Importentwurf

**Status (13.09.2026):** ✅ Umgesetzt. Der Entwurf wird zusätzlich in
`sessionStorage` gehalten und beim Rückweg wiederhergestellt
([source-form.tsx](../../../services/ingest/frontend/src/components/portal/source-form.tsx)).

**Reproduktion:** „Quelle hinzufügen“ → „Confluence verbinden“ → eine
Beispiel-Seiten-URL eingeben → „Verbindung einrichten oder prüfen“ → Browser
zurück. Jetzt ist wieder „Dateien hochladen“ ausgewählt. Nach erneutem
Wechsel zu Confluence ist die URL leer. Keine Warnung vor dem Verlust.

**Codebeleg:** [source-form.tsx](../../../services/ingest/frontend/src/components/portal/source-form.tsx),
Zeilen 18–28: Entwurf nur in lokalem Komponenten-State; Zeile 115:
Navigation zu `/connections`.

**Änderung / Abnahme:** Entwurf beim Verbindungsumweg erhalten oder Verbindung
direkt im Formular einrichten. Nach der Rückkehr bleiben Wissensbereich,
Quelltyp, URL und Optionen erhalten; eine neue Verbindung ist auswählbar.
Ergänzt UI-03 um den im Browser bestätigten Verlust bestehender Eingaben.

### UI-05 · P1 · Dokument ohne Wissensbereich endet ohne Zuordnungsmöglichkeit

**Status (13.09.2026):** ✅ Umgesetzt. Die Jobdetailseite zeigt bei fehlendem
Wissensbereich jetzt einen Hinweis mit Link zum Neuimport; die technischen
Verarbeitungsdetails stehen hinter `<details>` (Jobdetail).

**Reproduktion:** Unter „Verarbeitung“ einen verarbeiteten Auftrag mit
„Ohne Wissensbereich“ öffnen. Der Hinweis fordert dazu auf, die Quelle einem
Wissensbereich hinzuzufügen. Die Detailseite bietet Downloads, Markdown-Editor
und erneute Verarbeitung, aber keine Zuordnungsaktion und keinen passenden
Weiter-Link. Stattdessen dominieren „Job Details“ und technisches Laufzeit-JSON.

**Codebeleg:** [activity.tsx](../../../services/ingest/frontend/src/components/portal/activity.tsx),
Zeilen 93–95; [Jobdetail](../../../services/ingest/frontend/src/app/jobs/[id]/page.tsx),
ab Zeile 448, insbesondere „Processing Info“ ab Zeile 573.

**Änderung / Abnahme:** Auf der Detailseite einen erreichbaren Weg zum
Wissensbereich anbieten. Wenn eine nachträgliche Zuordnung fachlich nicht
unterstützt wird, den notwendigen Neuimport ausdrücklich erklären und
verlinken. Der Nutzer kann den angekündigten nächsten Schritt ausführen;
technische Diagnosen stehen hinter einer aufklappbaren Detailansicht.

### UI-06 · P2 · Nutzerseiten wechseln Sprache und Bedeutung der Navigation

**Status (13.09.2026):** Teilweise umgesetzt. Konto (jetzt „API-Zugriff“) und
der gesamte Confluence-Verbindungsablauf sind vollständig deutsch benannt.
Die Jobdetailseite trägt weiterhin englische Feldlabels (Filename, Status,
Created, Download-Buttons usw.) über die übersetzte Überschrift hinaus.

**Reproduktion:** „Konto“ öffnet „Settings“ mit ausschließlich „API tokens“
und einer Erklärung zu `Authorization: Bearer`. Im Confluence-Ablauf erscheinen
„Connections“, „Add connection“ und „Processing > Confluence Import“, obwohl
die umgebende Navigation deutsch ist. Auch das Jobdetail wechselt ins Englische.

**Codebeleg:** [Konto](../../../services/ingest/frontend/src/app/settings/page.tsx),
Zeilen 136–146; [Navigation](../../../services/ingest/frontend/src/components/sidebar-nav.tsx),
Zeilen 118–142; [Confluence-Verbindungen](../../../services/ingest/frontend/src/components/connections/confluence-connections-tab.tsx).

**Änderung / Abnahme:** Vorhandene Nutzerseiten durchgehend deutsch benennen.
Den heutigen Konto-Eintrag präzise „API-Zugriff“ nennen; „Verbindungen“ im
Quellenablauf einordnen. Keine neue Profilverwaltung ist dafür erforderlich.
Labels und Hilfetexte nennen die tatsächlich sichtbaren Navigationsziele.

### UI-07 · P1 · Chat-Anmeldung bleibt ohne Erklärung beim Laden

**Status (13.09.2026):** Teilweise umgesetzt. Der Ladezustand ist jetzt
zeitlich begrenzt (10 s) und zeigt danach einen verständlichen Fehler mit
„Erneut prüfen“; das normale Anmeldeformular bleibt als Rückweg sichtbar
([login/page.tsx](../../../services/ingest/frontend/src/app/login/page.tsx)).
Die eigentliche Ursache des hängenden Handoffs wurde nicht reproduziert
oder behoben.

**Reproduktion:** Aus dem Portal den Chat unter `http://localhost:3001`
öffnen → „Mit Weave anmelden“. Trotz bestehender Portal-Anmeldung bleibt
die Handoff-Anmeldeseite bei „Wird geladen“. Über mehrere Prüfungen hinweg
und nach erneutem Laden erschien weder Chat noch Fehler oder Wiederholungsaktion.
Die Browserkonsole lieferte bei der Abfrage keine Fehler/Warnungen.

**Einordnung:** Browserbeobachtung dieser lokalen Sitzung. Ursache offen;
kein bestätigter allgemeiner SSO- oder Backenddefekt. Der normale Login-Link
war vorhanden, die Agentenvermutung eines reinen Token-Logins trifft hier
also nicht zu.

**Code-Anknüpfung:** [Login-Handoff](../../../services/ingest/frontend/src/app/login/page.tsx),
Zeilen 62–73: Sessionprüfung und Weiterleitung im Ladezustand.

**Änderung / Abnahme:** Handoff in dieser Umgebung reproduzieren und Ursache
beheben. Zusätzlich einen zeitlich begrenzten Ladezustand mit verständlichem
Fehler, Wiederholung und Rückweg vorsehen. Mit bestehender Portal-Sitzung
muss der Chat erreichbar sein oder eine konkrete nächste Handlung anzeigen.

## Zusätzliche Codebefunde, nicht im Browser provoziert

### UI-08 · P1 · Markdown-Speichern bleibt nach Netzfehler gesperrt

**Status (13.09.2026):** ✅ Umgesetzt. `saveMarkdown()` läuft jetzt in
`try/catch/finally`; der Busy-State wird auch nach einer Exception
zurückgesetzt.

**Beleg:** [Jobdetail](../../../services/ingest/frontend/src/app/jobs/[id]/page.tsx),
Zeilen 416–445 und 649–650. `saveMarkdown()` setzt `isSaving`, wartet aber
ohne `try/catch/finally` auf den Request und die JSON-Antwort. Eine Exception
überspringt das Zurücksetzen; der Button bleibt bei „Saving...“ deaktiviert.

**Änderung / Abnahme:** Fehler anzeigen, Entwurf erhalten und Busy-State in
`finally` zurücksetzen. Einen abgebrochenen Netzwerkrequest prüfen: Speichern
ist danach erneut möglich. Bestehende Dokumente wurden für diesen Fehlerfall
nicht verändert.

### UI-09 · P1 · Qualitätsstufe C wird widersprüchlich erklärt

**Status (13.09.2026):** ✅ Umgesetzt. `activity.tsx` zeigt Stufe C jetzt als
„Qualitätsprüfung erforderlich“ statt „blockiert“, konsistent mit
`reviews.tsx`; der Activity-Test ist entsprechend angepasst.

**Beleg:** [activity.tsx](../../../services/ingest/frontend/src/components/portal/activity.tsx),
Zeilen 71–72 und 96–97, bezeichnet Stufe C als blockierte Freigabe.
[reviews.tsx](../../../services/ingest/frontend/src/components/portal/reviews.tsx),
Zeilen 102 und 109, erlaubt dagegen eine bewusste Freigabe trotz Stufe C.
Der [Backendvertrag](../../../services/ingest/backend/app/api/portal.py),
Zeilen 243–248, unterstützt genau diese explizite Bestätigung.

**Änderung / Abnahme:** In Verarbeitung und Prüfung konsistent
„Stufe C – Prüfung erforderlich“ anzeigen. Eine echte Blockade separat
benennen. Dasselbe C-Dokument darf auf den beiden Seiten keine gegensätzliche
Aussage zur Freigabemöglichkeit erhalten. Den bisherigen Activity-Test,
der die Blockade erwartet, entsprechend dem Backendvertrag korrigieren.

### UI-10 · P2 · Hauptlink laufender Importe führt vorzeitig zur Freigabe

**Status (13.09.2026):** ✅ Umgesetzt. `activityUrl()` führt bei
laufendem/fehlgeschlagenem Import jetzt auf `/imports/{run_id}` statt zur
Prüfung.

**Beleg:** [activity.tsx](../../../services/ingest/frontend/src/components/portal/activity.tsx),
Zeilen 63–65, verlinkt fertige Einzeljobs mit Wissensbereich zur Prüfung,
unabhängig vom noch laufenden oder fehlgeschlagenen Gesamtimport. Zeilen
86–91 zeigen diesen Importzustand bereits an. Die Prüfung erklärt dann,
dass unvollständige Importe nicht freigegeben werden können.

Ein separater Link „Confluence-Import ansehen“ ist in derselben Zeile bereits
vorhanden (Zeilen 112–115); es fehlt also nicht jeder Weg zum Import.

**Änderung / Abnahme:** Hauptaktion zustandsabhängig zum Importfortschritt bzw.
Importfehler führen. Erst nach vollständigem Import wird „Prüfen“ der nächste
Schritt. Mit fertigem Einzeljob in laufendem/fehlgeschlagenem Import prüfen.

### UI-11 · P2 · Anmeldung verliert das ursprünglich angeforderte Ziel

**Status (13.09.2026):** ✅ Umgesetzt. Beide Redirect-Pfade (`api.ts`s
`apiFetch`/`redirectIfSessionExpired` UND `auth-context.tsx`s eigener
Mount-Redirect) hängen jetzt denselben validierten `returnTo`-Pfad an;
`login/page.tsx` löst ihn nach der Anmeldung auf. Regressionstest in
[auth-context.test.tsx](../../../services/ingest/frontend/src/lib/auth-context.test.tsx).

**Beleg:** [auth-context.tsx](../../../services/ingest/frontend/src/lib/auth-context.tsx),
Zeilen 87–102, und [api.ts](../../../services/ingest/frontend/src/lib/api.ts),
Zeilen 52–53, leiten ohne Rücksprungziel auf `/login` um. Der
[Login](../../../services/ingest/frontend/src/app/login/page.tsx), Zeile 112,
führt danach außerhalb des Chat-Handoffs immer zur Übersicht.

**Änderung / Abnahme:** Angeforderten internen Pfad sicher als Rücksprungziel
erhalten. Einen Dokumentlink ohne Sitzung öffnen und anmelden: Danach muss
dieses Dokument statt der Übersicht erscheinen. Externe Rücksprungziele
abweisen. Der vollständige Ablauf wurde nicht durch Abmelden der vom Nutzer
bereitgestellten Sitzung wiederholt.

## Was beibehalten werden sollte

- Der Einstieg mit Wissensbereich → Quelle → Prüfung erklärt den Prozess gut.
- Freigabe und tatsächliche KI-Verfügbarkeit werden ausdrücklich getrennt.
- Der sichtbare Hinweis zu nicht übernommenen Confluence-Seitenrechten ist
  vor dem Start platziert.
- Leere Prüfungsliste, „Alle Dokumente“ und der freigegebene Stand waren
  erreichbar und verständlich.

## Navigationsempfehlung mit vorhandenen Funktionen

```text
WISSEN
  Übersicht
  Wissensbereiche
  Chat
QUELLEN UND VERARBEITUNG
  Quelle hinzufügen
    Confluence-Verbindungen
  Verarbeitung
    Auftragsdetails / Confluence-Importdetails
  Prüfen & freigeben
PERSÖNLICH
  API-Zugriff
```

„Confluence-Verbindungen“ bleibt direkt vom Importformular erreichbar;
UI-03 ergänzt dort die Anlage. Die Gruppierung benennt nur vorhandene
Nutzerfunktionen. Administrative Navigation ist nicht Teil des Vorschlags.

## Prüflücken und zurückgestellte Hinweise

- Keine echten Importläufe: In diesem Konto waren keine Confluence-Verbindungen
  vorhanden. Keine Upload-, Veröffentlichungs- oder Löschmutation ausgeführt.
- Chat-Gespräch, Botwechsel und Quellenfilter waren wegen des Handoff-Problems
  nicht erreichbar. Das absichtliche Beibehalten von Quellenfiltern beim
  Botwechsel ist im Code dokumentiert und wird nicht als neuer Fehler gezählt.
- Mobile Prüfung nicht belastbar: Der angeforderte Viewport 390 × 844 wurde
  vom Browser nicht übernommen; die gemessene Breite blieb 1202 Pixel. Der
  Override wurde zurückgesetzt. Daher keine behauptete mobile Verifikation.
- Der gemeinsame Retry-Button in `Notice` hat keinen expliziten
  `type="button"` und wird auch in Formularen verwendet. Ein unbeabsichtigtes
  Submit ist als gezielter Fehlerfall noch zu prüfen, nicht als im Browser
  reproduzierter Importstart behauptet.
- Eine alte `/search`-Weiterleitung allein belegt keine fehlende zugesagte
  Suchfunktion; daraus wurde kein neuer Befund abgeleitet.

Zuerst UI-04, UI-05 und UI-07 bearbeiten: Sie unterbrechen den unmittelbaren
Weg von einer Quelle bis zur Nutzung im Chat. Danach Fehlerbehandlung und
widersprüchliche Statusmeldungen korrigieren.
