#!/usr/bin/env bash
# Launch the ThetaData Terminal — local market-data feed used by the PEAD
# options source (data/options.py talks to http://127.0.0.1:25503).
#
# One-time setup:
#   1. Java 21+ (you have it) and the jar at var/thetadata/ThetaTerminalv3.jar (downloaded).
#   2. Create var/thetadata/creds.txt  (gitignored):
#         line 1 = your ThetaData email
#         line 2 = your ThetaData password
#   3. Run this in its OWN terminal (it's a long-running server):
#         ./scripts/start_thetadata.sh
#      It hosts the REST API on 127.0.0.1:25503.
#
# Then `ats pead prep <SYM>` will use ThetaData for Expected Move / IV / skew
# (falling back to yfinance if the terminal is down).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/var/thetadata"
JAR="$DIR/ThetaTerminalv3.jar"

[ -f "$JAR" ] || { echo "Missing $JAR — download ThetaTerminalv3.jar into var/thetadata/." >&2; exit 1; }
[ -f "$DIR/creds.txt" ] || {
  echo "Missing $DIR/creds.txt (line1=ThetaData email, line2=password)." >&2
  exit 1
}

cd "$DIR"
echo "Starting ThetaData Terminal (REST on 127.0.0.1:25503). Ctrl-C to stop."
exec java -jar ThetaTerminalv3.jar --creds-file creds.txt
