#!/usr/bin/env bash
# Lint- und Render-Test fuer den CNPG-Teil von deploy/charts/weave.
#
# Prueft, dass
#   - das Chart mit Vorgaben und mit dem Beispiel lintet,
#   - das Beispiel examples/cnpg-ha-barman-cloud.yaml genau die Felder fuer
#     Zonen-Affinity, Switchover, synchrone Replikation und Plugin-Backup
#     erzeugt (Stufe 1 ohne, Stufe 2 mit ScheduledBackup),
#   - bei abgeschaltetem Plugin barmancloud.cnpg.io nirgends vorkommt,
#   - die Pruefungen in _helpers.tpl mit ihrer Meldung abbrechen.
#
# Aufruf: scripts/check_chart_cnpg.sh   (braucht helm im PATH)
# Exit 0, wenn alle Faelle passen; sonst 1 und eine Liste.
set -uo pipefail

cd "$(dirname "$0")/.."
CHART=deploy/charts/weave
EX="$CHART/examples/cnpg-ha-barman-cloud.yaml"
P=postgresql.cnpg
failures=0
out=""

bad() {
  echo "FAIL: $*"
  failures=$((failures + 1))
}

# render <beschreibung> <helm-args...>: setzt $out, meldet einen Fehlschlag.
render() {
  local desc=$1
  shift
  if ! out=$(helm template weave "$CHART" "$@" 2>&1); then
    bad "$desc: helm template schlug fehl"
    echo "$out" | tail -n 5
    out=""
    return 1
  fi
}

has() { grep -qF -- "$2" <<<"$out" || bad "$1: erwartet '$2'"; }
lacks() { ! grep -qF -- "$2" <<<"$out" || bad "$1: unerwartet '$2'"; }
count() {
  local n
  n=$(grep -cF -- "$3" <<<"$out")
  [ "$n" -eq "$2" ] || bad "$1: '$3' ${n}x statt ${2}x"
}

# expect_fail <beschreibung> <teil der meldung> <helm-args...>
expect_fail() {
  local desc=$1 msg=$2
  shift 2
  if out=$(helm template weave "$CHART" "$@" 2>&1); then
    bad "$desc: Abbruch erwartet, Rendering lief durch"
  elif ! grep -qF -- "$msg" <<<"$out"; then
    bad "$desc: Meldung '$msg' fehlt"
    echo "$out" | tail -n 3
  fi
}

helm lint "$CHART" --set secrets.existingSecret=x >/dev/null || bad "helm lint mit Vorgaben"
helm lint "$CHART" -f "$EX" >/dev/null || bad "helm lint mit $EX"

d="Beispiel, Stufe 1 (archiving=false)"
if render "$d" -f "$EX"; then
  has "$d" "kind: ObjectStore"
  has "$d" "apiVersion: barmancloud.cnpg.io/v1"
  has "$d" "primaryUpdateMethod: switchover"
  has "$d" "topologyKey: topology.kubernetes.io/zone"
  has "$d" "podAntiAffinityType: required"
  has "$d" "dataDurability: preferred"
  has "$d" "name: barman-cloud.cloudnative-pg.io"
  has "$d" "barmanObjectName: \"weave-pg\""
  has "$d" "enabled: false"
  has "$d" "encryption: \"aws:kms\""
  has "$d" "archiveAdditionalCommandArgs:"
  count "$d" 2 "- \"--sse-kms-key-id=alias/<key>\""
  has "$d" "instanceSidecarConfiguration:"
  lacks "$d" "kind: ScheduledBackup"
  lacks "$d" "minSyncReplicas"
fi

d="Beispiel, Stufe 2 (archiving=true)"
if render "$d" -f "$EX" --set "$P.barmanCloudPlugin.archiving=true"; then
  has "$d" "kind: ScheduledBackup"
  has "$d" "method: plugin"
  has "$d" "isWALArchiver: true"
  has "$d" "suspend: true"
  has "$d" "schedule: \"0 0 2 * * *\""
fi

d="Plugin aus"
if render "$d" -f "$EX" --set "$P.barmanCloudPlugin.enabled=false"; then
  lacks "$d" "barmancloud.cnpg.io"
  lacks "$d" "kind: ScheduledBackup"
  lacks "$d" "plugins:"
fi

expect_fail "synchronous mit minSyncReplicas" "altes und neues API" \
  -f "$EX" --set "$P.minSyncReplicas=1" --set "$P.maxSyncReplicas=1"
expect_fail "synchronous.number >= instances" "synchronous.number=2" \
  -f "$EX" --set "$P.synchronous.number=2"
expect_fail "Plugin mit eingebautem Backup" "Cannot enable a WAL archiver plugin" \
  -f "$EX" --set "$P.backup.enabled=true" --set "$P.backup.destinationPath=s3://x/" \
  --set "$P.backup.inheritFromIAMRole=true"
expect_fail "destinationPath leer" "destinationPath ist leer" \
  -f "$EX" --set "$P.barmanCloudPlugin.destinationPath="
expect_fail "ohne IAM-Rolle und Secret" "s3CredentialsSecret.name ist leer" \
  -f "$EX" --set "$P.barmanCloudPlugin.inheritFromIAMRole=false"
expect_fail "kmsKeyId ohne aws:kms" "Ein KMS-Key wirkt nur mit encryption: aws:kms" \
  -f "$EX" --set "$P.barmanCloudPlugin.encryption=AES256"

if [ "$failures" -ne 0 ]; then
  echo "$failures Fehler."
  exit 1
fi
echo "CNPG-Render-Test: alle Faelle bestanden."
