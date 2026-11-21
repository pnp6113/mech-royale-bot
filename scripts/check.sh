#!/usr/bin/env bash
# Usage: scripts/check.sh [local|docker]   (default: both)
# local  = run the bot directly through the conformance check
# docker = build the image and check it inside the exact match sandbox the scorer uses
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
mode=${1:-both}
if [[ $mode == local || $mode == both ]]; then
  $PY kit/check/check.py -- $PY -u main.py
fi
if [[ $mode == docker || $mode == both ]]; then
  docker build -t mech-bot .
  $PY kit/check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m \
    --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "$PWD/kit/arena:/arena:ro" mech-bot
fi
