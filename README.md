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
- **Skills** —— 每个 skill 在四个工具里的状态徽标，点一下就在「已链接 / 未装」之间切换；下方是只存在于某个工具、还没进真源的 skill，可一键「收进真源」
- **MCP** —— 每个服务的四列状态，点格子切换该工具是否启用；右侧开关是全局停用

徽标含义：

| 徽标 | 意思 |
|---|---|
| 已链接 | 指向真源，内容天然一致 |
| 副本 | 真实目录拷贝，可能已偏离真源 |
| 副本·已改 | 内容与真源不同（按文件内容算，不看时间戳） |
| 未装 | 该工具下没有 |
| 断链 | 链接目标没了 |
| 已同步 / 有差异 / 未写入 | MCP 侧对应含义 |

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

想让自动同步常驻，用 `python cli.py watch`，再把这个命令挂到任务计划程序的开机启动里即可。

## 安全机制

改配置最怕写坏。这里做了四层：

1. **写前备份** —— 每次落盘前把原文件复制到 `data/backups/<工具>/`，保留最近 30 份
2. **原子写** —— 同目录临时文件 + `os.replace`，进程中途崩溃也不会留下半截 JSON
3. **只碰自己管的** —— 各工具里用户手配的 MCP 条目原样保留；manifest 里没列的服务一律不删
4. **幂等** —— 内容语义相同就不写盘，不会因为 `env: {}` 这类空字段反复重写整个配置

`tests/test_roundtrip.py` 专门验证这几点：在真实配置的**副本**上跑一遍写回，检查其他段落（Codex 的 `[desktop]`、`[projects.*]`、注释）是否完好。改动 `skillhub/mcp.py` 后请先跑它：

```bash
python tests/test_roundtrip.py
```

## 加一个新工具

只改 `skillhub/paths.py` 里的 `AGENTS`，加一项（key、名称、skills 目录、MCP 文件、格式、键名）即可，UI 会自动多出一列。

## 目录

```
skillhub/
  paths.py     路径与工具定义（唯一需要按机器改的地方）
  skills.py    扫描 / 状态判定 / junction 建立 / 纳管
  mcp.py       manifest 读写 / 各家格式渲染 / 同步
  safeio.py    备份 / 原子写 / 操作日志
  api.py       暴露给界面的方法
ui/            index.html + style.css + app.js（无框架）
data/
  mcp-servers.json   MCP 单一真源
  backups/           所有改动前的存档
  logs/actions.jsonl 操作记录
```

## 排错

**界面一片空白** —— 跑 `python app.py --selftest`，会无窗口加载界面并打印 DOM 检查结果。

**报 WebView2 相关错误** —— 需要系统安装 Microsoft Edge WebView2 Runtime。

**某个 skill 没显示** —— 判定要求目录里有 `SKILL.md`（大小写不敏感）。各工具的 skills 目录常混着 `agnes-secrets.json` 之类的杂项，故意不认。

**链接建不起来** —— junction 不需要管理员权限；若仍失败会退到 symlink，两者都不行会明确报错并写进操作记录。
