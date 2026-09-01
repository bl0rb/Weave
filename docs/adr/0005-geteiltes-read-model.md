# ADR 0005: Geteiltes Read-Model fuer den Chunk-Store

**Status:** angenommen

**Datum:** 2026-08-31

## Kontext

ADR-0004 legt fest: ein Postgres-Cluster, aber eine eigene Datenbank pro Service, keine geteilten Tabellen. Weave-Knowledge und Weave-Retrieval bilden dabei einen Sonderfall, den ADR-0004 selbst noch nicht aufloest:

- Weave-Knowledge chunked und embedded verarbeitete Dokumente und legt sie in `documents`/`chunks` (plus einer `collections`-Registry-Kopie) in seiner eigenen `weave_knowledge`-Datenbank ab.
- Weave-Retrieval beantwortet Suchanfragen ueber genau diese Chunks: Vektor-Aehnlichkeitssuche per pgvector-KNN (`ORDER BY embedding <=> :query_vector`) und Volltextsuche per `tsvector`-Ranking (`ts_rank(tsv, query)`), fusioniert per Reciprocal Rank Fusion.

Beide Operationen — pgvector-KNN und tsvector-Ranking — MUESSEN als SQL-Operationen direkt in der Datenbank laufen. Sie sind auf Indexe angewiesen, die nur Postgres selbst pflegt (HNSW fuer `vector_cosine_ops`, GIN fuer die generierte `tsvector`-Spalte); ausserhalb der Datenbank nachgebaut waeren sie entweder ein Full-Table-Scan pro Anfrage oder eine eigene, parallel gepflegte Indexstruktur.

Eine strikte Anwendung von ADR-0004 wuerde bedeuten: Weave-Retrieval spricht Weave-Knowledge nur ueber dessen HTTP-API an. Das scheitert an genau dieser Anforderung — ein API-Hop zwingt Weave-Knowledge dazu, pro Suchanfrage alle Kandidaten-Chunks seriell durchzureichen (oder die Fusions-/Ranking-Logik selbst nachzubauen), also die halbe Suche selbst zu implementieren. Das schliesst Weave-Knowledges eigene Rolle aus: es ist die Index-Pipeline (siehe dessen README-Nicht-Ziele), nicht die Suchpipeline — Suche ist explizit Weave-Retrievals Aufgabe.

## Entscheidung

`documents`/`chunks` (und die `collections`-Registry-Kopie) in der `weave_knowledge`-Datenbank sind ein **geteiltes Read-Model**:

- **Weave-Knowledge bleibt der einzige Schreiber.** Es fuehrt `INSERT`/`UPDATE`/`DELETE` auf diesen Tabellen aus und ist der einzige Service, der Alembic-Migrationen dagegen laufen laesst.
- **Weave-Retrieval liest read-only, direkt und synchron, auf derselben Datenbank** — kein API-Hop, keine Kopie, keine asynchrone Synchronisierung.

Das ist eine **bewusste, hier benannte Ausnahme** von ADR-0004s "kein Sharing von Tabellen": es gibt in der gesamten Weave-Plattform genau diese eine Ausnahme, dokumentiert genau hier. Sie gilt ausschliesslich fuer `documents`/`chunks`/`collections` in `weave_knowledge` — kein anderes Tabellenpaar zwischen anderen Services wird dadurch praejudiziert.

Der vollstaendige, verbindliche Vertrag (Tabellenschema, Spalten, Indexe, DB-Rolle, Versionierungsregel) lebt in `contracts/chunk-store.md`, nicht in diesem ADR — dieses Dokument haelt die Architektur-Entscheidung und ihre Begruendung fest, der Vertrag die technischen Details, die sich mit jeder additiven Schema-Aenderung weiterentwickeln.

### DB-Rolle

Weave-Retrieval verbindet sich **nie** mit Weave-Knowledges Schreib-Credentials, sondern ueber eine eigene, rein lesende Rolle:

```sql
CREATE ROLE weave_retrieval_ro WITH LOGIN PASSWORD '<siehe Secret-Manager>';
GRANT CONNECT ON DATABASE weave_knowledge TO weave_retrieval_ro;
GRANT USAGE ON SCHEMA public TO weave_retrieval_ro;
GRANT SELECT ON documents, chunks, collections TO weave_retrieval_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO weave_retrieval_ro;
```

