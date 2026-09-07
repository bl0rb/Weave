{{/*
weave.generated (ctx) -- die im Chart mitgelieferte values.generated.yaml.

Helm merged automatisch NUR values.yaml. Ohne diesen Zugriff scheitert der
veroeffentlichte Weg

    helm install weave oci://ghcr.io/bl0rb/charts/weave --version X

daran, dass der Installierende die erzeugte Datei gar nicht lokal hat --
sie liegt im Paket, aber Helm liest sie nicht von selbst. .Files.Get holt
sie aus genau diesem Paket, sodass ein Install ohne -f funktioniert.
*/}}
{{- define "weave.generated" -}}
{{- $raw := .Files.Get "values.generated.yaml" -}}
{{- if $raw -}}
{{- toYaml (fromYaml $raw) -}}
{{- else -}}
{{- toYaml dict -}}
{{- end -}}
{{- end -}}

{{/*
weave.configFor (ctx, service) / weave.secretsFor (ctx, service)

Die erzeugte Datei ist die Grundlage, explizit gesetzte Werte gewinnen --
so kann ein Wrapper einzelne Eintraege ueberschreiben, ohne die ganze
Datei nachbauen zu muessen.
*/}}
{{- define "weave.mergedConfig" -}}
{{- $gen := (include "weave.generated" . | fromYaml) -}}
{{- $g := (get $gen "config") | default dict -}}
{{- $v := .Values.config | default dict -}}
{{- toYaml (mergeOverwrite (deepCopy $g) $v) -}}
{{- end -}}

{{- define "weave.mergedSecrets" -}}
{{- $gen := (include "weave.generated" . | fromYaml) -}}
{{- $g := (get $gen "secrets") | default dict -}}
{{- $v := .Values.secrets | default dict -}}
{{- toYaml (mergeOverwrite (deepCopy $g) $v) -}}
{{- end -}}

