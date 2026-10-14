#!/usr/bin/env bash
# 立直麻将服务端 —— **自动获取并安装更新**（从 GitHub Release 拉服务端包，校验后原地替换）
#
#   ./update.sh                     更新到"最新的含服务端包的非 prerelease 发布"；本来在跑就自动重启
#   ./update.sh --list              只列出最近的发布 + "会不会选它"（干跑，不下载）
#   ./update.sh --check             只查有没有新内容（不下载、不安装、不动进程）
#   ./update.sh --tag v1.18.0       指定 tag（精确匹配）
#   ./update.sh --include-prerelease  允许选中 prerelease 发布
#   ./update.sh --force             即使远端摘要与本地记录相同也重装
#   ./update.sh --no-restart        装完不重启（自己挑时间 ./restart.sh）
#   ./update.sh --sha256 <hex>      额外用你手上的 sha256 兜底校验
#   ./update.sh --json-file <file>  ⚠ 离线/测试用：不联网，直接读本地的 releases JSON
#   REPO=owner/repo ./update.sh
#
# ## 怎么挑"最新的服务端包"（2026-10 改；旧规则会挑到没有服务端包的发布）
#
#   ① 拉 **`GET /repos/{owner}/{repo}/releases?per_page=100`**（发布列表），
#      ⛔ **不用 `releases/latest`** —— 它返回的是"整仓最新的非 prerelease 发布"，
#      而本仓库近几个发布（v1.15.0 四个风格包、v1.16.0 机器人包）**都不含服务端包**，
#      于是旧版会去下载一个根本不存在的 `sMahjong-server-v1.16.0.zip`（HTTP 404）。
#   ② 只在 **非 draft** 发布里挑；**prerelease 缺省跳过**（`v1.17.0-weights` 这类
#      权重归档就是 prerelease），要装得显式给 `--include-prerelease`。
#   ③ 服务端包**按资产名模式匹配**（`sMahjong-server-v<语义版本>.zip`），
#      ⛔ 不再拿 tag 去拼资产名 —— 拼出来的名字一旦不存在就只能 404，没有"往前找"的余地。
#   ④ **按语义版本排序取最大**，⛔ 不用 tag 字符串比较（字典序会说 `v1.9.0 > v1.16.0`）。
#   ⑤ 找不到候选 ⇒ **响亮地非零退出**（⛔ 绝不"取不到就跳过"）。
#
# ## 怎么判断"有没有新版"
#
#   ① **tag 不同** → 有新版；② tag 相同但**资产 sha256 变了** → 也算有新版
#   （本仓库修 bug 时不换版本号、原地重发同名资产）。⚠ 包内 `VERSION` 写的是 `1.18.0`
#   （**不带 v**）而 release tag 是 `v1.18.0`，所以两边都先归一化再比 —— 旧版直接比字符串，
#   在刚解压出来的包上**永不相等**，"已是最新"这条短路从来没生效过。
#   ⚠ 若上游**既没有 digest 也没有 `<资产>.sha256` 边车**，就明说"无校验可用"（只记大小），
#   ⛔ 不假装校过。
#
# 依赖：awk（POSIX，Ubuntu 自带）、curl 或 wget、unzip、sha256sum（或 shasum）。
set -euo pipefail
cd "$(dirname "$0")"

REPO="${REPO:-Tetrachlorosilane/sMahjong}"
TAG=""
CHECK=0
LIST=0
FORCE=0
INCLUDE_PRE=0
RESTART="auto"
EXTRA_SHA=""
JSON_FILE=""
ZIP_FILE=""
STATE_DIR="run"

WORK=""
AWK_FILE=""
cleanup() {
  [ -n "$AWK_FILE" ] && rm -f "$AWK_FILE"
  [ -n "$WORK" ] && rm -rf "$WORK"
  return 0
}
trap cleanup EXIT

die() { echo "错误：$*" >&2; exit 1; }

# 包内 VERSION = "1.18.0"，release tag = "v1.18.0" —— 先归一化再比（见文件头 §"怎么判断有没有新版"）
TAB="$(printf '\t')"   # 显式制表符：TSV 解析全靠它
norm_tag() { case "${1:-}" in '') printf '' ;; v*) printf '%s' "$1" ;; *) printf 'v%s' "$1" ;; esac; }