`weave_retrieval_ro` bekommt niemals `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE`/`CREATE`/`DROP`. Das ist keine reine Haertungsmassnahme, sondern die Durchsetzung dieser Architektur-Entscheidung auf DB-Ebene: selbst ein Bug in Weave-Retrievals eigenem Code kann diese Tabellen technisch nicht veraendern.

## Konsequenzen

**Positiv:**

- pgvector-KNN und tsvector-Ranking laufen dort, wo sie hingehoeren — als SQL, mit den passenden Indexen, ohne Umweg.
- Keine zweite Kopie der Chunk-Daten, kein Sync-Mechanismus, kein Konsistenzfenster zwischen zwei Datenbanken.
- Weave-Knowledges Rolle bleibt sauber auf die Index-Pipeline beschraenkt; Weave-Retrieval muss keine Indexier-Logik nachbauen, um suchen zu koennen.
- Die DB-Rolle erzwingt Read-Only technisch, nicht nur per Konvention.

**Negativ:**

- Das Schema von `documents`/`chunks`/`collections` wird zu einem **Vertrag zwischen zwei Services** (`contracts/chunk-store.md`): eine Spalte umbenennen, einen Typ aendern, eine Tabelle entfernen — jede breaking Aenderung braucht Koordination beider Teams/Deployments vor dem Rollout, nicht nur eine einzelne Migration in einem Repo.
- Weave-Retrieval besitzt **kein eigenes Alembic** auf dieser Datenbank und darf dort niemals Schema anlegen, auch nicht additiv — jede Schema-Aenderung ist ausschliesslich Weave-Knowledges Aufgabe.
- Ein bereits laufendes Weave-Retrieval darf nie gegen ein Schema laufen, das eine breaking Aenderung dieses Vertrags bereits enthaelt, die es selbst noch nicht kennt — Deploy-Reihenfolge bei breaking Changes ist nicht mehr beliebig.
- Diese eine Ausnahme muss bei jeder zukuenftigen Diskussion ueber ADR-0004 explizit mitgedacht werden, damit sie nicht versehentlich als Praezedenzfall fuer weitere geteilte Tabellen gelesen wird.

## Alternativen

1. **Interne Such-API in Weave-Knowledge:**
   Weave-Retrieval schickt eine Suchanfrage per HTTP an Weave-Knowledge, das intern gegen seine eigene Datenbank sucht und Ergebnisse zurueckgibt.
   Abgelehnt: verlagert pgvector-KNN und tsvector-Ranking — und damit RRF-Fusion und Reranking-Vorbereitung — in Weave-Knowledge hinein, dessen Rolle explizit die Index-Pipeline ist, nicht die Suchpipeline. Weave-Knowledge muesste entweder die halbe Suchlogik duplizieren oder Weave-Retrieval wuerde durch einen zusaetzlichen Netzwerk-Hop pro Suchanfrage ausgebremst, ohne dass dieser Hop irgendeinen Isolationsgewinn brächte (beide Services muessten sich ohnehin auf dasselbe Vektor-/Text-Schema einigen).

2. **Datenduplikat in einer eigenen Retrieval-Datenbank:**
   Weave-Retrieval haelt eine eigene Kopie von `documents`/`chunks` in `weave_retrieval` (so wie ADR-0004 es fuer den Normalfall vorsieht), synchronisiert per Event oder periodischem Abgleich.
   Abgelehnt: Sync-Aufwand und Konsistenzrisiko (welche Kopie ist gerade aktuell, was passiert bei einem verpassten Event) fuer einen Datensatz, der ohnehin nur von einem einzigen Schreiber stammt und bei dem Verzoegerung direkt die Suchqualitaet beeintraechtigt (frisch indizierte Dokumente waeren erst nach einem Sync-Zyklus auffindbar). Genau das Problem, das die `collections`-Registry-Kopie in Weave-Knowledge bereits fuer EINEN Wert (Team-Rechte) in Kauf nimmt, wollen wir fuer den viel groesseren und schreibintensiveren Chunk-Datensatz nicht wiederholen.

3. **Gemeinsames Schema fuer alle Services:**
   Eine Rueckkehr zu PaddleDocs monolithischem Modell, diesmal explizit fuer mehrere Services.
   Abgelehnt: genau das Problem, das ADR-0004 ueberhaupt erst aufloesen sollte (Cross-Service-Joins, schwer unabhaengig skalierbar/testbar, keine klare Daten-Ownership). Der Bedarf hier ist eng und spezifisch (zwei Services, drei Tabellen, ein Schreiber) — er rechtfertigt keine Generalisierung auf "Services duerfen sich generell Schemas teilen".