{{/*
===========================================================================
Der Vertrag, gegen den alle Workload-Templates dieses Charts geschrieben
sind.

JEDER Helper mit Parametern nimmt GENAU EIN dict entgegen, und dessen erster
Schluessel heisst immer "ctx" und traegt den Wurzel-Kontext ($). Also:

    {{- include "weave.labels" (dict "ctx" $ "component" "ingest-backend") | nindent 4 }}

Helper ohne Parameter nehmen den Kontext direkt:

    {{- include "weave.fullname" . }}

---------------------------------------------------------------------------
Workload -> Dienstname in config/secrets -> Komponentenname im Objektnamen

  Werte-Pfad         config/secrets       Komponente          Port
  ingestBackend      ingest               ingest-backend      8000
  ingestWorker       ingest               ingest-worker       -
  ingestFrontend     ingest + chat        ingest-frontend     3000
  knowledge          knowledge            knowledge           8000
  knowledgeWorker    knowledge            knowledge-worker    -
  retrieval          retrieval            retrieval           8000
  runtime            runtime              runtime             8000
  api                api                  api                 8000
  toolsBackend       tools                tools-backend       8000
  toolsBackend.mcp   tools                tools-mcp           8000
  chat               chat                 chat                3000
  embeddings         embeddings           embeddings          8000
  reranker           reranker             reranker            8000

Die Dienstnamen der mittleren Spalte sind die aus weave.yaml und damit die
Schluessel in values.generated.yaml. Der Compose-Stack nennt die
Chat-Oberflaeche weave-tools-frontend, weave.yaml nennt sie "chat" -- hier
gilt weave.yaml.

Ingest-Backend, -Worker und -Frontend teilen sich EINEN config-Block
(config.ingest), weil weave.yaml sie als einen Dienst fuehrt. Deshalb nimmt
weave.env ein "only", mit dem ein Workload sich auf die Variablen
beschraenkt, die er wirklich braucht.

---------------------------------------------------------------------------
WARUM ES "rename" GIBT

weave.yaml benennt eine Variable so, wie der Schluessel in der .env heisst.
Das ist NICHT immer der Name, unter dem der Container sie liest: die
Compose-Datei benennt beim Durchreichen um, damit drei Dienste je einen
eigenen .env-Schluessel fuer ihr jeweils eigenes SECRET_KEY haben koennen
(ADR-0003) und trotzdem alle drei im Container SECRET_KEY lesen. Genau
diese Umbenennungen muss auch das Chart machen -- sonst startet der Dienst
und findet seine Pflichtwerte nicht.

Vollstaendige Liste, aus deploy/docker-compose.weave.yml abgeglichen:

  knowledge / knowledgeWorker (secretEnv):
      WEAVE_KNOWLEDGE_SECRET_KEY        -> SECRET_KEY
      WEAVE_KNOWLEDGE_INGEST_API_TOKEN  -> WEAVE_INGEST_API_TOKEN
      WEAVE_KNOWLEDGE_WEBHOOK_SECRET    -> WEAVE_INGEST_WEBHOOK_SECRET
  api (secretEnv):
      WEAVE_API_SECRET_KEY              -> SECRET_KEY
  chat (env):
      CHAT_APP_BASE_URL                 -> APP_BASE_URL

Sonst nirgends. Insbesondere ingest liest SECRET_KEY und
PORTAL_KNOWLEDGE_WEBHOOK_SECRET schon unter dem Namen, den weave.yaml
vergibt.

---------------------------------------------------------------------------
DIE REIHENFOLGE: ERST weave.secretEnv, DANN weave.env

Nicht Geschmackssache. DATABASE_URL und REDIS_URL enthalten $(POSTGRES_PASSWORD)
bzw. $(REDIS_PASSWORD), damit das Passwort nie im gerenderten Manifest steht.
Kubernetes loest ein $(VAR) in einem env-Wert AUSSCHLIESSLICH aus Eintraegen
auf, die in derselben Liste WEITER OBEN stehen. Steht das Geheimnis weiter
unten, bleibt die Referenz als Text stehen -- der Dienst verbindet sich dann
mit dem Passwort "$(POSTGRES_PASSWORD)" und meldet einen
Authentifizierungsfehler, ohne dass irgendwo steht, warum.

weave.env sortiert alphabetisch, DATABASE_URL kaeme also vor
POSTGRES_PASSWORD. Deshalb in JEDEM Workload-Template:

    env:
      {{- include "weave.secretEnv" (...) | nindent 12 }}
      {{- include "weave.env" (...) | nindent 12 }}

---------------------------------------------------------------------------
DER AUFRUF JE WORKLOAD

Was jedes Workload-Template an weave.env/weave.secretEnv uebergeben muss.
"override" heisst: das Chart bestimmt den Wert, weil weave.yaml dort die
Adressierung des Compose-Stacks beschreibt (Docker-Servicenamen), die es im
Cluster nicht gibt.

  ingestBackend / ingestWorker
    env      service "ingest"
             overrides POSTGRES_HOST = weave.postgresHost
                       POSTGRES_PORT = weave.postgresPort
                       REDIS_URL     = weave.redisUrl db 0
                       PORTAL_KNOWLEDGE_BASE_URL = weave.internalUrl knowledge
    secretEnv service "ingest"   (REDIS_PASSWORD muss dabei sein: REDIS_URL
             enthaelt $(REDIS_PASSWORD) und Kubernetes ersetzt das nur aus
             einem Eintrag DERSELBEN env-Liste)
    Der Worker bekommt bewusst dieselbe Umgebung wie das Backend: beide
    laden dasselbe Settings-Modul, ein Zuviel schadet nicht, ein Zuwenig
    laesst den Import scheitern.

  ingestFrontend
    env      service "ingest", only [WEAVE_INGEST_PUBLIC_API_URL]
    env      service "chat",   only [WEAVE_CHAT_PUBLIC_URL]
             (WEAVE_CHAT_PUBLIC_URL steht in weave.yaml beim Dienst chat,
             gelesen wird sie aber vom Ingest-Frontend -- zwei Aufrufe)
    keine Geheimnisse

  knowledge / knowledgeWorker
    env      service "knowledge", except [POSTGRES_USER, POSTGRES_PORT]
             (die beiden gehen nur in die DATABASE_URL ein)
             overrides DATABASE_URL = weave.databaseUrl mit
                         database postgresql.databases.knowledge,
                         user weave.postgresUser,
                         passwordVar POSTGRES_PASSWORD
                       REDIS_URL = weave.redisUrl db 1  (NICHT 0, ADR-0001)
                       WEAVE_INGEST_BASE_URL = weave.internalUrl ingestBackend
    secretEnv service "knowledge" mit der rename-Tabelle oben
             (POSTGRES_PASSWORD und REDIS_PASSWORD bleiben drin, s.o.)
    knowledgeWorker zusaetzlich: command aus knowledgeWorker.queue/logLevel

  retrieval
    env      service "retrieval", except [RETRIEVAL_DB_USER]
             overrides DATABASE_URL = weave.databaseUrl mit
                         database postgresql.databases.knowledge,
                         user weave.retrievalDbUser,
                         passwordVar RETRIEVAL_DB_PASSWORD
    secretEnv service "retrieval"

  runtime
    env      service "runtime"
             overrides RETRIEVAL_BASE_URL = weave.internalUrl retrieval
                       CHAT_CONFIG_BASE_URL = weave.internalUrl ingestBackend
                       TOOLS_BASE_URL = weave.internalUrl toolsBackend
    secretEnv service "runtime"

  api
    env      service "api", except [POSTGRES_USER, POSTGRES_PORT]
             overrides DATABASE_URL = weave.databaseUrl mit
                         database postgresql.databases.api,
                         user weave.postgresUser,
                         passwordVar POSTGRES_PASSWORD
                       RUNTIME_BASE_URL   = weave.internalUrl runtime
                       RETRIEVAL_BASE_URL = weave.internalUrl retrieval
                       INGEST_API_URL     = weave.internalUrl ingestBackend
             (INGEST_LOGIN_URL bleibt aus der Konfiguration: das ist die
             Adresse, die ein BROWSER aufruft, kein Clusterinterna)
    secretEnv service "api", rename WEAVE_API_SECRET_KEY -> SECRET_KEY

  toolsBackend (und toolsBackend.mcp mit demselben Image, anderem command)
    env      service "tools"
             overrides WEAVE_API_BASE_URL = weave.internalUrl api
                       RETRIEVAL_BASE_URL = weave.internalUrl retrieval
    secretEnv service "tools"

  chat
    env      service "chat", except [WEAVE_CHAT_PUBLIC_URL],
             rename CHAT_APP_BASE_URL -> APP_BASE_URL
             overrides WEAVE_API_BASE_URL = weave.internalUrl api
    keine Geheimnisse

  embeddings / reranker
    env      service "embeddings" bzw. "reranker"
    secretEnv dito
===========================================================================
*/}}