usage() {
  cat <<'USAGE'
立直麻将服务端 —— 自动获取并安装更新（从 GitHub Release 拉服务端包，校验后原地替换）

  ./update.sh                       最新（含服务端包、非 prerelease）；本来在跑就自动重启
  ./update.sh --list                列出最近的发布与"会不会选它"（干跑，不下载）
  ./update.sh --check               只查有没有新内容（不下载、不安装、不动进程）
  ./update.sh --tag v1.18.0         指定 tag（精确匹配）
  ./update.sh --include-prerelease  允许选中 prerelease 发布
  ./update.sh --force               即使远端摘要与本地记录相同也重装
  ./update.sh --no-restart          装完不重启（自己挑时间 ./restart.sh）
  ./update.sh --restart             装完一定重启
  ./update.sh --sha256 <hex>        额外用你手上的 sha256 兜底校验
  ./update.sh --json-file <file>    ⚠ 离线/测试用：不联网，直接读本地的 releases JSON
  ./update.sh --zip-file <file>     ⚠ 离线/测试用：不下载，直接用本地这个服务端包（仍走全套校验）
  REPO=owner/repo ./update.sh       换仓库（自建 fork）

挑选规则：只认非 draft（默认也非 prerelease）发布 → 按资产名模式
`sMahjong-server-v<语义版本>.zip` 找服务端包 → 按语义版本排序取最大。
找不到就非零退出，不会"取不到就跳过"。退出码：0 成功/已是最新，1 出错，2 参数错。
依赖：awk、curl 或 wget、unzip、sha256sum（或 shasum）。
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) [ -n "${2:-}" ] || die "--tag 需要参数"; TAG="$2"; shift 2 ;;
    --check) CHECK=1; shift ;;
    --list) LIST=1; shift ;;
    --include-prerelease) INCLUDE_PRE=1; shift ;;
    --force) FORCE=1; shift ;;
    --no-restart) RESTART=no; shift ;;
    --restart) RESTART=yes; shift ;;
    --sha256) [ -n "${2:-}" ] || die "--sha256 需要参数"; EXTRA_SHA="$2"; shift 2 ;;
    --json-file) [ -n "${2:-}" ] || die "--json-file 需要参数"; JSON_FILE="$2"; shift 2 ;;
    --zip-file) [ -n "${2:-}" ] || die "--zip-file 需要参数"; ZIP_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; echo "用 ./update.sh --help 看用法" >&2; exit 2 ;;
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
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    echo "错误：需要 curl 或 wget" >&2; return 1
  fi
}
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else echo ""; fi
}
file_size() { wc -c < "$1" | tr -d '[:space:]'; }

for t in awk unzip; do
  command -v "$t" >/dev/null 2>&1 || die "缺少 $t（sudo apt install -y $t）"
done

mkdir -p "$STATE_DIR"
LOCAL_TAG_RAW=""
if [ -f VERSION ]; then LOCAL_TAG_RAW="$(head -n1 VERSION | tr -d '[:space:]')"; fi
LOCAL_TAG="$(norm_tag "$LOCAL_TAG_RAW")"
LOCAL_SHA=""
if [ -f "$STATE_DIR/installed.sha256" ]; then LOCAL_SHA="$(awk '{print $1}' "$STATE_DIR/installed.sha256")"; fi

