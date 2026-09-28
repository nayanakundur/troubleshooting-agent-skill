#!/usr/bin/env bash
# MongoDB life-report collector — per-node (VM/guest scope only).
#
# Run on EACH MongoDB replica member. Produces /tmp/mongo-life-<host>.txt.
# Pair with offline-collect-cluster.sh (run once from any node with mongosh + admin creds).
# Bundle all outputs with:
#   tar czf mongo-life-$(date -u +%Y%m%d-%H%M).tgz /tmp/mongo-life-*.txt
#
# Scope: guest-VM signals only. Physical disk SMART / ECC / RAID health must be
# collected on the hypervisor host separately.

set -u
HOST=$(hostname -s)
OUT=/tmp/mongo-life-${HOST}.txt

{
  echo "### node: $HOST  ts: $(date -u +%FT%TZ)"

  echo; echo "=== system ==="
  uname -a
  cat /etc/os-release 2>/dev/null | head -5
  uptime

  echo; echo "=== cpu / memory (vmstat 1 5) ==="
  vmstat 1 5

  echo; echo "=== memory (free) ==="
  free -h

  echo; echo "=== disk i/o (iostat -x 1 5) ==="
  iostat -x 1 5 2>&1 || echo "iostat not installed (dnf install -y sysstat)"

  echo; echo "=== disk capacity ==="
  df -hT
  echo
  df -i

  echo; echo "=== mongo data dir size ==="
  for p in /var/lib/mongo /data/db /var/lib/mongodb; do
    [ -d "$p" ] && sudo du -sh "$p" 2>/dev/null
  done

  echo; echo "=== block devices ==="
  lsblk -f

  echo; echo "=== THP ==="
  cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null
  cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null

  echo; echo "=== kernel errors / warnings (dmesg) ==="
  sudo dmesg -T --level=err,warn 2>/dev/null | tail -50

  echo; echo "=== boot-level errors (journal -p err) ==="
  sudo journalctl -p err -b --no-pager 2>/dev/null | tail -50

  echo; echo "=== OOM history (journal) ==="
  sudo journalctl -k --no-pager 2>/dev/null | grep -iE 'killed process|out of memory' | tail -20

  echo; echo "=== mongod service ==="
  systemctl show mongod -p ActiveState,SubState,ActiveEnterTimestamp,NRestarts 2>/dev/null
  sudo systemctl status mongod --no-pager 2>/dev/null | head -20

  echo; echo "=== mongod.conf ==="
  sudo cat /etc/mongod.conf 2>/dev/null

  echo; echo "=== mongod log anomalies (last 50) ==="
  sudo grep -E '"s":"E"|"s":"W"|REPL|assertion|INITSYNC' /var/log/mongodb/mongod.log 2>/dev/null | tail -50
} > "$OUT" 2>&1

echo "wrote $OUT ($(wc -l < "$OUT") lines)"