{{- define "weave.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "weave.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "weave.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Der Objektname eines Workloads: <fullname>-<komponente>.
Usage: {{ include "weave.componentName" (dict "ctx" $ "component" "ingest-backend") }}
*/}}
{{- define "weave.componentName" -}}
{{- printf "%s-%s" (include "weave.fullname" .ctx) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Labels fuer jedes Objekt. "component" ist optional -- ein Objekt ohne
Komponente (das Plattform-Secret) laesst es weg.
Usage: {{- include "weave.labels" (dict "ctx" $ "component" "api") | nindent 4 }}
*/}}
{{- define "weave.labels" -}}
helm.sh/chart: {{ include "weave.chart" .ctx }}
app.kubernetes.io/name: {{ include "weave.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
app.kubernetes.io/part-of: weave
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
{{- if .component }}
app.kubernetes.io/component: {{ .component }}
{{- end }}
{{- with .ctx.Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Nur das, was einen Pod einem Workload zuordnet. Ein Selector ist nach dem
Anlegen unveraenderlich, also darf hier NICHTS stehen, was sich zwischen zwei
Releases aendern kann -- insbesondere nicht die Chart-Version und nicht
global.commonLabels.
Usage: {{- include "weave.selectorLabels" (dict "ctx" $ "component" "api") | nindent 6 }}
*/}}
{{- define "weave.selectorLabels" -}}
app.kubernetes.io/name: {{ include "weave.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Annotationen fuer ein Objekt: global.commonAnnotations plus die des
Workloads. Damit setzt ein Wrapper-Chart z.B. argocd.argoproj.io/sync-wave,
ohne dieses Chart zu aendern. Die spezifischen gewinnen.

Gibt NICHTS aus, wenn beide leer sind -- deshalb gehoert der
`annotations:`-Schluessel im aufrufenden Template in ein `with`:

    {{- $ann := include "weave.annotations" (dict "ctx" $ "extra" .Values.api.podAnnotations) }}
    {{- with $ann }}
    annotations:
      {{- . | nindent 4 }}
    {{- end }}
*/}}
{{- define "weave.annotations" -}}
{{- $merged := merge (deepCopy (default dict .extra)) (default dict .ctx.Values.global.commonAnnotations) -}}
{{- with $merged }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
Pod-Annotationen: global.commonAnnotations, global.podAnnotations und die
podAnnotations des Workloads. Gleiche Aufrufform wie weave.annotations.
Usage: {{ include "weave.podAnnotations" (dict "ctx" $ "extra" .Values.api.podAnnotations) }}
*/}}
{{- define "weave.podAnnotations" -}}
{{- $merged := merge (deepCopy (default dict .extra)) (default dict .ctx.Values.global.podAnnotations) (default dict .ctx.Values.global.commonAnnotations) -}}
{{- with $merged }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
Pod-Labels: Selector-Labels plus global.podLabels plus die des Workloads.
Usage: {{- include "weave.podLabels" (dict "ctx" $ "component" "api" "extra" .Values.api.podLabels) | nindent 8 }}
*/}}
{{- define "weave.podLabels" -}}
{{- include "weave.selectorLabels" (dict "ctx" .ctx "component" .component) }}
{{- with .ctx.Values.global.podLabels }}
{{ toYaml . }}
{{- end }}
{{- with .extra }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "weave.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "weave.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Vollstaendige Image-Referenz. image.tag leer heisst appVersion des Charts;
global.imageRegistry wird, wenn gesetzt, vor das Repository gestellt.
Usage: image: {{ include "weave.image" (dict "ctx" $ "image" .Values.api.image) | quote }}
*/}}
{{- define "weave.image" -}}
{{- $registry := .ctx.Values.global.imageRegistry -}}
{{- $repo := required "image.repository darf nicht leer sein" .image.repository -}}
{{- $tag := .image.tag | default .ctx.Chart.AppVersion -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $registry) $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/*
Usage: imagePullPolicy: {{ include "weave.imagePullPolicy" (dict "ctx" $ "image" .Values.api.image) }}
*/}}
{{- define "weave.imagePullPolicy" -}}
{{- .image.pullPolicy | default .ctx.Values.global.imagePullPolicy | default "IfNotPresent" -}}
{{- end -}}

{{/*
Usage:
    {{- with (include "weave.imagePullSecrets" . ) }}
    imagePullSecrets:
      {{- . | nindent 8 }}
    {{- end }}
*/}}
{{- define "weave.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
===========================================================================
env aus der generierten Konfiguration
===========================================================================
*/}}