# ---------------------------------------------------------------- 选择逻辑（awk，见文件头）
# 就地写成临时文件：这份 awk 只做"解析 JSON + 按规则挑资产"，不做任何下载/落盘。
AWK_FILE="$(mktemp)"
cat > "$AWK_FILE" <<'AWKPROG'
# ============================================================================
#  release-pick —— 从 GitHub `GET /repos/{owner}/{repo}/releases` 的**原始 JSON** 里，
#  机械地挑出「最新的、含服务端 zip 的、非 draft（缺省也非 prerelease）」那个资产。
#
#  为什么不能像旧版那样用 sed/grep 抠字段（实测，2026-10-15）：
#    ① release 的 `body` 是任意 markdown，实测 **27/27 个 release 的 body 里都出现
#       `sMahjong-server-`**，`v1.10.0` / `v1.3.0` 的 body 里还有 `"name":`；
#    ② 所以「按行 / 按正则扫全份响应」会先命中 body 里的笔记文字，而不是真正的资产；
#    ③ 旧版另有两条硬伤：正则写成 `"name":"…"`（真实响应是 `"name": "…"`，**带空格**），
#       且 `digest` 与 `name` **不在同一行** ⇒ 摘要比对从一开始就没生效过。
#    所以这里做真正的 JSON 词法 + 结构解析：字符串里的 `{}[],:` 不参与结构判定。
#
#  输入：stdin = releases 数组的 JSON
#  参数：-v mode=pick|list  -v include_pre=0|1  -v want_tag=<tag 或空>
#  输出（TSV）：
#    pick 命中：OK <TAB> tag <TAB> 资产名 <TAB> 字节数 <TAB> digest或- <TAB> 边车或- <TAB> published_at <TAB> warn
#    pick 未中：NONE <TAB> 原因
#    list 每行：ROW <TAB> tag <TAB> pre <TAB> draft <TAB> published_at <TAB> 服务端资产或- <TAB> 字节或- <TAB> 校验来源 <TAB> 是否入选 <TAB> 是否选中
# ============================================================================

BEGIN { ri = -1; ai = -1; nt = 0; instr = 0; esc = 0; bare = 0; sbuf = ""; stacklen = 0; pending_key = "" }

function addtok(kind, val) { nt++; tk[nt] = kind; tv[nt] = val }
function flushbare() { if (bare) { addtok("V", sbuf); sbuf = ""; bare = 0 } }

# ---- 词法：把 JSON 切成一串 token（跨行累积；GitHub 的 JSON 里字符串不跨行） ----
function lex(line,   i, c) {
  for (i = 1; i <= length(line); i++) {
    c = substr(line, i, 1)
    if (instr) {
      if (esc) { sbuf = sbuf c; esc = 0 }
      else if (c == "\\") { sbuf = sbuf c; esc = 1 }
      else if (c == "\"") { instr = 0; addtok("S", sbuf); sbuf = "" }
      else sbuf = sbuf c
      continue
    }
    if (c == "\"") { flushbare(); instr = 1; sbuf = ""; continue }
    if (c == "{" || c == "}" || c == "[" || c == "]" || c == ":" || c == ",") {
      flushbare(); addtok("P", c); continue
    }
    if (c == " " || c == "\t" || c == "\r") { flushbare(); continue }
    sbuf = sbuf c; bare = 1
  }
  flushbare()
}

function new_release() { ri++; r_n[ri] = 0; r_tag[ri] = ""; r_pre[ri] = "false"; r_draft[ri] = "false"; r_pub[ri] = ""; ai = -1 }
function new_asset()   { ai++; r_n[ri]++; k = ri SUBSEP ai; a_name[k] = ""; a_size[k] = ""; a_dig[k] = "" }

function set_rel_field(key, val) {
  if (key == "tag_name") r_tag[ri] = val
  else if (key == "prerelease") r_pre[ri] = val
  else if (key == "draft") r_draft[ri] = val
  else if (key == "published_at") r_pub[ri] = val
}
function set_asset_field(key, val,   k) {
  k = ri SUBSEP ai
  if (key == "name") a_name[k] = val
  else if (key == "size") a_size[k] = val
  else if (key == "digest") a_dig[k] = val
}

# ---- 结构：栈式走 token 流。depth 1 = 顶层数组，2 = release 对象，3 = assets 数组，4 = 资产对象 ----
function walk(   i, n, d, v) {
  n = nt; stacklen = 0; pending_key = ""
  for (i = 1; i <= n; i++) {
    if (tk[i] == "P") {
      v = tv[i]
      if (v == "{" || v == "[") {
        d = ++stacklen; skind[d] = v; skey[d] = pending_key; pending_key = ""
        if (v == "{" && d == 2) new_release()
        else if (v == "{" && d == 4 && skind[3] == "[" && skey[3] == "assets") new_asset()
      } else if (v == "}" || v == "]") {
        if (stacklen > 0) stacklen--
        pending_key = ""
      } else if (v == ",") {
        pending_key = ""
      }
      continue
    }
    # 字符串后面紧跟 ':' ⇒ 它是 key，不是值
    if (tk[i] == "S" && i + 1 <= n && tk[i + 1] == "P" && tv[i + 1] == ":") { pending_key = tv[i]; continue }
    d = stacklen
    if (d >= 1 && skind[d] == "{") {
      if (d == 2) set_rel_field(pending_key, tv[i])
      else if (d == 4) set_asset_field(pending_key, tv[i])
    }
    pending_key = ""
  }
}

