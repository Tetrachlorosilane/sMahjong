#!/usr/bin/env bash
# 立直麻将服务端 —— 启动脚本（前台运行；systemd 见 docs/DEPLOY.md）
#
#   ./run.sh                    监听 0.0.0.0:10086
#   PORT=9000 ./run.sh          换端口
#   HOST=127.0.0.1 ./run.sh     只监听本机
#   JAR=build/other.jar ./run.sh  指定别的 jar
#
# java 同样不写死路径：JAVA_HOME → PATH → /usr/lib/jvm、~/.sdkman 等常见位置。
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-10086}"
HOST="${HOST:-0.0.0.0}"
JAR="${JAR:-build/mahjong-server.jar}"

if [ ! -f "$JAR" ]; then
  echo "未找到 $JAR，先执行 ./build.sh" >&2
  exit 1
fi

java_bin=""
for c in "${JAVA_HOME:-}/bin/java" \
         "$(command -v java 2>/dev/null || true)" \
         /usr/lib/jvm/*/bin/java /usr/java/*/bin/java /opt/java*/bin/java \
         "$HOME"/.sdkman/candidates/java/*/bin/java \
         /Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java; do
  if [ -n "$c" ] && [ -x "$c" ]; then java_bin="$c"; break; fi
done

if [ -z "$java_bin" ]; then
  echo "未找到 java。请安装 JRE/JDK 21（如 sudo apt install -y openjdk-21-jre-headless），或设 JAVA_HOME" >&2
  exit 1
fi

exec "$java_bin" -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8 \
     -jar "$JAR" --host "$HOST" --port "$PORT" "$@"
