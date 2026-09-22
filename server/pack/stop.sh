#!/usr/bin/env bash
# 立直麻将服务端 —— **停止**（先 SIGTERM，给它把回放/记录落盘的时间；超时才 SIGKILL）
#
#   ./stop.sh                最多等 ${STOP_TIMEOUT:-20} 秒
#   ./stop.sh --force        直接 SIGKILL（不推荐：可能丢最后几秒的对局记录）
#   STOP_TIMEOUT=60 ./stop.sh
#
# 幂等：没在跑就什么都不做（并清掉陈旧 pid 文件）。
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE="${PIDFILE:-run/mahjong-server.pid}"
WAIT="${STOP_TIMEOUT:-20}"
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-9) FORCE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg（只支持 --force）" >&2; exit 2 ;;
  esac
done

if [ ! -f "$PIDFILE" ]; then
  echo "未在运行（没有 $PIDFILE）。若确定有残留进程：pgrep -af 'mahjong-server.jar'"
  exit 0
fi

PID="$(cat "$PIDFILE" 2>/dev/null || true)"
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
  echo "进程已不在（清理 stale pid 文件）"
  rm -f "$PIDFILE"
  exit 0
fi

if [ "$FORCE" -eq 1 ]; then
  echo "SIGKILL pid $PID（--force）"
  kill -9 "$PID" 2>/dev/null || true
else
  echo "SIGTERM pid $PID（最多等 ${WAIT}s 让它落盘）…"
  kill "$PID" 2>/dev/null || true
  i=0
  while kill -0 "$PID" 2>/dev/null && [ "$i" -lt "$WAIT" ]; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "等待超时 → SIGKILL"
    kill -9 "$PID" 2>/dev/null || true
    sleep 1
  fi
fi

rm -f "$PIDFILE"
echo "已停止（pid $PID）"
