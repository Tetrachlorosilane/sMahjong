#!/usr/bin/env bash
# 立直麻将服务端 —— **自动获取并安装更新**（从 GitHub Release 拉服务端包，校验后原地替换）
#
#   ./update.sh                 更新到**最新** release；本来在跑就自动重启
#   ./update.sh --tag v1.8.0    指定 tag
#   ./update.sh --check         只查有没有新版（不下载、不安装、不动进程）
#   ./update.sh --force         即使远端摘要与本地记录相同也重装
#   ./update.sh --no-restart    装完不重启（自己挑时间 ./restart.sh）
#   ./update.sh --sha256 <hex>  额外用你手上的 sha256 兜底校验
#   REPO=owner/repo ./update.sh
#
# ## 怎么判断"有没有新版"
#
#   ① **tag 不同** → 有新版；
#   ② tag 相同但**资产摘要变了** → 也算有新版。第二条是必须的：本仓库修 bug 时
#      **不换版本号、原地重发同名资产**（`v1.8.0` 就这样重发过两次），只比 tag
#      会永远停在"已是最新"。所以这里比的是 GitHub API 给的 `digest`（sha256）
#      与本地 `run/installed.sha256` —— 下载后还会**再算一遍**核对。
#
# 依赖：curl 或 wget、unzip、sha256sum（或 shasum）。Ubuntu 默认全都有。
set -euo pipefail
cd "$(dirname "$0")"

REPO="${REPO:-Tetrachlorosilane/sMahjong}"
TAG=""
CHECK=0
FORCE=0
RESTART="auto"
EXTRA_SHA=""
STATE_DIR="run"
PREFIX="sMahjong-server-"          # 资产名前缀（zip 内也有一层同名目录）

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:-}"; shift 2 ;;
    --check) CHECK=1; shift ;;
    --force) FORCE=1; shift ;;
    --no-restart) RESTART=no; shift ;;
    --restart) RESTART=yes; shift ;;
    --sha256) EXTRA_SHA="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- 工具探测
fetch() {                          # fetch <url> → stdout
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$1"
  else
    echo "错误：需要 curl 或 wget" >&2; return 1
  fi
}
download() {                       # download <url> <outfile>
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 2 -o "$2" "$1"
  else
    wget -q -O "$2" "$1"
  fi
}
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else echo ""; fi
}

for t in unzip; do
  command -v "$t" >/dev/null 2>&1 || { echo "错误：缺少 $t（sudo apt install -y unzip）" >&2; exit 1; }
done

mkdir -p "$STATE_DIR"
LOCAL_TAG=""; [ -f VERSION ] && LOCAL_TAG="$(head -n1 VERSION | tr -d '[:space:]')"
LOCAL_SHA=""; [ -f "$STATE_DIR/installed.sha256" ] && LOCAL_SHA="$(awk '{print $1}' "$STATE_DIR/installed.sha256")"

# ---------------------------------------------------------------- 查远端
API="https://api.github.com/repos/$REPO/releases"
if [ -n "$TAG" ]; then
  URL_JSON="$API/tags/$TAG"
else
  URL_JSON="$API/latest"
fi
echo "==> 查询 $URL_JSON"
JSON="$(fetch "$URL_JSON")" || { echo "错误：查不到 release（网络？仓库私有？）" >&2; exit 1; }

if [ -z "$TAG" ]; then
  TAG="$(printf '%s' "$JSON" | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
fi
[ -n "$TAG" ] || { echo "错误：响应里没有 tag_name" >&2; exit 1; }

ASSET="${PREFIX}${TAG}.zip"
ASSET_URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
# 取**本资产**的 digest：先把 JSON 从资产名处断行，只在这一段里找 digest
REMOTE_SHA="$(printf '%s' "$JSON" \
  | sed "s/\"name\":\"$ASSET\"/\n&/" \
  | sed -n '2p' \
  | grep -o '"digest":"sha256:[0-9a-f]*"' | head -n1 | cut -d: -f3 || true)"

echo "    远端 tag      : $TAG"
echo "    本地 VERSION  : ${LOCAL_TAG:-（未知）}"
echo "    远端 sha256   : ${REMOTE_SHA:-（API 没给，跳过摘要比对）}"
echo "    本地 sha256   : ${LOCAL_SHA:-（没有记录）}"

