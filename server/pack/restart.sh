#!/usr/bin/env bash
# 立直麻将服务端 —— **重启**（= stop.sh + start.sh；参数透传给 start.sh）
#
#   ./restart.sh                换端口：PORT=9000 ./restart.sh
#   STOP_TIMEOUT=60 ./restart.sh
set -euo pipefail
cd "$(dirname "$0")"

here="$(cd "$(dirname "$0")" && pwd)"
"$here/stop.sh"
"$here/start.sh" "$@"
