# SkillHub

一个本地的 Skill / MCP 中枢。**装一次，四个工具同时可用**；改一处，四处一起变。

管理对象：

| 工具 | Skills | MCP 配置 |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `~/.claude.json`（JSON） |
| Codex | `~/.codex/skills/` | `~/.codex/config.toml`（TOML） |
| Qoder CN | `~/.qoder-cn/skills/` | `~/.qoder-cn/settings.json`（JSON） |
| WorkBuddy | `~/.workbuddy/skills/` | `~/.workbuddy/mcp.json`（JSON） |

## 为什么要它

原来的状态是：Qoder 和 WorkBuddy 里各有一份 skill **副本**，改一处另一处不知道；
MCP 在四个地方各写一遍，格式还分 JSON 和 TOML 两种。装个新 skill 要手动复制四遍。

## 怎么做到的

**Skill 只存一份，靠目录联接（junction）分发。**

```
~/.agents/skills/kill-ai-slop/      <- 唯一的真身
        ^
        |  junction
~/.claude/skills/kill-ai-slop
~/.codex/skills/kill-ai-slop
~/.qoder-cn/skills/kill-ai-slop
~/.workbuddy/skills/kill-ai-slop
```

链接不是复制。删掉任何一个工具目录里的入口，真身毫发无损；改真身里的文件，四个工具立刻都是新内容。

**MCP 只维护 `data/mcp-servers.json`，按各家格式渲染。**

```json
{
  "ssh-vm": {
    "command": "...python.exe",
    "args": ["...ssh_mcp_server.py"],
    "env": { "SSH_HOST": "192.168.1.10" },
    "targets": ["qoder", "workbuddy"],
    "disable_in": ["workbuddy"]
  }
}
```

- `targets` —— 只写到哪些工具。**默认不会扩散**，因为像 Codex 的 `node_repl` 绑死了本机运行时管道，铺到别家必炸。
- `disable_in` —— 同一服务在 A 工具启用、B 工具关闭，这种真实需求单独表达。
- 支持开关字段的（Codex / WorkBuddy）会保留条目并标为关闭；不支持的（Claude / Qoder）直接不写入，避免"显示禁用却在跑"。

## 用法

双击 `启动 SkillHub.bat`。三个页面：

- **总览** —— 四个工具的健康状态、待处理的不一致清单
- **Skills** —— 每个 skill 在四个工具里的状态徽标，点一下就在「已链接 / 未装」之间切换；行尾「删除」从真源删掉；下方是只存在于某个工具、还没进真源的 skill，可一键「收进真源」或「删除」
- **MCP** —— 每个服务的四列状态，点格子切换该工具是否启用；右侧是全局停用与删除

徽标含义：

| 徽标 | 意思 |
|---|---|
| 已链接 | 指向真源，内容天然一致 |
| 副本 | 真实目录拷贝，可能已偏离真源 |
| 副本·已改 | 内容与真源不同（按文件内容算，不看时间戳） |
| 未装 | 该工具下没有 |
| 断链 | 链接目标没了 |
| 已同步 / 有差异 / 未写入 | MCP 侧对应含义 |

## 看它到底是干什么的

装多了就会忘。「这个 agnes-video 到底干嘛的来着」——两下就能答：

- **悬停名字**（Skills 表格、MCP 表格、未纳管的小标签都支持）→ 浮出一句介绍。长描述只给前 240 字，末尾提示「点开看全文」。
- **点名字** → 详情面板：
  - Skill：完整 description、四个工具的安装状态、`frontmatter` 里的 author/license 等字段、目录里的文件清单、SKILL.md 正文
  - MCP：启动命令、环境变量（`TOKEN`/`KEY`/`SECRET` 这类键的值默认遮住，防止截屏外泄）、各工具状态、真源位置
- **MCP 可以自己写备注** —— MCP 没有 SKILL.md 那样的自述，只能靠一句话备注。备注存在 `data/mcp-servers.json` 的 `note` 字段里，**不会写进任何工具配置**，纯本地记录。

未纳管的 skill 一样能看：它在哪个工具里，就去那儿读。

## 命令行

界面之外也能直接用（适合脚本、hook、开机自启）：