{{/*
Rendert die NICHT-geheimen Einstellungen eines Dienstes aus
.Values.config.<dienst> als env-Liste.

Parameter:
  ctx        Wurzelkontext ($), Pflicht.
  service    Dienstname aus weave.yaml, Pflicht (siehe Tabelle oben).
  only       Liste: nur diese Variablen. Leer/fehlend = alle.
  except     Liste: diese zusaetzlich zu .Values.env.exclude weglassen.
  overrides  dict: Variablen, die das Chart selbst bestimmt. Sie GEWINNEN
             gegenueber dem generierten Wert und gehen an only/except vorbei.
  rename     dict {alterName: neuerName}: die Variable geht unter dem neuen
             Namen in den Container. Siehe die Umbenennungstabelle am Kopf
             dieser Datei; wird nach only/except/overrides angewandt.

`overrides` ist der Platz fuer alles, was Compose als Docker-Servicename
kennt und im Cluster anders heisst -- POSTGRES_HOST, DATABASE_URL,
REDIS_URL, und die internen Basis-URLs. Das ist kein zweiter
Konfigurationsort: weave.yaml beschreibt dort die Adressierung des
Compose-Stacks, dieses Chart die des Clusters (siehe values.yaml,
internalUrls).

Usage (secretEnv IMMER zuerst, siehe den Abschnitt zur Reihenfolge oben):
    env:
      {{- include "weave.secretEnv" (dict "ctx" $ "service" "api"
            "rename" (dict "WEAVE_API_SECRET_KEY" "SECRET_KEY")) | nindent 12 }}
      {{- include "weave.env" (dict "ctx" $ "service" "api"
            "except" (list "POSTGRES_USER" "POSTGRES_PORT")
            "overrides" (dict
              "DATABASE_URL" (include "weave.databaseUrl" (dict "ctx" $ "database" $.Values.postgresql.databases.api "user" (include "weave.postgresUser" $) "passwordVar" "POSTGRES_PASSWORD"))
            )) | nindent 12 }}
*/}}
{{- define "weave.env" -}}
{{- $ctx := .ctx -}}
{{- $service := required "weave.env: 'service' fehlt" .service -}}
{{- $config := get (include "weave.mergedConfig" $ctx | fromYaml) $service -}}
{{- if and $config (not (kindIs "map" $config)) -}}
{{- fail (printf "config.%s ist kein Abbildungsblock. values.generated.yaml sieht nicht so aus, wie 'weave_config.py helm-values' sie schreibt." $service) -}}
{{- end -}}
{{- $config = default dict $config -}}
{{- $only := default (list) .only -}}
{{- $except := concat (default (list) $ctx.Values.env.exclude) (default (list) .except) -}}
{{- $out := dict -}}
{{- range $var, $val := $config -}}
{{- if and (or (empty $only) (has $var $only)) (not (has $var $except)) -}}
{{- $_ := set $out $var $val -}}
{{- end -}}
{{- end -}}
{{- range $var, $val := (default dict .overrides) -}}
{{- $_ := set $out $var $val -}}
{{- end -}}
{{/* Umbenennung: weave.yaml benennt eine Variable so, wie der Schluessel in
     der .env heisst -- was nicht immer der Name ist, unter dem der Container
     sie liest (die Compose-Datei benennt sie beim Durchreichen um). Siehe
     die Tabelle am Kopf dieser Datei. */}}
{{- range $from, $to := (default dict .rename) -}}
{{- if hasKey $out $from -}}
{{- $_ := set $out $to (get $out $from) -}}
{{- $_ := unset $out $from -}}
{{- end -}}
{{- end -}}
{{/* Als Liste zusammengesetzt und erst am Ende verbunden, damit die
     Ausgabe weder mit einem Zeilenumbruch beginnt noch mit einem endet --
     sonst reisst jedes `| nindent` eine Leerzeile ins Manifest. */}}
{{- $lines := list -}}
{{- range $var := (keys $out | sortAlpha) -}}
{{- $lines = append $lines (printf "- name: %s\n  value: %s" $var (get $out $var | quote)) -}}
{{- end -}}
{{- join "\n" $lines -}}
{{- end -}}

{{/*
===========================================================================
Secrets
===========================================================================
*/}}

{{/*
Der Name des Secrets, aus dem JEDES Geheimnis dieser Plattform kommt:
entweder das fremde aus secrets.existingSecret oder das vom Chart
angelegte. Ein Wrapper-Chart, das den External Secrets Operator benutzt,
setzt secrets.existingSecret auf den Namen, den sein ExternalSecret
erzeugt -- dieses Chart rendert dafuer nichts.
*/}}
{{- define "weave.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "weave.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Rendert aus .Values.secrets.<dienst> die valueFrom-secretKeyRef-Eintraege.
Der Schluessel im Secret heisst wie die Variable.

Parameter: ctx (Pflicht), service (Pflicht), only, except, rename -- wie bei
weave.env, nur ohne overrides: ein Geheimnis kommt immer aus dem Secret,
nie aus einem Wert im Chart. "rename" aendert nur den Namen der
Umgebungsvariable, nie den Schluessel im Secret.

Usage:
      {{- include "weave.secretEnv" (dict "ctx" $ "service" "api"
            "rename" (dict "WEAVE_API_SECRET_KEY" "SECRET_KEY")) | nindent 12 }}
*/}}
{{- define "weave.secretEnv" -}}
{{- $ctx := .ctx -}}
{{- $service := required "weave.secretEnv: 'service' fehlt" .service -}}
{{- $names := get (include "weave.mergedSecrets" $ctx | fromYaml) $service -}}
{{- if and $names (not (kindIs "slice" $names)) -}}
{{- fail (printf "secrets.%s ist keine Liste von Variablennamen. Reserviert sind in diesem Block nur existingSecret, create, values und optional; alles andere ist ein Dienstname und kommt aus values.generated.yaml." $service) -}}
{{- end -}}
{{- $names = default (list) $names -}}
{{- $only := default (list) .only -}}
{{- $except := default (list) .except -}}
{{- $secretName := include "weave.secretName" $ctx -}}
{{- $optional := "" -}}
{{- if $ctx.Values.secrets.optional -}}
{{- $optional = "\n      optional: true" -}}
{{- end -}}
{{/* Wie bei weave.env: ohne fuehrenden und abschliessenden Zeilenumbruch,
     damit `| nindent` keine Leerzeile erzeugt. */}}
{{- $rename := default dict .rename -}}
{{- $lines := list -}}
{{- range $var := ($names | sortAlpha) -}}
{{- if and (or (empty $only) (has $var $only)) (not (has $var $except)) -}}
{{/* Umbenannt wird nur der NAME der Umgebungsvariable. Der Schluessel im
     Secret heisst weiter wie in values.generated.yaml -- sonst muesste
     derselbe Wert unter zwei Schluesseln im Secret liegen. */}}
{{- $envName := $var -}}
{{- if hasKey $rename $var -}}
{{- $envName = get $rename $var -}}
{{- end -}}
{{- $lines = append $lines (printf "- name: %s\n  valueFrom:\n    secretKeyRef:\n      name: %s\n      key: %s%s" $envName ($secretName | quote) ($var | quote) $optional) -}}
{{- end -}}
{{- end -}}
{{- join "\n" $lines -}}
{{- end -}}

