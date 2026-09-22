#!/usr/bin/env bash
# 立直麻将服务端 —— **后台启动**（配合 stop.sh / restart.sh / status.sh / update.sh）
#
#   ./start.sh                     监听 0.0.0.0:10086
#   PORT=9000 ./start.sh           换端口
#   HOST=127.0.0.1 ./start.sh      只监听本机
#   ./start.sh --fast --no-replay  额外参数**原样透传**给 jar（见 docs/DEPLOY.md）
#   JAVA_OPTS="-Xmx512m" ./start.sh
#
# 产物：PID 写进 run/mahjong-server.pid，日志追加到 logs/mahjong-server-YYYYMMDD.log，
#       进程与终端解绑（nohup），关掉 SSH 也不会停。已在运行时不重复启动（幂等）。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-10086}"
HOST="${HOST:-0.0.0.0}"
JAR="${JAR:-mahjong-server.jar}"
PIDFILE="${PIDFILE:-run/mahjong-server.pid}"
LOGDIR="${LOGDIR:-logs}"

if [ ! -f "$JAR" ]; then
  echo "错误：找不到 $JAR（当前目录 $(pwd)）" >&2
  exit 1
fi

mkdir -p "$(dirname "$PIDFILE")" "$LOGDIR"

if [ -f "$PIDFILE" ]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
    echo "已在运行：pid $old（要重启用 ./restart.sh）"
    exit 0
  fi
  rm -f "$PIDFILE"        # 陈旧 pid 文件（上次没停干净 / 机器重启过）
fi

# java 不写死路径：PATH → JAVA_HOME → 常见安装位置
JAVA_BIN="$(command -v java 2>/dev/null || true)"
if [ -z "$JAVA_BIN" ] && [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
  JAVA_BIN="$JAVA_HOME/bin/java"
fi
if [ -z "$JAVA_BIN" ]; then
  echo "错误：找不到 java（需要 JRE 17+）。安装：sudo apt install -y openjdk-21-jre-headless" >&2
  exit 1
fi

LOG="$LOGDIR/mahjong-server-$(date +%Y%m%d).log"
# 说明：${JAVA_OPTS:-} 故意不加引号 —— 让 "-Xmx512m -Xss1m" 这类参数按空格拆开
# shellcheck disable=SC2086
nohup "$JAVA_BIN" ${JAVA_OPTS:-} -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8 \
  -jar "$JAR" --host "$HOST" --port "$PORT" "$@" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

sleep 1
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "已启动：pid $(cat "$PIDFILE")  $HOST:$PORT  日志：$LOG"
  echo "（停止：./stop.sh    看状态：./status.sh    更新：./update.sh）"
else
  echo "启动失败，日志尾部：" >&2
  tail -n 20 "$LOG" >&2 || true
  rm -f "$PIDFILE"
  exit 1
fi
