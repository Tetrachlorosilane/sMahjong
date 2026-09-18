# dsh-github 补丁：文件请求体 + uploads 资产上传

给本机加载的 `@dsh-external/dsh-github` 插件（host 半侧）补两件事，
让 GitHub 的发布流程能**全部走插件**、不必再退回 `gh` CLI：

| 新增/改动 | 解决的问题 |
| --- | --- |
| `github_request` 新增 `bodyFile`（+ `contentType`） | 请求体从**本地文件原样读**，不经过 JSON 序列化 —— 大体积内容（base64 的 blob 体、任意二进制）不必进模型上下文 |
| 新工具 `github_upload_release_asset` | release 资产上传走 **`uploads.github.com`**，那是与 `api.github.com` **不同的 origin** |
| `Config.uploadsBase` / `Config.maxUploadBytes` | 上传主机可用企业版地址覆盖；文件体大小上限（默认 512 MB） |

## 为什么需要它（上游的两条结构性限制）

1. `resolveGitHubUrl()` 把请求目标 origin 钉死在 `apiBase`（同源检查），
   而 GitHub 的资产上传**必须在 `uploads.github.com`** —— 用 `github_request` 打那个地址
   只会得到「请求路径逃逸了 apiBase」。
2. `github_request` 的 `body` 参数是 `type: 'json'`，实现里只有 `JSON.stringify(body)`，
   **没有从文件读请求体的通路**；37 MB 的 zip 不可能塞进内联 JSON。

所以补丁做的是给这两条各开一个**受控**的口子：新工具仍然复用 `authorize()`
（权限判据按 API 路径形状匹配 `releases` → `write:repo`），上传主机只从
`uploadsBase`（或按 `apiBase` 推导）来，不接受任意 URL。

## 怎么用

```powershell
# 打补丁 → 转译 → 安装到 ~/.dsh/plugins/dsh-github
pwsh -File tools\dsh-github-patch\apply.ps1

# 只校验补丁能否干净应用（不落盘）
pwsh -File tools\dsh-github-patch\apply.ps1 -Check

# 还原上游原文
pwsh -File tools\dsh-github-patch\apply.ps1 -Reverse

# 换检出/安装位置
pwsh -File tools\dsh-github-patch\apply.ps1 -PluginDir <插件检出> -InstallDir <已安装插件目录>
```

打完补丁**插件进程里还是旧代码**，要重载才生效：

```
dev_reload_package dsh-github
```

脚本是**幂等**的：src 里已经有 `github_upload_release_asset` 就跳过打补丁、只重新构建安装。
打补丁前会把原文备份成 `src\index.ts.pre-patch.bak`。

## 为什么不用 `git apply` / GNU patch

两个都**实测不可用**，所以自带了一个 60 行的 `apply-patch.mjs`：

- **`git apply` 在非 git 仓库目录里会静默跳过**：打印 `Skipped patch 'src/index.ts'.`
  但**退出码 0**。插件目录（`~/.dsh/plugins/dsh-github` 与源码 checkout）都不是 git 仓库，
  用它等于什么都没做还报成功 —— 这是最危险的一种失败。
- **Git for Windows 自带的 MSYS `patch.exe`** 在受限沙箱里连自身都起不来：
  `fatal error - couldn't create signal pipe, Win32 error 5`。

`apply-patch.mjs` 逐行严格比对上下文：任何一处对不上就整体报错、**文件一个字节都不写**，
绝不"尽力而为"地模糊匹配。已验证：

| 用例 | 结果 |
| --- | --- |
| 对上游原文正向应用 | 与生成器产物**逐字节相同**（41,226 → 51,958 字节） |
| 对上游原文 `--check` | 通过 |
| 对已打补丁的文件 `--reverse` | 与上游原文**逐字节相同** |
| 正向 → 反向往返 | 回到原文（哈希相同） |
| 对已打补丁的文件正向 `--check` | 报错退出 1 |
| 故意改坏一行上下文 | 报错退出 1，且被改的文件未被写入 |

## 为什么转译用 `tsc --noCheck`

上游 `src/index.ts` 在本机工具链下**本来就有** 11 处类型报错：

```
src/index.ts(298,25): error TS2339: Property 'body' does not exist on type 'Response'.
（另有 headers / status / ok 共 11 处，分布在与本补丁无关的 readResponseText / whoami 等处）
```

这是 `@types/node` 与全局 `Response` 的对不齐，**不是本补丁引入的**。
`--noCheck` 只跳过类型检查、照常 emit，产物与上游同构（ESM，无 helper 变化）。
本补丁没有动过 `tsconfig.json`，正常环境里 `bash scripts/build.sh` 依旧可用。

`lib/types/index.d.ts` 是由模块头注释 + `Config` 生成的：本补丁**同步改了源里的头注释**，
所以在能通过类型检查的环境里重建会自动得到一致的声明文件。

## 基线与失配

- 基线：`@dsh-external/dsh-github` 0.0.1，`src/index.ts` 1104 行 / 41,226 字节
  （`host-filebody-and-uploads.patch` 里的 `index bb419a4..` 即该版本的 blob 前 7 位）。
- 上游若改动了补丁涉及的 16 处锚点，`apply.ps1 -Check` / `apply-patch.mjs` 会**直接报错**
  并指出第几个 hunk 的第几行对不上 —— 按提示重做这几处即可，不会静默错位。