{{/*
Alle Variablennamen, die in dem einen Plattform-Secret vorkommen muessen --
ueber alle Dienste hinweg, entdoppelt und sortiert. Benutzt von
templates/secret.yaml und von NOTES/Pruefungen.
*/}}
{{- define "weave.allSecretNames" -}}
{{- $names := list -}}
{{- $reserved := list "existingSecret" "create" "values" "optional" -}}
{{- range $service, $value := (include "weave.mergedSecrets" . | fromYaml) -}}
{{- if not (has $service $reserved) -}}
{{- if kindIs "slice" $value -}}
{{- $names = concat $names $value -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- toYaml ($names | uniq | sortAlpha) -}}
{{- end -}}

{{/*
===========================================================================
PostgreSQL
===========================================================================
*/}}

{{/*
Die Rolle, unter der Ingest, Knowledge und API schreiben. Steht in
weave.yaml (config.postgres.POSTGRES_USER) und wird deshalb von dort
gelesen, nicht in values.yaml wiederholt.
*/}}
{{- define "weave.postgresUser" -}}
{{- $pg := get (include "weave.mergedConfig" . | fromYaml) "postgres" | default dict -}}
{{- $user := get $pg "POSTGRES_USER" -}}
{{- if not $user -}}
{{- fail "config.postgres.POSTGRES_USER fehlt. Diese Datei erzeugt 'python scripts/weave_config.py helm-values --out deploy/charts/weave/values.generated.yaml'; sie muss beim Installieren mit -f uebergeben werden." -}}
{{- end -}}
{{- $user -}}
{{- end -}}

{{/*
Die Datenbank, die Weave-Ingest besitzt -- POSTGRES_DB aus weave.yaml. Die
beiden anderen Datenbanknamen kennt weave.yaml nicht und stehen deshalb in
values.yaml unter postgresql.databases.
*/}}
{{- define "weave.postgresIngestDatabase" -}}
{{- $pg := get (include "weave.mergedConfig" . | fromYaml) "postgres" | default dict -}}
{{- $db := get $pg "POSTGRES_DB" -}}
{{- if not $db -}}
{{- fail "config.postgres.POSTGRES_DB fehlt -- values.generated.yaml wurde nicht mit -f uebergeben." -}}
{{- end -}}
{{- $db -}}
{{- end -}}

{{/*
Die Read-only-Rolle, mit der Weave-Retrieval in weave_knowledge liest
(ADR-0005, contracts/chunk-store.md). Steht als RETRIEVAL_DB_USER in
weave.yaml.
*/}}
{{- define "weave.retrievalDbUser" -}}
{{- $r := get (include "weave.mergedConfig" . | fromYaml) "retrieval" | default dict -}}
{{- $user := get $r "RETRIEVAL_DB_USER" -}}
{{- if not $user -}}
{{- fail "config.retrieval.RETRIEVAL_DB_USER fehlt -- values.generated.yaml wurde nicht mit -f uebergeben." -}}
{{- end -}}
{{- $user -}}
{{- end -}}

