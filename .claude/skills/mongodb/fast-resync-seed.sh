#!/usr/bin/env bash
# Fast resync — physical file-copy seed of a MongoDB replica set member.
#
# Copies a healthy SECONDARY's WiredTiger data files directly to a target member
# under db.fsyncLock(), instead of a slow logical initial sync. No index-build
# phase → near line-rate. Use when a member is stale beyond the oplog window and
# logical initial sync is too slow or cannot complete (e.g. host instability
# resetting the VM mid-clone), or on Community edition (no fileCopyBased sync).
#
# THIS SCRIPT DOES ONLY THE SAFETY-CRITICAL COPY:
#   set default WC -> w:1  →  fsyncLock source  →  verify frozen  →  rsync  →
#   (trap) fsyncUnlock source + restore default WC to majority (ALWAYS, even on error)
#
# The surrounding steps (lower target priority, stop+wipe target, start+verify,
# failback) are done by the operator — see Task "Fast Resync" in SKILL.md.
#
# Required env vars:
#   PRIMARY_URI  mongodb:// URI to the PRIMARY (directConnection), incl. admin creds
#   SOURCE_URI   mongodb:// URI to the healthy SECONDARY used as copy source
#   TLS          mongosh TLS flags, e.g. "--tls --tlsCAFile /path/ca.pem"  ("" if off)
#   SSH_KEY      workstation private key for SSH_USER@SOURCE_HOST
#   SSH_USER     SSH user on the nodes (e.g. pet-user)
#   SOURCE_HOST  source node address (reachable from workstation AND from itself→target)
#   TARGET_HOST  target node address (reachable from SOURCE_HOST over the internal net)
#   TARGET_MEMBER  target's replica-set member id as in rs.conf (e.g. pe-mongo01:27017),
#                  used by the pre-flight guardrails to locate it
#   NODE_KEY     private key path ON SOURCE_HOST used to ssh to the target (node-to-node)
# Optional:
#   DBPATH       mongod dbPath (default /var/lib/mongo)
#   NODE_SSH_EXTRA  extra ssh opts for the node-to-node hop (e.g. "-F /dev/null" to
#                   bypass a broken ~/.ssh/config on the source node)
#   LOG          log file (default /tmp/mongo-fast-resync.log)
set -u
for v in PRIMARY_URI SOURCE_URI SSH_KEY SSH_USER SOURCE_HOST TARGET_HOST TARGET_MEMBER NODE_KEY; do
  [ -n "${!v:-}" ] || { echo "MISSING required env var: $v" >&2; exit 2; }
