#!/usr/bin/env bash
#
# WAL-archiving probe: barman-cloud against Cloudflare R2 (wayfinder #18,
# scope added by the hosting ticket #9 / ADR 0009).
#
# Question it settles: does barman-cloud — the engine inside CNPG's Barman
# Cloud Plugin — archive WAL to R2 and restore from it? No primary source
# confirms R2; this is a full archive → backup → restore → row-count
# round-trip in a throwaway local postgres:17 Docker container.
#
# Reads R2_* from probe.env next to this script; writes out/barman-probe.log.
# Leaves objects under s3://$R2_BUCKET/barman-probe/ for inspection.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
set -a; source "$DIR/probe.env"; set +a
mkdir -p "$DIR/out"

ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
DEST="s3://${R2_BUCKET}/barman-probe"

docker run --rm \
  -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  -e AWS_DEFAULT_REGION=auto \
  -e ENDPOINT="$ENDPOINT" -e DEST="$DEST" \
  --entrypoint bash postgres:17 -euo pipefail -c '
    echo "=== install barman[cloud] ==="
    apt-get update -qq && apt-get install -y -qq python3-pip python3-venv >/dev/null
    python3 -m venv /opt/barman
    /opt/barman/bin/pip -q install "barman[cloud]"
    export PATH=/opt/barman/bin:$PATH
    barman-cloud-wal-archive --version

    echo "=== init + configure archiving to R2 ==="
    export PGDATA=/var/lib/postgresql/probe
    mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" /opt/barman -R
    run_pg() { su postgres -c "PATH=/opt/barman/bin:/usr/lib/postgresql/17/bin:$PATH AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=auto $1"; }
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
    sleep 5
    run_pg "psql -At -c \"SELECT archived_count, last_archived_wal, coalesce(last_failed_wal,'\''<none>'\'') FROM pg_stat_archiver;\"" | tee /tmp/archiver.txt
    grep -qv "^0|" /tmp/archiver.txt || { echo "BARMAN_PROBE_FAIL: nothing archived"; exit 1; }

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
    sleep 3
    COUNT=$(run_pg "psql -p 5433 -At -c \"SELECT count(*) FROM probe;\"")
    echo "restored row count: $COUNT (expected 100000)"
    [ "$COUNT" = "100000" ] && echo "BARMAN_PROBE_PASS" || { echo "BARMAN_PROBE_FAIL: wrong row count"; tail -50 /tmp/pg-restore.log; exit 1; }
  ' 2>&1 | tee "$DIR/out/barman-probe.log"

echo
echo "Transcript saved to out/barman-probe.log — look for BARMAN_PROBE_PASS above."
