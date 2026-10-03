# ADR 0008: Besitz, Freigaben und Metadaten für Wissensbereiche und Bots

**Status:** angenommen (Umsetzung offen)

**Datum:** 2026-09-26

## Kontext

Ein Wissensbereich (`collections` in Weave-Ingest) hat genau einen Besitzer (`owner_id`). Zugriff entsteht heute aus drei Quellen:

- Freigabe an Teams (`read_teams`). Ob eine Person darüber auch hochladen und pflegen darf, entscheidet ihre Teamrolle (`user_teams.role`: `member` oder `reader`) – für alle Bereiche dieses Teams gleich.
- Freigabe an Personen (`read_users`), bewusst nur lesend.
- Implizit über das Hauptteam des Besitzers (`users.team_id`): Wer im selben Team ist, sieht den Bereich; wer dort `member` ist, darf ihn verwalten.

Wer einen Bereich verwalten darf, lässt sich damit nicht am Bereich ablesen, sondern hängt vom Hauptteam einer anderen Person ab.

Bots (`managed_bots`) verwalten ausschließlich Admins. Freigeben lassen sie sich nur an Teams (`teams`); eine leere Liste bedeutet „für alle". Einen Besitzer oder Freigaben an einzelne Personen gibt es nicht.

Für Abruf und Pflege fehlt, wofür ein Bereich gedacht ist und wer fachlich zuständig ist. `description` ist optional, `department` ist ein Freitext, der als Verarbeitungsvorgabe in die Dokument-Metadaten wandert, und wer einen Bereich angelegt hat, geht verloren, sobald der Besitz wechselt.

## Entscheidung

### Rollen an Wissensbereichen

Rechte an einem Wissensbereich entstehen ausschließlich über Freigaben. Eine Freigabe verbindet den Bereich mit einer Person oder einem Team und trägt eine Rolle:

| Rolle | Darf |
|---|---|
| Besitzer | Metadaten ändern, Freigaben verwalten, Bereich löschen – und alles, was Mitglieder dürfen |
| Mitglied | Dokumente hochladen, freigeben, pflegen und zurückziehen – und alles, was Leser dürfen |
| Leser | Den Bereich in Chat und Suche verwenden |

- Ein Bereich hat einen oder mehrere Besitzer. Besitzer sind immer Personen, nie Teams. Der letzte Besitzer kann nicht entfernt werden (wie der letzte aktive Admin).
- Freigaben an Personen gelten für lokale und SSO-Konten gleich. Referenziert wird die Nutzer-ID, nie E-Mail oder Name.
- Teams erhalten die Rolle Mitglied oder Leser. Für die einzelne Person gilt die niedrigere Rolle aus Freigabe und Teammitgliedschaft: Wer im Team nur Leser ist, wird durch eine Mitglied-Freigabe des Teams nicht zum Mitglied.
- Hat eine Person mehrere Freigaben (direkt und über Teams), gilt die höchste.
- Admins dürfen alles; das ist keine Rolle am Bereich.
- `visibility = public` bleibt erhalten: Dann ist jeder angemeldete Nutzer Leser. Mitglied und Besitzer brauchen weiterhin eine Freigabe.

Die Regel über das Hauptteam des Besitzers entfällt für Wissensbereiche.

### Metadaten

Beim Anlegen eines Wissensbereichs werden erfasst:

- **Zweck** – Pflichtfeld. Nutzt das bestehende Feld `description`. Bestehende Bereiche ohne Zweck werden in der Pflege als unvollständig angezeigt.
- **Zuständiges Team** – optionaler Verweis auf ein Team (`responsible_team_id`). Eine reine Angabe für Abruf und Pflege, kein Recht. Die Oberfläche schlägt beim Anlegen eine Mitglied-Freigabe für dieses Team vor, die entfernt werden kann.
- **Ersteller** – `created_by_id`. Wird beim Anlegen gesetzt und nie geändert; bleibt auch nach einer Übergabe des Besitzes erhalten.
- **Besitzer** – beim Anlegen der Ersteller; weitere lassen sich hinzufügen.

`department` bleibt unverändert. Es ist eine Verarbeitungsvorgabe für Dokumente, nicht das zuständige Team.

### Rollen an Bots

| Rolle | Darf |
|---|---|
| Besitzer | Fachliche Einstellungen (Beschreibung, Systemprompt, Wissensbereiche, Quellenpflicht) und Freigaben pflegen |
| Nutzer | Den Bot im Chat verwenden |