# ---- 语义版本（⛔ 不用 tag 字符串比较：字典序会说 v1.9.0 > v1.16.0） ----
function is_digits(s,   i, c) {
  if (s == "") return 0
  for (i = 1; i <= length(s); i++) { c = substr(s, i, 1); if (c < "0" || c > "9") return 0 }
  return 1
}
function sv_parse(s,   t, dash, core, n, p, pre) {
  if (s in SV_M) return 1
  t = s
  if (substr(t, 1, 1) == "v") t = substr(t, 2)
  dash = index(t, "-")
  if (dash > 0) { core = substr(t, 1, dash - 1); pre = substr(t, dash + 1) }
  else { core = t; pre = "" }
  n = split(core, p, ".")
  if (n != 3) return 0
  if (!is_digits(p[1]) || !is_digits(p[2]) || !is_digits(p[3])) return 0
  SV_M[s] = p[1] + 0; SV_m[s] = p[2] + 0; SV_p[s] = p[3] + 0; SV_pre[s] = pre
  return 1
}
function sv_cmp(a, b) {
  if (SV_M[a] != SV_M[b]) return (SV_M[a] < SV_M[b]) ? -1 : 1
  if (SV_m[a] != SV_m[b]) return (SV_m[a] < SV_m[b]) ? -1 : 1
  if (SV_p[a] != SV_p[b]) return (SV_p[a] < SV_p[b]) ? -1 : 1
  if (SV_pre[a] == SV_pre[b]) return 0
  if (SV_pre[a] == "") return 1          # 正式版 > 同号预发布
  if (SV_pre[b] == "") return -1
  return (SV_pre[a] < SV_pre[b]) ? -1 : 1
}

# ---- 服务端资产名模式（用**实际**发布名构造，⛔ 不写死版本号） ----
function is_server_asset(n) {
  return (n ~ /^sMahjong-server-v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?\.zip$/) ? 1 : 0
}
function asset_version(n,   t) {
  t = n
  sub(/^sMahjong-server-/, "", t)
  sub(/\.zip$/, "", t)
  return t
}

{ lex($0) }

