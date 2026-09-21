#!/usr/bin/env bash
#
# WAL-archiving probe: barman-cloud against Cloudflare R2 (wayfinder #18,
# scope added by the hosting ticket #9 / ADR 0009).
#
# Question it settles: does barman-cloud — the engine inside CNPG's Barman
# Cloud Plugin — archive WAL to R2 and restore from it? No primary source
# confirms R2; this is a full archive → backup → restore → row-count
# round-trip in a throwaway local postgres:17 Docker container, using the
# Debian barman-cli-cloud package from the PGDG repo the image ships with.
#
# Standalone: needs only Docker and the R2_* values in probe.env next to this
# script. Writes out/barman-probe.log; leaves objects under
# s3://$R2_BUCKET/barman-probe/ for inspection.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$DIR/out"

# probe.env is data, not shell — AUTH_TOKEN may hold arbitrary characters,
# so parse the four keys we need instead of sourcing the file. The || true
# keeps a no-match grep from killing the script under pipefail before the
# friendly "missing" messages below can fire.
[[ -f "$DIR/probe.env" ]] || { echo "probe.env not found — run wizard.sh stages 2-3 first"; exit 1; }
getval() { { grep -E "^$1=" "$DIR/probe.env" 2>/dev/null || true; } | tail -n1 | cut -d= -f2-; }
R2_ACCOUNT_ID="$(getval R2_ACCOUNT_ID)"
R2_BUCKET="$(getval R2_BUCKET)"
R2_ACCESS_KEY_ID="$(getval R2_ACCESS_KEY_ID)"
R2_SECRET_ACCESS_KEY="$(getval R2_SECRET_ACCESS_KEY)"
for v in R2_ACCOUNT_ID R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
  [[ -n "${!v}" ]] || { echo "missing $v in probe.env — run wizard.sh stages 2-3 first"; exit 1; }
done

ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
DEST="s3://${R2_BUCKET}/barman-probe"

docker run --rm \
  -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  -e AWS_DEFAULT_REGION=auto \
  -e ENDPOINT="$ENDPOINT" -e DEST="$DEST" \
  --entrypoint bash postgres:17 -euo pipefail -c '
    echo "=== install barman-cli-cloud (PGDG apt package) ==="
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends barman-cli-cloud >/dev/null
    barman-cloud-wal-archive --version

    echo "=== init + configure archiving to R2 ==="
    export PGDATA=/var/lib/postgresql/probe
    mkdir -p "$PGDATA" && chown -R postgres:postgres "$PGDATA"
    run_pg() { su postgres -c "PATH=/usr/lib/postgresql/17/bin:$PATH AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=auto $1"; }
    run_pg "initdb -D $PGDATA" >/dev/null
    cat >> "$PGDATA/postgresql.conf" <<CONF
archive_mode = on
archive_command = '\''barman-cloud-wal-archive --cloud-provider aws-s3 --endpoint-url $ENDPOINT $DEST pg1 %p'\''
wal_level = replica
CONF
    run_pg "pg_ctl -D $PGDATA -l /tmp/pg.log start"

    echo "=== generate WAL + verify archiving ==="
    run_pg "psql -c \"CREATE TABLE probe AS SELECT g id, md5(g::text) v FROM generate_series(1,100000) g;\""
    run_pg "psql -c \"SELECT pg_switch_wal(); CHECKPOINT;\""
    ARCH=0
    for i in $(seq 1 12); do
      run_pg "psql -At -c \"SELECT archived_count, coalesce(last_archived_wal,'\'''\''), coalesce(last_failed_wal,'\''<none>'\'') FROM pg_stat_archiver;\"" > /tmp/archiver.txt 2>/dev/null || true
      cat /tmp/archiver.txt
      ARCH=$(cut -d"|" -f1 /tmp/archiver.txt)
      ARCH="${ARCH//[^0-9]/}"; ARCH="${ARCH:-0}"
      [ "$ARCH" -gt 0 ] && break
      echo "  (waiting for the archiver, attempt $i/12)"
      sleep 5
    done
    if [ "$ARCH" -eq 0 ]; then
      echo "BARMAN_PROBE_FAIL: nothing archived after 60s"
      tail -50 /tmp/pg.log
      exit 1
    fi

    echo "=== base backup to R2 ==="
    run_pg "barman-cloud-backup --cloud-provider aws-s3 --endpoint-url $ENDPOINT $DEST pg1"
    run_pg "barman-cloud-backup-list --cloud-provider aws-s3 --endpoint-url $ENDPOINT $DEST pg1"

    echo "=== restore round-trip ==="
    run_pg "pg_ctl -D $PGDATA stop" >/dev/null
    BACKUP_ID=$(run_pg "barman-cloud-backup-list --cloud-provider aws-s3 --endpoint-url $ENDPOINT $DEST pg1" | awk "NR>1{print \$1; exit}")
    echo "restoring backup $BACKUP_ID"
    RESTORE=/var/lib/postgresql/restore
    mkdir -p $RESTORE && chown postgres:postgres $RESTORE
    run_pg "barman-cloud-restore --cloud-provider aws-s3 --endpoint-url $ENDPOINT $DEST pg1 $BACKUP_ID $RESTORE"
    cat >> "$RESTORE/postgresql.conf" <<CONF
port = 5433
archive_mode = off
restore_command = '\''barman-cloud-wal-restore --cloud-provider aws-s3 --endpoint-url $ENDPOINT $DEST pg1 %f %p'\''
CONF
    run_pg "touch $RESTORE/recovery.signal"
    run_pg "pg_ctl -D $RESTORE -l /tmp/pg-restore.log start"
    COUNT=""
    for i in $(seq 1 20); do
      COUNT=$(run_pg "psql -p 5433 -At -c \"SELECT count(*) FROM probe;\"" 2>/dev/null || true)
      [ "$COUNT" = "100000" ] && break
      echo "  (waiting for recovery to finish, attempt $i/20)"
      sleep 3
    done
    echo "restored row count: ${COUNT:-<none>} (expected 100000)"
    if [ "$COUNT" = "100000" ]; then
      echo "BARMAN_PROBE_PASS"
    else
      echo "BARMAN_PROBE_FAIL: wrong row count"
      tail -50 /tmp/pg-restore.log
      exit 1
    fi
  ' 2>&1 | tee "$DIR/out/barman-probe.log"

echo
echo "Transcript saved to out/barman-probe.log — look for BARMAN_PROBE_PASS above."