done
TLS="${TLS:-}"; DBPATH="${DBPATH:-/var/lib/mongo}"
NODE_SSH_EXTRA="${NODE_SSH_EXTRA:-}"; LOG="${LOG:-/tmp/mongo-fast-resync.log}"
SSHW="-i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o BatchMode=yes"
RE="ssh $NODE_SSH_EXTRA -i $NODE_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o BatchMode=yes"
: > "$LOG"; say(){ echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cleanup(){
  say "[cleanup] unlocking source + restoring default write concern to majority"
  mongosh "$SOURCE_URI" $TLS --quiet --eval \
    'try{let r;do{r=db.fsyncUnlock();}while(r.ok&&r.lockCount>0);print("unlock lockCount="+(r?r.lockCount:"?"));}catch(e){print("unlock note: "+e.message);}' >>"$LOG" 2>&1
  mongosh "$PRIMARY_URI" $TLS --quiet --eval \
    'print("restoreWC ok="+db.adminCommand({setDefaultRWConcern:1,defaultWriteConcern:{w:"majority"}}).ok)' >>"$LOG" 2>&1
  say "[cleanup] done"
}

# ---- STEP0 pre-flight guardrails (fail-closed, BEFORE any mutation / before the trap) ----
say "STEP0 pre-flight safety checks"
IS_PRIMARY=$(mongosh "$PRIMARY_URI" $TLS --quiet --eval 'print(db.hello().isWritablePrimary===true?"YES":"NO")' 2>>"$LOG" | tail -1)
[ "$IS_PRIMARY" = "YES" ] || { say "ABORT: PRIMARY_URI does not point at the primary (isWritablePrimary=$IS_PRIMARY)"; exit 3; }

SRC_STATE=$(mongosh "$SOURCE_URI" $TLS --quiet --eval 'const h=db.hello(); print((h.secondary===true && h.isWritablePrimary!==true)?"SECONDARY":"NOTSEC")' 2>>"$LOG" | tail -1)
[ "$SRC_STATE" = "SECONDARY" ] || { say "ABORT: source is not a SECONDARY ($SRC_STATE) — refusing to fsyncLock (never lock the primary)"; exit 3; }

GUARD=$(mongosh "$PRIMARY_URI" $TLS --quiet --eval '
  const TGT="'"$TARGET_MEMBER"'";
  const conf=rs.conf(), st=rs.status();
  const pr=st.members.find(m=>m.stateStr==="PRIMARY");
  if(!conf.members.find(m=>m.host===TGT)){print("TARGET_NOT_IN_CONFIG");quit();}
  if(pr && pr.name===TGT){print("TARGET_IS_PRIMARY");quit();}
  const totalVotes=conf.members.filter(m=>m.votes>0).reduce((a,m)=>a+m.votes,0);
  const majority=Math.floor(totalVotes/2)+1;
  const healthyVotes=st.members.filter(m=>m.health===1 && m.name!==TGT)
    .reduce((a,m)=>{const c=conf.members.find(x=>x.host===m.name);return a+((c&&c.votes>0)?c.votes:0);},0);
  print(healthyVotes>=majority ? ("OK healthyVotes="+healthyVotes+" majority="+majority) : ("NO_MAJORITY healthyVotes="+healthyVotes+" majority="+majority));
' 2>>"$LOG" | tail -1)
case "$GUARD" in
  OK*)                say "  guardrails PASS: source=SECONDARY, target!=primary, majority-holds-with-target-down ($GUARD)" ;;
  TARGET_IS_PRIMARY)  say "ABORT: TARGET_MEMBER is the current PRIMARY — refusing to reseed the primary"; exit 3 ;;
  TARGET_NOT_IN_CONFIG) say "ABORT: TARGET_MEMBER '$TARGET_MEMBER' not found in rs.conf — check the value"; exit 3 ;;
  NO_MAJORITY*)       say "ABORT: majority would be lost with the target down ($GUARD) — reseed only one member at a time"; exit 3 ;;
  *)                  say "ABORT: guardrail check failed/unparseable ($GUARD)"; exit 3 ;;
esac

trap cleanup EXIT

say "STEP1 default write concern -> w:1 (source will be frozen; majority is unreachable, avoids client hang)"
mongosh "$PRIMARY_URI" $TLS --quiet --eval \
  'print("setWC ok="+db.adminCommand({setDefaultRWConcern:1,defaultWriteConcern:{w:1}}).ok)' >>"$LOG" 2>&1

say "STEP2 fsyncLock source"
LC=$(mongosh "$SOURCE_URI" $TLS --quiet --eval \
  'const r=db.fsyncLock(); print((r.ok===1 && Number(r.lockCount)>=1)?"LOCKED":"FAIL")' 2>>"$LOG" | tail -1)
say "  fsyncLock result=$LC"
[ "$LC" = "LOCKED" ] || { say "ABORT: fsyncLock failed"; exit 1; }

say "STEP3 verify source replication is FROZEN (proof of a consistent copy point)"
T1=$(mongosh "$SOURCE_URI" $TLS --quiet --eval 'print(db.getSiblingDB("local").oplog.rs.find().sort({$natural:-1}).limit(1).next().ts.getHighBits())' 2>>"$LOG" | tail -1)
sleep 5
T2=$(mongosh "$SOURCE_URI" $TLS --quiet --eval 'print(db.getSiblingDB("local").oplog.rs.find().sort({$natural:-1}).limit(1).next().ts.getHighBits())' 2>>"$LOG" | tail -1)
say "  source lastApplied t1=$T1 t2=$T2"
[ -n "$T1" ] && [ "$T1" = "$T2" ] || { say "ABORT: source not frozen (t1=$T1 t2=$T2) — copy would be inconsistent"; exit 1; }

say "STEP4 rsync $SOURCE_HOST:$DBPATH -> $TARGET_HOST:$DBPATH (whole-file, node-to-node)"
ssh $SSHW "$SSH_USER@$SOURCE_HOST" \
  "sudo rsync -aH --numeric-ids -W --delete --info=stats2 \
   -e '$RE' --rsync-path='sudo rsync' \
   --exclude='mongod.lock' --exclude='diagnostic.data/' --exclude='*.sock' \
   '$DBPATH/' '$SSH_USER@$TARGET_HOST:$DBPATH/'" >>"$LOG" 2>&1
RC=$?
say "STEP4 rsync finished rc=$RC"
exit $RC