```bash
python cli.py status        # 看四家状态与未纳管条目
python cli.py sync          # 只补缺失的链接（安全，不动存量）
python cli.py sync --force  # 连副本/断链一起改造成链接（原内容自动备份）
python cli.py watch         # 常驻监听真源，新装 skill 自动补链
python cli.py mcp-sync      # 把 MCP manifest 推给各工具
python cli.py gui           # 打开桌面界面
```

**自动同步的边界（重要）**：

界面启动时和 `sync` 命令都只处理「目标位置为空」的 skill。已经存在的**副本、断链、外部链接一律跳过**。

原因很简单：自动把用户目录里真实存在的副本换成链接，等于静默搬走文件。哪怕有备份，也不该在你没看见的时候发生。要批量改造存量副本，用 `sync --force`，或者到界面上逐项确认——这两种方式都有明确的「我现在要改」语义。

### 按平台关掉自动同步

总览页每张工具卡右上角有个开关，关掉后该工具**不再接收任何自动推送的 skill**：

- 新 skill 不会自动铺进来
- 手里已有的 skill 一个都不动（既不删也不改）
- 想单独给它装某个 skill，仍然可以点 Skills 页那一格的徽标手动建链接

配置存在 `data/settings.json`：

```json
{ "auto_sync": { "workbuddy": false } }
```

**没写进配置的工具一律视为参与**。这是刻意的默认方向——新增工具时不会因为忘了配置就静默不接收 skill。

命令行侧：`cli.py sync` 和 `cli.py watch` 都遵守这个开关；如果显式传 `--agent qoder`，则以命令行为准，忽略开关。

想让自动同步常驻，用 `python cli.py watch`，再把这个命令挂到任务计划程序的开机启动里即可。

## 删除

三层粒度，都能反悔——删除一律是「先搬进备份区」，从不直接抹掉：

| 操作 | 位置 | 做了什么 |
|---|---|---|
| 断链 | Skills 页点已链接的徽标 | 只删链接，真源与副本都不动 |
| 删真源 | Skills 页行尾「删除」 | 真源整目录移到 `data/backups/source/`，各工具里指向它的链接一并拆掉 |
| 清副本 | 未纳管列表「删除」 | 各工具里那份独立副本移到 `data/backups/<工具>/skills/` |

删真源时**副本默认保留**：它可能是你改过、真源里已经没有的唯一版本，不该被连坐删掉。删完之后这些副本会出现在「未纳管」里，要清再点一次删除即可。

MCP 侧对称：

| 操作 | 位置 | 做了什么 |
|---|---|---|
| 停用 | 右侧「停用」 | 全局关闭，配置留着，随时能开 |
| 删真源 | 右侧「删除」 | 从 manifest 摘掉，**并把各工具配置里的条目抹掉**；定义存档到 `data/backups/deleted-mcp.json` |
| 清游离 | 游离条目「删除」 | 从各工具配置里删掉，不进真源 |

MCP 删除必须同时抹各工具配置——只从 manifest 摘掉的话，条目会在各工具里退化成「非托管」，同步时被当成你手配的原样保留，看着删了其实还在跑。这是 `drop` 参数存在的唯一理由。

所有删除都是**两段点击**：第一次点只把按钮变红显示「确认删除」，4.5 秒内再点一次才执行。列表里误点一下不该毁掉东西。

删除只有 GUI 有，命令行故意不做——批量误删的代价太高，值不得省那两下点击。

## 安全机制

改配置最怕写坏。这里做了四层：

1. **写前备份** —— 每次落盘前把原文件复制到 `data/backups/<工具>/`，保留最近 30 份
2. **原子写** —— 同目录临时文件 + `os.replace`，进程中途崩溃也不会留下半截 JSON
3. **只碰自己管的** —— 各工具里用户手配的 MCP 条目原样保留；manifest 里没列的服务一律不删
4. **幂等** —— 内容语义相同就不写盘，不会因为 `env: {}` 这类空字段反复重写整个配置
5. **删除即搬家** —— 删 skill 是把整个目录 move 进 `data/backups/source/`，失败就整体放弃、什么都不动；绝不会出现「删了一半」

