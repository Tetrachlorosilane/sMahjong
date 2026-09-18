#!/usr/bin/env bash
# 立直麻将服务端 —— 构建脚本（Linux / macOS / WSL）
#
#   ./build.sh                 编译 + 打包 → build/mahjong-server.jar
#   ./build.sh --selftest      顺带跑一次规则引擎自检
#   JDK_HOME=/path ./build.sh  指定 JDK（默认自动探测）
#   MJ_RELEASE=21 ./build.sh   改用 --release 21 编译（默认 17，见下）
#   MJ_OUT=dist ./build.sh     换产出目录（默认 build）
#
# 为什么不依赖本机环境：
#   本脚本**不写死任何 JDK 路径**，按 JDK_HOME → JAVA_HOME → PATH →
#   /usr/lib/jvm、~/.sdkman、/Library/Java/... 依次找 javac，并校验版本 ≥ 17；
#   找不到时直接给出各发行版的安装命令。
#
#   默认用 `--release 17` 编译：这样**用 JDK 21 构建出来的 jar 也能跑在 JDK 17 上**，
#   产物不再取决于构建机的 JDK 版本（与 docs/DEPLOY.md 的「JDK 17+」一致）。
#   想用本机默认目标版本就设 MJ_RELEASE= 。
set -euo pipefail

cd "$(dirname "$0")"

SRC=src/main/java
OUT="${MJ_OUT:-build}"
RELEASE="${MJ_RELEASE:-17}"
CLASSES="$OUT/classes"
JAR="$OUT/mahjong-server.jar"
SELFTEST=0

for arg in "$@"; do
  case "$arg" in
    --selftest) SELFTEST=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg（只支持 --selftest）" >&2; exit 2 ;;
  esac
done

# --- 1) 找 JDK（javac ≥ 17，多个候选取版本最高的那个）-----------------------
cands=()
[ -n "${JDK_HOME:-}" ] && cands+=("$JDK_HOME")
[ -n "${JAVA_HOME:-}" ] && cands+=("$JAVA_HOME")
if command -v javac >/dev/null 2>&1; then
  real="$(readlink -f "$(command -v javac)" 2>/dev/null || command -v javac)"
  cands+=("$(dirname "$(dirname "$real")")")
fi
for d in /usr/lib/jvm/* /usr/java/* /opt/java* /opt/jdk* \
         "$HOME"/.sdkman/candidates/java/* "$HOME"/.jdks/* \
         /Library/Java/JavaVirtualMachines/*/Contents/Home /snap/*/*/jdk*; do
  [ -d "$d" ] && cands+=("$d")
done

best=""
best_major=0
best_ver=""
for h in ${cands[@]+"${cands[@]}"}; do
  [ -n "$h" ] || continue
  jc="$h/bin/javac"
  [ -x "$jc" ] || continue
  v="$("$jc" -version 2>&1 | head -n1)"
  major="$(printf '%s' "$v" | sed -E 's/^[^0-9]*([0-9]+).*/\1/')"
  case "$major" in ''|*[!0-9]*) continue ;; esac
  if [ "$major" -ge 17 ] && [ "$major" -gt "$best_major" ]; then
    best="$h"; best_major="$major"; best_ver="$v"
  fi
done

if [ -z "$best" ]; then
  cat >&2 <<'EOF'
错误：未找到可用的 JDK（需要 javac ≥ 17）。安装方式：
  Debian/Ubuntu : sudo apt update && sudo apt install -y openjdk-21-jdk-headless
  RHEL/Fedora   : sudo dnf install -y java-21-openjdk-devel
  macOS         : brew install openjdk@21
  已有 JDK      : JDK_HOME=/path/to/jdk ./build.sh   （或设 JAVA_HOME）
EOF
  exit 1
fi

JAVAC="$best/bin/javac"
JARBIN="$best/bin/jar"
JAVA="$best/bin/java"
if [ ! -x "$JARBIN" ]; then
  echo "错误：$best/bin 下没有 jar —— 这看起来是 JRE 而不是 JDK" >&2
  exit 1
fi
echo "==> JDK   : $best  ($best_ver)"

# --release 需要 JDK 9+；MJ_RELEASE= 表示用本机默认目标版本
rel_flag=()
if [ -n "$RELEASE" ] && "$JAVAC" --help 2>&1 | grep -q -- '--release'; then
  rel_flag=(--release "$RELEASE")
  echo "==> 目标  : --release $RELEASE"
else
  echo "==> 目标  : 本机默认"
fi

echo "==> 清理"
rm -rf "$CLASSES"
mkdir -p "$CLASSES"

echo "==> 编译"
if ! find "$SRC" -name '*.java' | LC_ALL=C sort > "$OUT/sources.txt"; then
  echo "错误：在 $SRC 下没找到任何 .java 文件" >&2
  exit 1
fi
if [ ! -s "$OUT/sources.txt" ]; then
  echo "错误：在 $SRC 下没找到任何 .java 文件" >&2
  exit 1
fi
"$JAVAC" -encoding UTF-8 ${rel_flag[@]+"${rel_flag[@]}"} -d "$CLASSES" "@$OUT/sources.txt"

echo "==> 打包"
"$JARBIN" --create --file "$JAR" --main-class mahjong.Main -C "$CLASSES" .

size="$(du -h "$JAR" 2>/dev/null | cut -f1 || echo '?')"
echo "==> 完成: $JAR  ($size)"
echo
echo "运行："
echo "  $JAVA -jar $JAR                 # 监听 0.0.0.0:10086"
echo "  $JAVA -jar $JAR --port 9000 -v  # 自定义端口 + 调试日志"
echo "  $JAVA -jar $JAR --selftest      # 规则引擎自测"

if [ "$SELFTEST" -eq 1 ]; then
  echo
  echo "==> 自检 --selftest"
  exec "$JAVA" -jar "$JAR" --selftest
fi