END {
  walk()

  # ① 每个 release 归拢出服务端资产：优先「内嵌版本 == tag 版本」，否则取版本最大的那个
  for (i = 0; i <= ri; i++) {
    tmatch = ""; tsz = ""; tdig = ""
    gmax = "";   gsz = ""; gdig = ""
    tv_ok = sv_parse(r_tag[i])
    for (j = 0; j < r_n[i]; j++) {
      k = i SUBSEP j
      nm = a_name[k]
      if (!is_server_asset(nm)) continue
      av = asset_version(nm)
      if (!sv_parse(av)) continue
      if (tv_ok && tmatch == "" && sv_cmp(av, r_tag[i]) == 0) { tmatch = nm; tsz = a_size[k]; tdig = a_dig[k] }
      if (gmax == "" || sv_cmp(av, asset_version(gmax)) > 0) { gmax = nm; gsz = a_size[k]; gdig = a_dig[k] }
    }
    if (tmatch != "") { pick_name[i] = tmatch; pick_sz[i] = tsz; pick_dig[i] = tdig; pick_warn[i] = "" }
    else if (gmax != "") {
      pick_name[i] = gmax; pick_sz[i] = gsz; pick_dig[i] = gdig
      pick_warn[i] = "资产内嵌版本(" asset_version(gmax) ") != tag(" r_tag[i] ")"
    } else { pick_name[i] = ""; pick_sz[i] = ""; pick_dig[i] = ""; pick_warn[i] = "" }

    # 校验资产：优先 API 的 digest，其次同名 <资产>.sha256 边车文件
    pick_side[i] = "-"
    if (pick_name[i] != "") {
      for (j = 0; j < r_n[i]; j++) {
        k = i SUBSEP j
        if (a_name[k] == pick_name[i] ".sha256") { pick_side[i] = a_name[k]; break }
      }
    }
  }

  # ② 过滤 + 语义版本排序取最大
  chosen = -1
  for (i = 0; i <= ri; i++) {
    cand[i] = 0
    if (pick_name[i] == "") continue
    if (r_draft[i] == "true") continue                      # draft 永不装
    if (want_tag == "") {
      if (include_pre != "1" && r_pre[i] == "true") continue # prerelease 缺省跳过
    } else {
      if (r_tag[i] != want_tag) continue
    }
    if (!sv_parse(r_tag[i])) continue                        # 排不出序的 tag 不当候选
    cand[i] = 1
    if (chosen < 0 || sv_cmp(r_tag[i], r_tag[chosen]) > 0) chosen = i
  }

  if (mode == "list") {
    for (i = 0; i <= ri; i++) {
      ck = "none"
      if (pick_dig[i] != "" && pick_dig[i] != "null") ck = "digest"
      else if (pick_side[i] != "-") ck = "sidecar"
      printf "ROW\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%d\t%d\n",
        r_tag[i], r_pre[i], r_draft[i], r_pub[i],
        (pick_name[i] == "" ? "-" : pick_name[i]),
        (pick_sz[i] == "" ? "-" : pick_sz[i]),
        ck,
        cand[i], (i == chosen ? 1 : 0)
    }
    exit 0
  }

  if (chosen < 0) {
    if (want_tag != "") printf "NONE\ttag %s 没有可安装的服务端 zip（或该 tag 是 draft / 不存在）\n", want_tag
    else printf "NONE\t没有任何 release 含非 draft%s 的服务端 zip\n", (include_pre == "1" ? "" : "、非 prerelease")
    exit 0
  }
  printf "OK\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
    r_tag[chosen], pick_name[chosen], pick_sz[chosen],
    (pick_dig[chosen] == "" || pick_dig[chosen] == "null" ? "-" : pick_dig[chosen]),
    pick_side[chosen], r_pub[chosen], pick_warn[chosen]
}
AWKPROG

if [ -n "$JSON_FILE" ]; then
  [ -f "$JSON_FILE" ] || die "--json-file 指向的文件不存在：$JSON_FILE"
  echo "==> 读本地 releases JSON：$JSON_FILE（离线，不联网）"
  JSON="$(cat "$JSON_FILE")"
else
  URL="https://api.github.com/repos/$REPO/releases?per_page=100"
  echo "==> 查询 $URL"
  JSON="$(fetch "$URL")" || die "查不到 release 列表（网络？仓库私有？被限流？）"
fi
[ -n "$JSON" ] || die "release 列表是空的"

pick_json() { printf '%s' "$JSON" | awk -v mode="$1" -v include_pre="$INCLUDE_PRE" -v want_tag="$TAG" -f "$AWK_FILE"; }

PICK_LINE="$(pick_json pick)"
PICK_STATUS="${PICK_LINE%%"$TAB"*}"

# --list：把最近的发布全列出来（含"会不会选它"），再打印本次选择
if [ "$LIST" -eq 1 ]; then
  echo "==> 最近的发布（API 返回顺序 = 最新在前）"
  printf '    %-22s %-6s %-6s %-21s %-34s %-10s %-8s %s\n' \
    TAG PRE DRAFT PUBLISHED 服务端资产 字节 校验 结果
  pick_json list | while IFS="$TAB" read -r _ r_tag r_pre r_draft r_pub r_asset r_size r_ck r_inc r_sel; do
    r_verdict=""
    [ "$r_inc" = "1" ] && r_verdict="入选"
    [ "$r_sel" = "1" ] && r_verdict="$r_verdict ★本次选中"
    printf '    %-22s %-6s %-6s %-21s %-34s %-10s %-8s %s\n' \
      "$r_tag" "$r_pre" "$r_draft" "$r_pub" "$r_asset" "$r_size" "$r_ck" "$r_verdict"
  done
  echo
fi

