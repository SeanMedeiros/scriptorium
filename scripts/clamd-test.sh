#!/usr/bin/env bash
# Start a real ClamAV daemon for development/tests on 127.0.0.1:${CLAMD_PORT:-3310}.
# When the official signature databases (freshclam) are unreachable, the daemon is loaded with
# deploy/clamav/scriptorium-test.ndb, a one-line body signature matching the EICAR test string,
# so the full INSTREAM path (engine, protocol, detection, fail-closed) is exercised for real.
# Production deployments use freshclam-maintained official databases (docs/security.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${CLAMD_PORT:-3310}"
WORK="${CLAMD_WORKDIR:-/tmp/scriptorium-clamd}"
mkdir -p "$WORK/db"
if ls /var/lib/clamav/*.c[lv]d >/dev/null 2>&1; then cp /var/lib/clamav/*.c[lv]d "$WORK/db/"; fi
cp "$ROOT/deploy/clamav/scriptorium-test.ndb" "$WORK/db/"
cat > "$WORK/clamd.conf" <<CONF
Foreground yes
TCPSocket $PORT
TCPAddr 127.0.0.1
DatabaseDirectory $WORK/db
LogFile $WORK/clamd.log
PidFile $WORK/clamd.pid
StreamMaxLength 600M
MaxFileSize 600M
MaxScanSize 600M
CONF
if [ "${1:-}" = "--foreground" ]; then exec clamd -c "$WORK/clamd.conf"; fi
clamd -c "$WORK/clamd.conf" &
for _ in $(seq 1 60); do
  if printf 'zPING\0' | timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT; cat >&3; timeout 1 cat <&3" 2>/dev/null | grep -q PONG; then
    echo "clamd ready on 127.0.0.1:$PORT"; exit 0; fi
  sleep 1
done
echo "clamd did not start; see $WORK/clamd.log" >&2; exit 1