`tests/test_roundtrip.py` 专门验证 1–4 点：在真实配置的**副本**上跑一遍写回，检查其他段落（Codex 的 `[desktop]`、`[projects.*]`、注释）是否完好。`tests/test_delete.py` 验证删除：真源进备份而非消失、链接全拆、副本不被连坐、MCP 从 manifest 和四家配置里同时抹掉且手配条目与 TOML 注释完好。改动 `skillhub/mcp.py` 或 `skills.py` 后请先跑它们：

```bash
python tests/test_roundtrip.py
python tests/test_delete.py
```

## 换机器、别人用

代码里没有一处绝对路径，全部由 `Path.home()` 推导。别人 clone 下来直接跑，用的就是他自己机器上的位置。

定位顺序（后者覆盖前者）：

1. **内置默认** —— `~/.claude/skills`、`~/.codex/config.toml` 这些
2. **环境变量** —— 工具自己认的：`CLAUDE_CONFIG_DIR`、`CODEX_HOME`；Qoder 依次找 `~/.qoder-cn`、`~/.qoder`，哪存在用哪个
3. **`data/locations.json`** —— 界面上「路径设置」页填的值

界面上「路径设置」页列出真源与四家的 skills 目录、MCP 配置文件，标出「存在 / 不存在」，可以逐项改、也可以一键清空回到自动探测。输入框留空 = 用默认值。

改完保存后需要**关掉窗口重新打开**才生效：`AGENTS` 和真源是启动时构建的常量，各模块 `from .paths import ...` 拿到的就是那一刻的值。

### 名单外的工具

内置名单只有四家。名单外的工具不需要改代码——路径页底部会**扫主目录按特征找**，判定只看证据不看名字：

- 有 `<目录>/skills/<某个>/SKILL.md` → 是 skill 宿主
- 有能读出 `mcpServers`（或 `[mcp_servers]`）段的 json/toml → 是 MCP 宿主

扫到之后点「纳入」，它就被写进 `data/locations.json` 的 `custom` 段，和内置四家一样出现在总览、Skills、MCP 表里。撤销纳入只从配置里摘掉，不动那个工具磁盘上的任何文件。

内置四家、已纳入的、真源自己都会被排除，不会重复报。

**为什么是扫描而不是单纯猜路径**：写死 `~/.trae`、`~/.cursor` 这类名单，遇到没见过的工具就是瞎的；按 `SKILL.md` 和 `mcpServers` 这两个特征认，任何遵守同样约定的工具都能被找到。代价是格式差异（`enabled` 还是 `disabled`、键名怎么拼）猜不出来，所以扫描结果只当"候选"，纳入时用实际读到的格式，不擅自假设。

## 加一个新工具

只改 `skillhub/paths.py` 里的 `AGENTS`，加一项（key、名称、skills 目录、MCP 文件、格式、键名）即可，UI 会自动多出一列。

## 目录

```
skillhub/
  paths.py     路径与工具定义（唯一需要按机器改的地方）
  skills.py    扫描 / 状态判定 / junction 建立 / 纳管 / 删除 / 自动同步
  mcp.py       manifest 读写 / 各家格式渲染 / 同步 / 删除
  settings.py  用户偏好（哪些工具参与自动同步）
  safeio.py    备份 / 原子写 / 操作日志
  api.py       暴露给界面的方法
cli.py         命令行入口
ui/            index.html + style.css + app.js（无框架）
data/
  mcp-servers.json      MCP 单一真源
  settings.json         本机偏好
  locations.json       本机路径覆盖(不填=自动探测)
  backups/<工具>/        各工具配置每次落盘前的存档（保留 30 份）
  backups/source/       删掉的真源 skill（整目录，可原样搬回）
  backups/deleted-mcp.json  删掉的 MCP 定义（可照着手写回 manifest）
  logs/actions.jsonl    操作记录
```

## 排错

**界面一片空白** —— 跑 `python app.py --selftest`，会无窗口加载界面并打印 DOM 检查结果。

**报 WebView2 相关错误** —— 需要系统安装 Microsoft Edge WebView2 Runtime。

**某个 skill 没显示** —— 判定要求目录里有 `SKILL.md`（大小写不敏感）。各工具的 skills 目录常混着 `agnes-secrets.json` 之类的杂项，故意不认。

**链接建不起来** —— junction 不需要管理员权限；若仍失败会退到 symlink，两者都不行会明确报错并写进操作记录。