if [ "$PICK_STATUS" != "OK" ]; then
  die "挑不出可安装的服务端包：${PICK_LINE#NONE"$TAB"}（用 --list 看全部发布）"
fi

IFS="$TAB" read -r _ TAG ASSET PICK_SIZE PICK_DIGEST PICK_SIDECAR PUB PICK_WARN <<< "$PICK_LINE"
EXPECT_SHA=""
case "$PICK_DIGEST" in
  ''|'-'|'null') EXPECT_SHA="" ;;
  sha256:*) EXPECT_SHA="${PICK_DIGEST#sha256:}" ;;
  *) EXPECT_SHA="$PICK_DIGEST" ;;
esac

echo "    发布 tag      : $TAG（发布于 ${PUB:-未知}）"
echo "    服务端资产    : $ASSET（${PICK_SIZE:-未知} 字节）"
echo "    远端 sha256   : ${EXPECT_SHA:-（该发布未提供 digest）}"
if [ -n "$PICK_WARN" ]; then echo "    ⚠ $PICK_WARN"; fi
echo "    本地 VERSION  : ${LOCAL_TAG_RAW:-（未知）}（归一化：${LOCAL_TAG:-无}）"
echo "    本地 sha256   : ${LOCAL_SHA:-（没有记录）}"
if [ -n "$EXPECT_SHA" ]; then
  echo "    完整性校验    : release 资产的 digest（sha256）"
elif [ "$PICK_SIDECAR" != "-" ]; then
  echo "    完整性校验    : 边车资产 $PICK_SIDECAR"
else
  echo "    完整性校验    : **无校验可用**（该发布既无 digest 也无 <资产>.sha256 边车；只记大小）"
fi

if [ "$LIST" -eq 1 ]; then
  echo "==> --list：本次会安装 $TAG / $ASSET（未下载、未安装）"
  exit 0
fi

# ---------------------------------------------------------------- 有没有新版
NEW=0
REASON=""
if [ -z "$LOCAL_TAG" ]; then
  NEW=1; REASON="本地没有 VERSION 记录"
elif [ "$TAG" != "$LOCAL_TAG" ]; then
  NEW=1; REASON="tag 不同（本地 $LOCAL_TAG）"
elif [ -n "$EXPECT_SHA" ] && [ "$EXPECT_SHA" != "${LOCAL_SHA:-}" ]; then
  NEW=1; REASON="同一个 tag（$TAG）下的资产摘要变了（原地重发）"
elif [ -z "$EXPECT_SHA" ]; then
  NEW=0; REASON="tag 相同，但该发布**没有摘要可对** ⇒ 无法判断是否原地重发"
else
  NEW=0; REASON="tag 与资产 sha256 都一致"
fi
if [ "$FORCE" -eq 1 ]; then NEW=1; REASON="--force"; fi

if [ "$NEW" -eq 0 ] && [ "$FORCE" -eq 0 ]; then
  echo "==> 已是最新：$TAG（$REASON）"
  [ -z "$EXPECT_SHA" ] && echo "    想无论如何重装一次：./update.sh --force"
  exit 0
fi

if [ "$CHECK" -eq 1 ]; then
  echo "==> 有新内容：$TAG —— $REASON（--check：没有下载、没有安装）"
  exit 0
fi

# ---------------------------------------------------------------- 下载 + 校验
WORK="$(mktemp -d)"
ASSET_URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
if [ -n "$ZIP_FILE" ]; then
  [ -f "$ZIP_FILE" ] || die "--zip-file 指向的文件不存在：$ZIP_FILE"
  echo "==> 用本地包（离线/测试）：$ZIP_FILE"
  cp -f "$ZIP_FILE" "$WORK/$ASSET" || die "复制本地包失败：$ZIP_FILE"
else
  echo "==> 下载 $ASSET_URL"
  download "$ASSET_URL" "$WORK/$ASSET" || die "下载失败：$ASSET_URL（该资产不存在？网络？）"
fi

GOT_SIZE="$(file_size "$WORK/$ASSET")"
GOT_SHA="$(sha256_of "$WORK/$ASSET")"
echo "    下载得到大小  : $GOT_SIZE 字节"
echo "    下载得到 sha256: ${GOT_SHA:-（算不出，缺少 sha256sum/shasum）}"