{{/*
Der Name des CNPG-Cluster-Objekts. CNPG legt dazu die Services
<name>-rw (Primary, schreibend), <name>-ro (Repliken) und <name>-r (alle) an.
*/}}
{{- define "weave.cnpgClusterName" -}}
{{- printf "%s-pg" (include "weave.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "weave.postgresHost" -}}
{{- $mode := .Values.postgresql.mode -}}
{{- if eq $mode "cnpg" -}}
{{- printf "%s-rw" (include "weave.cnpgClusterName" .) -}}
{{- else if eq $mode "external" -}}
{{- if not .Values.postgresql.external.host -}}
{{- fail "postgresql.mode=external, aber postgresql.external.host ist leer. Ohne Host weiss kein Dienst, wohin er verbinden soll." -}}
{{- end -}}
{{- .Values.postgresql.external.host -}}
{{- else -}}
{{- include "weave.componentName" (dict "ctx" . "component" "postgres") -}}
{{- end -}}
{{- end -}}

{{/*
Der Port ist im Cluster eine Eigenschaft des Services, den dieses Chart
anlegt (immer 5432), und nur im Modus external eine freie Angabe. Deshalb
NICHT aus config.*.POSTGRES_PORT: dort steht der Port des Compose-Stacks.
*/}}
{{- define "weave.postgresPort" -}}
{{- if eq .Values.postgresql.mode "external" -}}
{{- .Values.postgresql.external.port | default 5432 -}}
{{- else -}}
5432
{{- end -}}
{{- end -}}

{{/*
Eine DATABASE_URL im Format, das Weave-Knowledge, -Retrieval und -API
erwarten (postgresql+psycopg://...).

Das Passwort wird NICHT eingesetzt, sondern als $(VAR) stehen gelassen:
Kubernetes ersetzt $(VAR) in einem env-Wert durch einen zuvor in derselben
env-Liste definierten Eintrag. Der aufrufende Workload muss also den
Eintrag <passwordVar> per weave.secretEnv VOR dieser Variable haben. So
steht das Passwort nie im gerenderten Manifest.

Achtung: ein Passwort mit Sonderzeichen aus dem Zeichenvorrat, den eine URL
selbst benutzt (@ : / ? #), zerlegt diese URL falsch. Passwoerter aus
[A-Za-z0-9] halten dieses Format aus.

Parameter: ctx, database, user, passwordVar.
*/}}
{{- define "weave.databaseUrl" -}}
{{- $user := required "weave.databaseUrl: 'user' fehlt" .user -}}
{{- $db := required "weave.databaseUrl: 'database' fehlt" .database -}}
{{- $var := required "weave.databaseUrl: 'passwordVar' fehlt" .passwordVar -}}
{{- printf "postgresql+psycopg://%s:$(%s)@%s:%v/%s" $user $var (include "weave.postgresHost" .ctx) (include "weave.postgresPort" .ctx) $db -}}
{{- end -}}

{{/*
===========================================================================
Redis
===========================================================================
*/}}

{{- define "weave.redisHost" -}}
{{- if eq .Values.redis.mode "external" -}}
{{- if not .Values.redis.external.host -}}
{{- fail "redis.mode=external, aber redis.external.host ist leer." -}}
{{- end -}}
{{- .Values.redis.external.host -}}
{{- else -}}
{{- include "weave.componentName" (dict "ctx" . "component" "redis") -}}
{{- end -}}
{{- end -}}

{{- define "weave.redisPort" -}}
{{- if eq .Values.redis.mode "external" -}}
{{- .Values.redis.external.port | default 6379 -}}
{{- else -}}
6379
{{- end -}}
{{- end -}}

{{/*
REDIS_URL mit derselben $(VAR)-Ersetzung wie weave.databaseUrl: der
aufrufende Workload braucht den Eintrag REDIS_PASSWORD (aus
weave.secretEnv) VOR dieser Variable.

Logische Datenbank: 0 fuer Ingest, 1 fuer Knowledge (ADR-0001). Sie ist
Pflichtparameter, damit sie an der Aufrufstelle sichtbar ist statt in einem
Default zu verschwinden.

Usage: {{ include "weave.redisUrl" (dict "ctx" $ "db" 1) }}
*/}}
{{- define "weave.redisUrl" -}}
{{- if kindIs "invalid" .db -}}
{{- fail "weave.redisUrl: 'db' fehlt -- 0 fuer Ingest, 1 fuer Knowledge (ADR-0001)." -}}
{{- end -}}
{{- printf "redis://:$(REDIS_PASSWORD)@%s:%v/%v" (include "weave.redisHost" .ctx) (include "weave.redisPort" .ctx) .db -}}
{{- end -}}

{{/*
===========================================================================
Cluster-interne Adressen
===========================================================================
*/}}

{{/*
Die Adresse, unter der ein Dienst dieses Releases von einem anderen
erreichbar ist. Ein gesetzter Wert in .Values.internalUrls gewinnt; sonst
wird aus Release-Namen und dem Service-Port abgeleitet.

Parameter: ctx, component -- einer der Schluessel aus values.yaml
internalUrls: ingestBackend, knowledge, retrieval, runtime, api,
toolsBackend, embeddings, reranker.

Usage: {{ include "weave.internalUrl" (dict "ctx" $ "component" "retrieval") }}
*/}}
{{- define "weave.internalUrl" -}}
{{- $ctx := .ctx -}}
{{- $key := required "weave.internalUrl: 'component' fehlt" .component -}}
{{- $slugs := dict
      "ingestBackend" "ingest-backend"
      "knowledge" "knowledge"
      "retrieval" "retrieval"
      "runtime" "runtime"
      "api" "api"
      "toolsBackend" "tools-backend"
      "embeddings" "embeddings"
      "reranker" "reranker" -}}
{{- $slug := get $slugs $key -}}
{{- if not $slug -}}
{{- fail (printf "weave.internalUrl: '%s' ist kein bekannter Dienst. Erlaubt: %s" $key (keys $slugs | sortAlpha | join ", ")) -}}
{{- end -}}
{{- $override := get (default dict $ctx.Values.internalUrls) $key -}}
{{- if $override -}}
{{- $override -}}
{{- else -}}
{{- $workload := get $ctx.Values $key -}}
{{- $port := 8000 -}}
{{- if and $workload (kindIs "map" $workload) -}}
{{- $svc := get $workload "service" -}}
{{- if and $svc (kindIs "map" $svc) -}}
{{- $port = get $svc "port" | default 8000 -}}
{{- end -}}
{{- end -}}
{{- printf "http://%s:%v" (include "weave.componentName" (dict "ctx" $ctx "component" $slug)) $port -}}
{{- end -}}
{{- end -}}

{{/*
===========================================================================
Die mitgelieferten Modelldienste an ihre Verbraucher haengen
===========================================================================

EMBEDDING_BASE_URL und RERANK_BASE_URL sind in weave.yaml mit dem Wert ""
deklariert -- im Compose-Stack traegt der Betrieb dort von Hand
http://weave-embeddings:8000 bzw. http://weave-reranker:8000 ein. Das ist
genau die Sorte Adresse, die es im Cluster nicht gibt: Servicename und
Port haengen am Release-Namen.

Deshalb setzt das Chart die Adresse selbst -- aber NUR, wenn beides gilt:

  - der Dienst ist in diesem Release ueberhaupt eingeschaltet, und
  - die Konfiguration laesst die Adresse leer.

Steht in der Konfiguration eine Adresse, gewinnt sie. Ein Anbieter
ausserhalb des Clusters (OpenAI, ein eigener Gateway) ist damit weiter
moeglich, und weave.yaml bleibt die Quelle fuer alles, was sie wirklich
sagt. Ohne diese Naht rendert embeddings.enabled=true einen Dienst, mit
dem niemand spricht -- und ein EMBEDDING_PROVIDER=openai mit leerer
Basis-URL laeuft still gegen die echte OpenAI-API.

Parameter: ctx, service ("knowledge" oder "retrieval"). Gibt "" zurueck,
wenn nichts zu setzen ist -- die Aufrufstelle prueft das mit `with`.
*/}}
{{- define "weave.embeddingBaseUrl" -}}
{{- $ctx := .ctx -}}
{{- $service := required "weave.embeddingBaseUrl: 'service' fehlt" .service -}}
{{- if $ctx.Values.embeddings.enabled -}}
{{- $cfg := get (include "weave.mergedConfig" $ctx | fromYaml) $service | default dict -}}
{{- if not (get $cfg "EMBEDDING_BASE_URL") -}}
{{- include "weave.internalUrl" (dict "ctx" $ctx "component" "embeddings") -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "weave.rerankBaseUrl" -}}
{{- $ctx := .ctx -}}
{{- if $ctx.Values.reranker.enabled -}}
{{- $cfg := get (default dict $ctx.Values.config) "retrieval" | default dict -}}
{{- if not (get $cfg "RERANK_BASE_URL") -}}
{{- include "weave.internalUrl" (dict "ctx" $ctx "component" "reranker") -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
===========================================================================
db-bootstrap-Job: mit welchen Zugangsdaten er verbindet
===========================================================================
Der Job braucht CREATE DATABASE und CREATE ROLE. Im Modus cnpg hat der
initdb-owner die nicht -- dort ist es die postgres-Rolle aus dem
superuserSecret.
*/}}

{{- define "weave.dbBootstrapUser" -}}
{{- if .Values.dbBootstrap.superuser.username -}}
{{- .Values.dbBootstrap.superuser.username -}}
{{- else if eq .Values.postgresql.mode "cnpg" -}}
postgres
{{- else -}}
{{- include "weave.postgresUser" . -}}
{{- end -}}
{{- end -}}

{{- define "weave.dbBootstrapSecretName" -}}
{{- if .Values.dbBootstrap.superuser.passwordSecret.name -}}
{{- .Values.dbBootstrap.superuser.passwordSecret.name -}}
{{- else if eq .Values.postgresql.mode "cnpg" -}}
{{- if not .Values.postgresql.cnpg.superuserSecret.name -}}
{{- fail "postgresql.mode=cnpg und dbBootstrap.enabled=true, aber weder postgresql.cnpg.superuserSecret.name noch dbBootstrap.superuser.passwordSecret.name ist gesetzt. Der Job muss CREATE DATABASE und CREATE ROLE ausfuehren; der von bootstrap.initdb angelegte Owner darf das nicht. Entweder ein kubernetes.io/basic-auth-Secret fuer die postgres-Rolle hinterlegen oder dbBootstrap.enabled=false setzen und die drei Datenbanken samt weave_retrieval_ro von Hand anlegen (die SQL steht in deploy/postgres-init/01-create-databases.sh)." -}}
{{- end -}}
{{- .Values.postgresql.cnpg.superuserSecret.name -}}
{{- else -}}
{{- include "weave.secretName" . -}}
{{- end -}}
{{- end -}}

{{- define "weave.dbBootstrapSecretKey" -}}
{{- if .Values.dbBootstrap.superuser.passwordSecret.key -}}
{{- .Values.dbBootstrap.superuser.passwordSecret.key -}}
{{- else if eq .Values.postgresql.mode "cnpg" -}}
password
{{- else -}}
POSTGRES_PASSWORD
{{- end -}}
{{- end -}}

{{/*
===========================================================================
Fail-fast
===========================================================================
Lieber ein `helm template`, das mit einem Satz abbricht, als ein Release,
das erst im Cluster als CrashLoopBackOff auffaellt.
*/}}

{{/*
Ohne values.generated.yaml haette jeder Container eine leere Umgebung und
jeder Dienst wuerde beim Start ueber einen fehlenden Pflichtwert
stolpern -- ohne Hinweis darauf, dass schlicht eine Datei fehlt.
*/}}
{{- define "weave.requireGeneratedValues" -}}
{{- if not (include "weave.mergedConfig" . | fromYaml) -}}
{{- fail "Die Konfiguration ist leer. Normalerweise liegt values.generated.yaml im Chart und wird von selbst gelesen; fehlt sie, neu erzeugen mit\n    python scripts/weave_config.py helm-values --out deploy/charts/weave/values.generated.yaml\nDiese Datei ist die zweite Ansicht derselben weave.yaml, aus der auch deploy/.env entsteht -- sie gehoert nicht von Hand gepflegt." -}}
{{- end -}}
{{- if not (include "weave.mergedSecrets" . | fromYaml) -}}
{{- fail "Die Geheimnis-Namen fehlen: weder das Chart noch die uebergebenen Werte enthalten einen secrets-Block. values.generated.yaml neu erzeugen mit\n    python scripts/weave_config.py helm-values --out deploy/charts/weave/values.generated.yaml" -}}
{{- end -}}
{{- end -}}

{{- define "weave.requireSecrets" -}}
{{- $s := .Values.secrets -}}
{{- if and $s.existingSecret $s.create -}}
{{- fail "secrets.existingSecret UND secrets.create sind gesetzt. Genau eines von beidem: entweder ein Secret, das jemand anders fuellt (External Secrets Operator, Vault, Wrapper-Chart), oder eines, das dieses Chart aus secrets.values anlegt." -}}
{{- end -}}
{{- if and (not $s.existingSecret) (not $s.create) -}}
{{- fail "Weder secrets.existingSecret noch secrets.create ist gesetzt. Ohne Geheimnisse startet kein einziger Dienst dieser Plattform. Entweder\n    --set secrets.existingSecret=weave-platform-secrets\n(das Secret muss existieren und je Eintrag aus secrets.<dienst> einen gleichnamigen Schluessel tragen), oder\n    --set secrets.create=true --set secrets.values.POSTGRES_PASSWORD=...\n(dann steht der Wert im Klartext in der Helm-Release-Historie -- fuer den Produktivbetrieb ist existingSecret der richtige Weg)." -}}
{{- end -}}
{{- if and $s.create (not $s.values) -}}
{{- fail "secrets.create=true, aber secrets.values ist leer. Die Schluessel heissen wie die VARIABLEN aus secrets.<dienst>, nicht wie die Eintraege in weave.yaml -- also POSTGRES_PASSWORD, REDIS_PASSWORD, SECRET_KEY, RETRIEVAL_API_TOKEN und so weiter." -}}
{{- end -}}
{{- end -}}

{{- define "weave.requirePostgresMode" -}}
{{- $mode := .Values.postgresql.mode -}}
{{- if not (has $mode (list "cnpg" "external" "bundled")) -}}
{{- fail (printf "postgresql.mode=%q ist unbekannt. Erlaubt sind cnpg (Produktivbetrieb, setzt den CloudNativePG-Operator im Cluster voraus), external (bestehender Server, nichts wird gerendert) und bundled (Evaluierungspfad, ein Deployment plus PVC, kein HA)." $mode) -}}
{{- end -}}
{{- end -}}

{{/*
SEARCH_TOP_K Dokumente gehen an den Reranker, der ueber
RERANKER_MAX_DOCUMENTS mit 413 antwortet. Weave-Retrieval faengt das ab und
liefert die UNRERANKTE Reihenfolge -- die Suche funktioniert also weiter,
nur schlechter, und nichts sagt es. `weave_config.py check` prueft genau
das fuer die .env; hier gilt es fuer die Chart-Werte, sobald der
mitgelieferte Reranker ueberhaupt laeuft.
*/}}
{{- define "weave.requireRerankerLimits" -}}
{{- if .Values.reranker.enabled -}}
{{- $r := get (include "weave.mergedConfig" . | fromYaml) "retrieval" | default dict -}}
{{- $k := get (default dict .Values.config) "reranker" | default dict -}}
{{- $top := get $r "SEARCH_TOP_K" -}}
{{- $max := get $k "RERANKER_MAX_DOCUMENTS" -}}
{{- if and $top $max -}}
{{- if gt (int $top) (int $max) -}}
{{- fail (printf "config.retrieval.SEARCH_TOP_K (%v) ist groesser als config.reranker.RERANKER_MAX_DOCUMENTS (%v). Der Reranker antwortet dann mit 413, Weave-Retrieval faengt das ab und liefert die unrerankte Reihenfolge -- die Suche wird still schlechter. Beide Werte stehen in weave.yaml." $top $max) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "weave.requireRedisMode" -}}
{{- $mode := .Values.redis.mode -}}
{{- if not (has $mode (list "bundled" "external")) -}}
{{- fail (printf "redis.mode=%q ist unbekannt. Erlaubt sind bundled und external." $mode) -}}
{{- end -}}
{{- end -}}

{{/*
Ein eingeschalteter Ingress ohne Hostnamen rendert eine Regel ohne host --
also einen Catch-all, der jede Anfrage an diesen Controller auf diesen
Dienst zieht. Das faellt erst auf, wenn eine andere Anwendung im selben
Cluster nicht mehr erreichbar ist.
Usage: {{ include "weave.requireIngressHosts" (dict "ctx" $ "name" "api") }}
*/}}
{{- define "weave.requireIngressHosts" -}}
{{- $ing := get .ctx.Values.ingress .name -}}
{{- if and $ing $ing.enabled -}}
{{- if not $ing.hosts -}}
{{- fail (printf "ingress.%s.enabled=true, aber ingress.%s.hosts ist leer. Dieses Chart bringt bewusst keinen Vorgabe-Hostnamen mit -- ein Ingress ohne host faengt jede Anfrage dieses Controllers ab." .name .name) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Ein Backup, das nirgendwohin schreibt oder sich nirgends anmelden kann, ist
kein Backup -- und es faellt erst auf, wenn eines gebraucht wird.
*/}}
{{- define "weave.requireCnpgBackup" -}}
{{- $b := .Values.postgresql.cnpg.backup -}}
{{- if $b.enabled -}}
{{- if not $b.destinationPath -}}
{{- fail "postgresql.cnpg.backup.enabled=true, aber destinationPath ist leer. Ohne Ziel (z.B. s3://mein-bucket/weave) schreibt Barman nirgendwohin." -}}
{{- end -}}
{{- if not $b.inheritFromIAMRole -}}
{{- if not $b.s3CredentialsSecret.name -}}
{{- fail "postgresql.cnpg.backup.enabled=true mit inheritFromIAMRole=false, aber s3CredentialsSecret.name ist leer. Entweder inheritFromIAMRole=true setzen (Anmeldung ueber die IAM-Rolle des ServiceAccounts, IRSA/Workload Identity) oder ein Secret mit accessKeyId und secretAccessKey hinterlegen. Dieses Chart legt dieses Secret nicht an." -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Sammelpruefung. Jedes Workload-Template ruft sie als erste Zeile auf, damit
die Meldung kommt, egal welches Objekt zuerst gerendert wird.
Usage: {{- include "weave.validate" . }}
*/}}
{{- define "weave.validate" -}}
{{- include "weave.requireGeneratedValues" . -}}
{{- include "weave.requireSecrets" . -}}
{{- include "weave.requirePostgresMode" . -}}
{{- include "weave.requireRedisMode" . -}}
{{- include "weave.requireRerankerLimits" . -}}
{{- end -}}