- Anlegen, Löschen und die technische Anbindung (n8n-Webhook, Geheimnis, Timeout) bleiben Admins vorbehalten. Admins bestimmen die Besitzer und dürfen selbst freigeben.
- Freigaben gehen an Personen (lokal und SSO) oder Teams.
- „Leere Teamliste = für alle" wird durch ein ausdrückliches „für alle freigegeben" ersetzt. Ohne Freigabe ist ein Bot nur für Besitzer und Admins nutzbar (fail closed, wie `visibility` bei Wissensbereichen).
- Ein Besitzer kann einem Bot nur Wissensbereiche zuordnen, die er selbst lesen darf.

### Bots antworten nur aus lesbaren Bereichen

Ein Bot antwortet einer Person nur aus Wissensbereichen, auf die diese Person selbst Zugriff hat: Die Bereiche des Bots werden mit den lesbaren Bereichen des Fragenden geschnitten. Eine Bot-Freigabe verleiht keine Leserechte. Das setzt den Grundsatz aus ADR 0007 fort, dass die Bot-Auswahl Nutzerrechte nur einschränken, nie erweitern kann, und gilt auch für an n8n delegierte Bots.

### SSO-Konten

SSO-Konten entstehen bei der ersten Anmeldung. Eine Freigabe an eine SSO-Person ist erst danach möglich; die Personensuche zeigt nur bestehende Konten. Vorgemerkte Freigaben per E-Mail-Adresse gibt es nicht.

### Dienstübergreifender Vertrag

Weave-Knowledge und Weave-Retrieval brauchen nur die Leserechte. Der Registry-Vertrag (`visibility`, `read_teams`, `read_users`) bleibt deshalb unverändert: Weave-Ingest berechnet ihn aus allen Freigaben, da jede Rolle Lesen einschließt. Rechteänderungen erfordern weiterhin keine Neuindizierung.

### Löschen und Entfernen

- **Person deaktivieren:** Anmeldung und Chat sind sofort gesperrt, da Weave-API die Identität bei jeder Anfrage in Weave-Ingest prüft. Die Freigaben bleiben, damit das Deaktivieren umkehrbar ist; die Oberfläche markiert deaktivierte Besitzer.
- **Person löschen:** Ihre Freigaben entfallen. Ist sie letzter Besitzer eines Bereichs oder Bots, bestimmt die Administration einen Nachfolger, der alle ihre Besitzerrollen übernimmt; ohne Nachfolger wird das Löschen abgelehnt.
- **Team löschen:** Seine Freigaben und Mitgliedschaften entfallen, und es ist nirgends mehr zuständiges Team. Vorher zeigt die Oberfläche, welche Bereiche und Bots betroffen sind.
- **Freigabe entziehen, Teamrolle ändern:** wirkt sofort. Dokumente eines Bereichs folgen nur dessen Rollen: Mitglieder und Besitzer sehen und bearbeiten sie, Leser nutzen den Bereich im Chat. Wer die Freigabe verliert, verliert damit auch den Zugriff auf seine eigenen Uploads dort.
- **Dokument löschen:** Mitglieder, Besitzer und Administration. Ein freigegebenes Dokument wird dabei aus dem Wissen zurückgezogen; Weave-Knowledge merkt sich die Rücknahme und ignoriert spätere Freigaben desselben Dokuments.
- **Bereich löschen:** leer, oder durch einen Besitzer mitsamt allen Dokumenten, nachdem er den Namen eingegeben hat. Passwortgeschützte Dokumente, laufende Importe und die Zuordnung zu einem Bot oder einer aktiven technischen Identität verhindern das Löschen – sonst erbte ein späterer Bereich mit demselben Kurznamen diesen Zugriff.
- **Bot löschen:** nur Administration; seine Freigaben entfallen.

### Migration

- `owner_id` wird zur Besitzer-Freigabe und zu `created_by_id`.
- `read_users` wird zu Leser-Freigaben.
- `read_teams` wird zu Mitglied-Freigaben. Durch die Regel „niedrigere Rolle gilt" wirken die Teamrollen wie bisher.
- Das Hauptteam des Besitzers wird zur Mitglied-Freigabe und zum zuständigen Team. Seine Mitglieder behalten damit ihre bisherigen Rechte.
- Bereiche ohne Besitzer (Altbestand mit `owner_id = NULL`) bleiben Admins vorbehalten, bis ein Admin einen Besitzer setzt.
- Bots: `teams` wird zu Nutzer-Freigaben, eine leere Liste zu „für alle freigegeben". Besitzer setzt ein Admin nach der Migration.

