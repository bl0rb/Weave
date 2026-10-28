# Wiki-Seiten

Dies ist die im Repository gepflegte Fassung des
[GitHub-Wikis](https://github.com/bl0rb/Weave/wiki). Die Dateien liegen hier
unverändert so, wie das Wiki sie erwartet — mit `[[Seitenname]]`-Links, die
GitHub nur im Wiki selbst auflöst, und `_Sidebar.md` als Navigation. Einstieg
ist [Home.md](Home.md); [Was-ist-neu.md](Was-ist-neu.md) ordnet die Änderungen
seit v0.7.1 ein.

Ins Wiki übernehmen (im Klon von `https://github.com/bl0rb/Weave.wiki.git`):

```bash
cp -r docs/wiki/*.md docs/wiki/images <pfad-zum-wiki-klon>/
cd <pfad-zum-wiki-klon> && git add -A && git commit -m "Sync from docs/wiki" && git push
```

Das Wiki ist der Einstieg, nicht die Doku: Maßgeblich bleiben
[betrieb.md](../betrieb.md), [wissensportal.md](../wissensportal.md),
[../adr/](../adr/) und [../../contracts/](../../contracts/).