if [ -n "${PICK_SIZE:-}" ] && [ "$PICK_SIZE" != "$GOT_SIZE" ]; then
  die "资产大小与 API 报告的不一致（期望 $PICK_SIZE，实得 $GOT_SIZE）—— 拒绝安装"
fi

# digest 缺失时退回 <资产>.sha256 边车
if [ -z "$EXPECT_SHA" ] && [ "$PICK_SIDECAR" != "-" ]; then
  echo "==> 没有 digest，改用边车校验文件 $PICK_SIDECAR"
  download "https://github.com/$REPO/releases/download/$TAG/$PICK_SIDECAR" "$WORK/$PICK_SIDECAR" \
    || die "校验文件下载失败：$PICK_SIDECAR"
  EXPECT_SHA="$(grep -oE '[0-9a-fA-F]{64}' "$WORK/$PICK_SIDECAR" | head -n1 | tr 'A-F' 'a-f')"
  [ -n "$EXPECT_SHA" ] || die "校验文件 $PICK_SIDECAR 里找不到 64 位十六进制 sha256"
fi

if [ -n "$EXPECT_SHA" ]; then
  [ "$EXPECT_SHA" = "$GOT_SHA" ] \
    || die "sha256 与发布上的不一致（期望 $EXPECT_SHA，实得 $GOT_SHA）—— 拒绝安装"
  echo "    sha256 校验通过"
else
  echo "    ⚠ 该发布未提供 digest，也没有 <资产>.sha256 ⇒ **无校验可用**（只核了大小 $GOT_SIZE 字节）"
fi
if [ -n "$EXTRA_SHA" ]; then
  [ "$EXTRA_SHA" = "$GOT_SHA" ] || die "sha256 与 --sha256 给的不一致（期望 $EXTRA_SHA，实得 $GOT_SHA）—— 拒绝安装"
  echo "    --sha256 兜底校验通过"
fi
unzip -tq "$WORK/$ASSET" >/dev/null || die "zip 完整性检查失败（unzip -t）"

echo "==> 解包到临时目录"
unzip -q "$WORK/$ASSET" -d "$WORK/x" || die "解包失败"
SRC="$WORK/x/${ASSET%.zip}"
if [ ! -d "$SRC" ]; then
  # 兼容旧结构（文件平铺在 zip 根）
  SRC="$WORK/x"
fi
[ -f "$SRC/mahjong-server.jar" ] || die "包里没有 mahjong-server.jar（结构异常）"

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
  BAK="$STATE_DIR/backup/mahjong-server-${LOCAL_TAG_RAW:-unknown}.jar"
  cp -p mahjong-server.jar "$BAK"
  echo "==> 旧 jar 已备份到 $BAK（回滚：cp '$BAK' mahjong-server.jar && ./restart.sh）"
fi

# ⚠ 必须"先写临时名再 rename"（⛔ 不能直接 cp 覆盖）：本脚本自己就在 $SRC/*.sh 里，
#   而 `cp` 是**就地截断同一个 inode** —— 正在被 bash 逐块读取的脚本会被换成新内容，
#   bash 从旧偏移继续读就会读到错位的命令：**静默停在安装中途、退出码还是 0**
#   （实测：jar 换好了，却没写 VERSION / installed.sha256、也没重启 —— 半装被报成成功）。
#   `mv` 是 rename：换一个新 inode，bash 手上的旧 fd 仍读旧内容，一定跑完。
install_file() {   # install_file <源文件> <目标相对路径>
  cp -f "$1" "./.$2.new" && mv -f "./.$2.new" "./$2"
}
echo "==> 安装新文件（保留 logs/ run/ replays/ players/ —— 玩家档案不能被升级冲掉）"
install_file "$SRC/mahjong-server.jar" mahjong-server.jar
for f in "$SRC"/*.sh; do
  [ -f "$f" ] || continue
  install_file "$f" "$(basename "$f")"
done
chmod +x ./*.sh 2>/dev/null || true
for f in VERSION README.txt DEPLOY.md; do
  if [ -f "$SRC/$f" ]; then install_file "$SRC/$f" "$f"; fi
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