## Konsequenzen

- Wer was darf, steht am Bereich bzw. am Bot und ist ohne Kenntnis fremder Hauptteams nachvollziehbar.
- Weave-Ingest bekommt Freigabe-Tabellen für Bereiche und Bots (Ziel, Person oder Team, Rolle) sowie die Felder `responsible_team_id` und `created_by_id`. `read_teams`/`read_users` werden aus den Freigaben berechnet statt gepflegt.
- Freigaben an Personen können erstmals Mitglied- und Besitzerrechte tragen. Die bisherige Absicherung „Person = nur lesen" wird damit bewusst aufgehoben; vergeben dürfen solche Rechte nur Besitzer und Admins.
- Weave-Runtime muss Bot-Freigaben an Personen und das Kennzeichen „für alle" auswerten; heute prüft sie nur die Teams des Nutzers (`effective_teams`).
- Die Pflege kann Bereiche ohne Zweck, ohne zuständiges Team oder mit ausschließlich deaktivierten Besitzern auflisten.
- SSO-Personen müssen sich einmal angemeldet haben, bevor sie einzeln freigegeben werden können.
- `users.team_id` bleibt vorerst bestehen: Die Team-Sichtbarkeit von Verarbeitungsaufträgen, Importläufen und Benchmarks hängt weiter daran. Es zu entfernen ist ein eigener Schritt.

## Alternativen

- **Bot-Freigabe verleiht Leserechte:** bequemer für Nutzer, macht einen Bot aber zum Umweg an den Freigaben eines Bereichs vorbei. Verworfen.
- **Vorgemerkte Freigaben per E-Mail für SSO-Konten:** erlaubt Freigaben vor der ersten Anmeldung, bindet Rechte aber an eine veränderliche Adresse und braucht eigene Auflösung beim Login. Verworfen; Freigabe nach der Anmeldung reicht.
- **Rolle nur an der Teammitgliedschaft (Ist-Zustand):** kein neues Datenmodell, aber keine Rollen pro Bereich und keine Mitglied-Rechte für einzelne Personen.
- **Teams als Besitzer:** verwässert die Verantwortung; bei Personalwechseln bleibt offen, wer zuständig ist. Den fachlichen Bezug deckt das zuständige Team ab.
- **Zuständiges Team verleiht automatisch Rechte:** vermischt Auskunft und Berechtigung. Verworfen zugunsten einer vorgeschlagenen, sichtbaren Mitglied-Freigabe.

## Nachtrag 2026-10-03: Altbestand ohne Wissensbereich (Audit F41)

Dokumente in Weave-Knowledge ohne `collection_slug` – indiziert, bevor es Wissensbereiche gab, oder über den alten `document.processed`-Weg – haben keine Freigaben. Ein Bot mit `include_uncollected` (bisher Voreinstellung) fand sie, begrenzt nur durch `Document.team`: das Hauptteam des Hochladenden zum Zeitpunkt der Freigabe, also genau die Regel, die diese Entscheidung für Wissensbereiche abschafft.

- **Job gehört zu einem Bereich** (`processing_info.settings.collection_id` in Weave-Ingest): `python -m app.cli reconcile-collections` in Weave-Knowledge übernimmt diesen Bereich. Danach entscheiden allein seine Freigaben. Inhalt und Index bleiben unverändert, es entsteht keine neue, ungeprüfte Freigabe. Der Befehl ist idempotent und ändert nur Zeilen ohne Bereich.
- **Job ohne Bereich:** Ohne Freigaben gibt es kein Leserecht – ausgeschlossen ist der Normalfall, wie bei Bereichen ohne Besitzer. Neue Bots starten mit `include_uncollected = false`.
- **Bestehende Bots** behalten ihre gespeicherte Einstellung, YAML-Bots ohne eigene Angabe den Runtime-Standard (`true`), damit nach dem Update keine Antworten stillschweigend wegfallen. Solange ein Bot den Altbestand einbezieht, gilt dafür weiter `Document.team` – als Übergang, nicht als Rechtemodell. Admins schalten es je Bot ab, sobald der verbleibende Altbestand nicht mehr gebraucht wird (Betriebshandbuch, Abschnitt 3.2).

Verworfen: die Team-Regel für den Altbestand dauerhaft festschreiben. Sie hielte neben den Freigaben eine zweite Rechtequelle am Leben, die vom Hauptteam einer anderen Person abhängt.
