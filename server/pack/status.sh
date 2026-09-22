#!/usr/bin/env bash
# 立直麻将服务端 —— 查看**运行状态**（进程 / 端口 / 版本 / 日志尾部），只读，不动进程。
#
#   ./status.sh           看本机 10086
#   PORT=9000 ./status.sh
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE="${PIDFILE:-run/mahjong-server.pid}"
PORT="${PORT:-10086}"
LOGDIR="${LOGDIR:-logs}"

echo "== 版本 =="
if [ -f VERSION ]; then cat VERSION; else echo "(没有 VERSION 文件)"; fi

echo
echo "== 进程 =="
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  pid="$(cat "$PIDFILE")"
  echo "运行中：pid $pid"
  # 启动时间（Linux）/ 命令行（尽力而为，缺工具不报错）
  ps -o pid=,etime=,rss=,args= -p "$pid" 2>/dev/null || true
else
  echo "未运行（$PIDFILE 不存在或进程已退出）"
  echo "残留进程检查：pgrep -af 'mahjong-server.jar' || true"
fi

echo
echo "== 端口 $PORT =="
if command -v ss >/dev/null 2>&1; then
  ss -ltnp 2>/dev/null | grep -E "[:.]$PORT\b" || echo "没有监听（或需要 root 才看得到进程名）"
elif command -v netstat >/dev/null 2>&1; then
  netstat -ltnp 2>/dev/null | grep -E "[:.]$PORT\b" || echo "没有监听"
else
  echo "(没有 ss / netstat，跳过)"
fi

echo
echo "== 日志尾部 =="
if [ -d "$LOGDIR" ]; then
  latest="$(ls -1t "$LOGDIR"/mahjong-server-*.log 2>/dev/null | head -n1 || true)"
  if [ -n "$latest" ]; then
    echo "($latest)"
    tail -n 20 "$latest"
  else
    echo "(没有日志文件)"
  fi
else
  echo "(没有 $LOGDIR 目录)"
fi
