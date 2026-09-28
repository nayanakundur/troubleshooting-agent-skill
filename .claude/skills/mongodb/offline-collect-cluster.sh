#!/usr/bin/env bash
# MongoDB life-report collector — cluster-wide (mongosh).
#
# Run ONCE from any node that has mongosh + admin credentials. Produces
# /tmp/mongo-life-cluster.txt. Pair with offline-collect-node.sh (run on
# each replica member).
#
# Required env vars:
#   MONGO_URI   — full URI, e.g.
#                 mongodb://admin:PASS@host:27017/?authSource=admin&replicaSet=rs0
#   MONGO_TLS   — TLS flags, e.g. "--tls --tlsCAFile /path/to/ca.pem"
#                 Set to empty string ("") if TLS is disabled.
#   MONGO_DB    — optional, application database name (default: itential)

set -u
: "${MONGO_URI:?set MONGO_URI to the full mongodb:// connection string}"
: "${MONGO_TLS:?set MONGO_TLS to TLS flags, or empty string if TLS disabled}"
DB="${MONGO_DB:-itential}"

OUT=/tmp/mongo-life-cluster.txt

mongosh "$MONGO_URI" $MONGO_TLS --quiet --eval "
  const DB = '$DB';
  const out = (label, val) => { print('=== ' + label + ' ==='); print(JSON.stringify(val, null, 2)); };

  out('rs.status',  rs.status());
  out('rs.conf',    rs.conf());

  const ss = db.serverStatus();
  out('host',                 ss.host);
  out('version',              ss.version);
  out('uptime_sec',           ss.uptime);
  out('mem',                  ss.mem);
  out('wiredTiger.cache',     ss.wiredTiger.cache);
  out('writeConflicts',       ss.metrics.operation.writeConflicts);
  out('locks',                ss.locks);
  out('connections',          ss.connections);

  out('currentOp.total',      db.currentOp().inprog.length);
  out('currentOp.slow>1s',    db.currentOp({ secs_running: { \$gt: 1 } }));
  out('currentOp.aggregate',  db.currentOp({ 'command.aggregate': { \$exists: true } }));

  out('listDatabases',        db.adminCommand({ listDatabases: 1 }));

  out('profilingStatus',      db.getSiblingDB(DB).getProfilingStatus());
  print('=== system.profile top20 ===');
  db.getSiblingDB(DB).system.profile.find().sort({ millis: -1 }).limit(20)
    .forEach(d => print(JSON.stringify(d)));

  print('=== jobs ===');
  print('count: ' + db.getSiblingDB(DB).jobs.estimatedDocumentCount());
  db.getSiblingDB(DB).jobs.getIndexes().forEach(i => print(JSON.stringify(i)));

  print('=== tasks ===');
  print('count: ' + db.getSiblingDB(DB).tasks.estimatedDocumentCount());
  db.getSiblingDB(DB).tasks.getIndexes().forEach(i => print(JSON.stringify(i)));
" > "$OUT" 2>&1

echo "wrote $OUT ($(wc -l < "$OUT") lines)"