if [ "$TAG" = "${LOCAL_TAG:-}" ] && [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "${LOCAL_SHA:-}" ] && [ "$FORCE" -eq 0 ]; then
  echo "==> 已是最新（tag 与资产摘要都一致）；要强制重装用 --force"
  exit 0
fi

if [ "$CHECK" -eq 1 ]; then
  if [ "$TAG" = "${LOCAL_TAG:-}" ]; then
    echo "==> 有新内容：同一个 tag（$TAG）下的资产变了（原地重发）"
  else
    echo "==> 有新版本：$TAG（本地 ${LOCAL_TAG:-未知}）"
  fi
  exit 0
fi

# ---------------------------------------------------------------- 下载 + 校验
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "==> 下载 $ASSET_URL"
download "$ASSET_URL" "$TMP/$ASSET"

GOT_SHA="$(sha256_of "$TMP/$ASSET")"
echo "    下载得到 sha256: ${GOT_SHA:-（算不出，跳过）}"
if [ -n "$REMOTE_SHA" ] && [ -n "$GOT_SHA" ] && [ "$REMOTE_SHA" != "$GOT_SHA" ]; then
  echo "错误：sha256 与 release 上的 digest 不一致 —— 拒绝安装" >&2; exit 1
fi
if [ -n "$EXTRA_SHA" ] && [ "$EXTRA_SHA" != "$GOT_SHA" ]; then
  echo "错误：sha256 与 --sha256 给的不一致 —— 拒绝安装" >&2; exit 1
fi
unzip -tq "$TMP/$ASSET" >/dev/null || { echo "错误：zip 完整性检查失败" >&2; exit 1; }

echo "==> 解包到临时目录"
unzip -q "$TMP/$ASSET" -d "$TMP/x"
SRC="$TMP/x/${PREFIX}${TAG}"
if [ ! -d "$SRC" ]; then
  # 兼容旧结构（文件平铺在 zip 根）
  SRC="$TMP/x"
fi
[ -f "$SRC/mahjong-server.jar" ] || { echo "错误：包里没有 mahjong-server.jar（结构异常）" >&2; exit 1; }

# ---------------------------------------------------------------- 安装
WAS_RUNNING=0
PIDFILE="${PIDFILE:-$STATE_DIR/mahjong-server.pid}"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then WAS_RUNNING=1; fi

if [ "$WAS_RUNNING" -eq 1 ]; then
  echo "==> 服务在跑：先停（给它落盘的时间）"
  STOP_TIMEOUT="${STOP_TIMEOUT:-20}" ./stop.sh || true
fi

if [ -f mahjong-server.jar ]; then
  mkdir -p "$STATE_DIR/backup"
  BAK="$STATE_DIR/backup/mahjong-server-${LOCAL_TAG:-unknown}.jar"
  cp -p mahjong-server.jar "$BAK"
  echo "==> 旧 jar 已备份到 $BAK（回滚：cp '$BAK' mahjong-server.jar && ./restart.sh）"
fi

echo "==> 安装新文件（保留 logs/ run/ replays/）"
cp -f "$SRC/mahjong-server.jar" ./mahjong-server.jar
for f in "$SRC"/*.sh; do
  [ -f "$f" ] || continue
  cp -f "$f" "./$(basename "$f")"
done
chmod +x ./*.sh 2>/dev/null || true
for f in VERSION README.txt DEPLOY.md; do
  [ -f "$SRC/$f" ] && cp -f "$SRC/$f" "./$f"
done

printf '%s\n' "${GOT_SHA:-unknown}" > "$STATE_DIR/installed.sha256"
printf '%s\n' "$TAG" > "$STATE_DIR/installed.tag"
echo "$TAG" > VERSION 2>/dev/null || true

echo "==> 已安装 $TAG（sha256 ${GOT_SHA:-unknown}）"

# ---------------------------------------------------------------- 重启
if [ "$RESTART" = "no" ]; then
  echo "==> --no-restart：没有重启（手动：./start.sh）"
elif [ "$RESTART" = "yes" ] || [ "$WAS_RUNNING" -eq 1 ]; then
  echo "==> 重启服务"
  ./start.sh
else
  echo "==> 之前没在跑，也没有 --restart：只装不启动（手动：./start.sh）"
fi
