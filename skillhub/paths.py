"""路径常量与 AI 工具（agent）定义.

定位规则, 从低到高:
  1. 内置默认值           ~/.claude/skills 这类
  2. 环境变量             CLAUDE_CONFIG_DIR / CODEX_HOME 等, 工具自己认的
  3. data/locations.json  用户手动指定(界面上可改)

全部由 Path.home() 推导, 代码里没有任何绝对路径 —— 别人 clone 下来在自己
机器上跑, 用的就是他的 home、他的工具位置。

改完 locations.json 需要重启进程: AGENTS / SKILLS_SOURCE 是启动时构建的
模块级常量, 各模块 `from .paths import AGENTS` 拿到的就是当时那个元组。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOME = Path.home()

#: 建链接要调一次命令解释器。本程序由 pythonw 启动(自身没有控制台), Windows
#: 会给子进程新建一个控制台窗口 —— 那就是操作时一闪而过的黑框。
#: CREATE_NO_WINDOW 让它根本不出现。非 Windows 上必须为 0。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# ---- 项目自身路径 ----------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UI_DIR = PROJECT_ROOT / "ui"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
MCP_MANIFEST = DATA_DIR / "mcp-servers.json"
LOCATIONS_FILE = DATA_DIR / "locations.json"


@dataclass(frozen=True)
class Agent:
    """一个 AI 编程工具 (Claude Code / Codex / Qoder / WorkBuddy ...)."""

    key: str
    name: str
    skills_dir: Path
    mcp_path: Path
    #: json -> 用 "mcpServers" 这样的键; toml -> 用 "mcp_servers" 表
    mcp_format: str
    mcp_key: str
    #: 该家是否支持在 MCP 条目里写 enabled/disabled
    enable_field: str = ""  # "" | "enabled" | "disabled"
    hint: str = ""

    @property
    def available(self) -> bool:
        """工具是否安装过.

        skills 目录或 MCP 配置文件任一存在就算装了 —— 有些工具(比如只给
        Claude Desktop 配 MCP 的场景)根本没有 skills 目录, 不能因此判为未安装。
        """
        return self.skills_dir.parent.exists() or self.mcp_path.exists()

    @property
    def mcp_exists(self) -> bool:
        return self.mcp_path.exists()


@dataclass(frozen=True)
class LocationSpec:
    """一家的默认位置与探测规则."""

    key: str
    name: str
    #: 配置根目录候选, 按顺序探测; env_var 有值时优先
    roots: tuple[str, ...] = ()
    env_var: str = ""
    #: 相对配置根的 skills 子目录
    skills_rel: str = "skills"
    #: MCP 配置路径模板, 支持 {root} 与 {home}
    mcp_path: str = "{root}/mcp.json"
    #: 配置根被移走(环境变量或手填)时改用的路径; 空 = 不随根移动
    mcp_path_moved: str = ""
    mcp_format: str = "json"
    mcp_key: str = "mcpServers"
    enable_field: str = ""
    hint: str = ""


#: 顺序即 UI 展示顺序. Qoder 给两个候选是因为国内版/国际版目录名不同.
SPECS: tuple[LocationSpec, ...] = (
    LocationSpec(
        key="claude",
        name="Claude Code",
        roots=("~/.claude",),
        env_var="CLAUDE_CONFIG_DIR",
        skills_rel="skills",
        # 全局 MCP 在 home 下的 .claude.json, 不在 ~/.claude 目录里;
        # 但配置根被 CLAUDE_CONFIG_DIR 挪走后, 它也跟着进那个目录
        mcp_path="{home}/.claude.json",
        mcp_path_moved="{root}/.claude.json",
        hint="全局 MCP 存在 ~/.claude.json 顶层 mcpServers",
    ),
    LocationSpec(
        key="codex",
        name="Codex",
        roots=("~/.codex",),
        env_var="CODEX_HOME",
        mcp_path="{root}/config.toml",
        mcp_format="toml",
        mcp_key="mcp_servers",
        enable_field="enabled",
        hint="TOML 配置, 需保留注释与其他段落",
    ),
    LocationSpec(
        key="qoder",
        name="Qoder CN",
        roots=("~/.qoder-cn", "~/.qoder"),
        mcp_path="{root}/settings.json",
        hint="国内版默认 ~/.qoder-cn, 国际版 ~/.qoder",
    ),
    LocationSpec(
        key="workbuddy",
        name="WorkBuddy",
        roots=("~/.workbuddy",),
        mcp_path="{root}/mcp.json",
        enable_field="disabled",
    ),
)

DEFAULT_SOURCE = "~/.agents/skills"


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def resolve(text: str | Path) -> Path:
    """展开 ~ 与环境变量, 但**不**要求路径存在."""
    return Path(os.path.expandvars(os.path.expanduser(str(text))))


def read_locations() -> dict[str, Any]:
    """读 data/locations.json; 坏了就当没有, 绝不因此启动失败."""
    try:
        data = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pick_root(spec: LocationSpec, override: dict[str, Any]) -> Path:
    """选配置根: 手填 > 环境变量 > 第一个存在的候选 > 第一个候选."""
    if override.get("root"):
        return resolve(override["root"])
    if spec.env_var:
        env = os.environ.get(spec.env_var)
        if env:
            return resolve(env)
    cands = [resolve(r) for r in spec.roots]
    for c in cands:
        if c.exists():
            return c
    return cands[0] if cands else HOME


def _mcp_for(spec: LocationSpec, root: Path, override_mcp: str = "") -> Path:
    if override_mcp:
        return resolve(override_mcp)
    default_root = resolve(spec.roots[0]) if spec.roots else HOME
    tpl = spec.mcp_path_moved if (spec.mcp_path_moved and root != default_root) else spec.mcp_path
    return resolve(tpl.format(root=root.as_posix(), home=HOME.as_posix()))


def _build() -> tuple[Path, tuple[Agent, ...]]:
    ov = read_locations()
    source = resolve(ov.get("skills_source") or DEFAULT_SOURCE)
    over = ov.get("agents") or {}

    out: list[Agent] = []
    for spec in SPECS:
        o = over.get(spec.key) or {}
        root = _pick_root(spec, o)
        skills = resolve(o["skills_dir"]) if o.get("skills_dir") else root / spec.skills_rel
        mcp = _mcp_for(spec, root, o.get("mcp_path") or "")
        out.append(
            Agent(
                key=spec.key,
                name=spec.name,
                skills_dir=skills,
                mcp_path=mcp,
                mcp_format=spec.mcp_format,
                mcp_key=spec.mcp_key,
                enable_field=spec.enable_field,
                hint=spec.hint,
            )
        )

    # 名单之外、由"扫目录"发现或用户手动纳入的工具。
    # 它们不知道各家格式差异, 只按扫描到的证据来 —— 所以 format / key 都来自
    # 实际读到的配置, 不是猜的。
    for key, c in (ov.get("custom") or {}).items():
        if not isinstance(c, dict):
            continue
        fallback = HOME / ("." + key)
        out.append(
            Agent(
                key=str(key),
                name=str(c.get("name") or key),
                skills_dir=resolve(c["skills_dir"]) if c.get("skills_dir") else fallback / "skills",
                mcp_path=resolve(c["mcp_path"]) if c.get("mcp_path") else fallback / "mcp.json",
                mcp_format=str(c.get("format") or "json"),
                mcp_key=str(c.get("mcp_key") or "mcpServers"),
                enable_field=str(c.get("enable_field") or ""),
                hint="扫描发现后纳入的工具",
            )
        )
    return source, tuple(out)


SKILLS_SOURCE, AGENTS = _build()
AGENT_MAP: dict[str, Agent] = {a.key: a for a in AGENTS}


def agent(key: str) -> Agent:
    return AGENT_MAP[key]


# --------------------------------------------------------------------------- #
# 给界面看的快照 / 保存
# --------------------------------------------------------------------------- #
def detect_now() -> list[dict[str, Any]]:
    """现场重探测: 只看环境变量与默认候选, 忽略手填值.

    界面上"自动检测"按钮用它 —— 装了新工具或改了环境变量后立刻能看到,
    不用为了"检测到没有"重启一次。
    """
    out = []
    for spec in SPECS:
        root = _pick_root(spec, {})
        skills = root / spec.skills_rel
        mcp = _mcp_for(spec, root)
        env_val = os.environ.get(spec.env_var, "") if spec.env_var else ""
        out.append(
            {
                "key": spec.key,
                "name": spec.name,
                "root": str(root),
                "skills_dir": str(skills),
                "mcp_path": str(mcp),
                "detected": root.exists(),
                "skills_exists": skills.exists(),
                "mcp_exists": mcp.exists(),
                "env_var": spec.env_var,
                "env_value": env_val,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 扫目录发现: 不靠名单, 靠特征
# --------------------------------------------------------------------------- #
#: 扫主目录时跳过的: 系统目录与纯缓存, 探它们没意义还慢
SCAN_SKIP = {
    "appdata", "documents", "downloads", "desktop", "music", "pictures", "videos",
    "favorites", "links", "contacts", "searches", "saved games", "onedrive",
    "node_modules", "site-packages", "__pycache__", "venv", ".venv", ".git",
}

#: 常见的 MCP 配置文件名. 名字对不上也能命中 —— 见 _mcp_host 里的通配
MCP_FILENAMES = (
    "mcp.json", "mcpservers.json", "mcp_servers.json", "settings.json",
    "claude_desktop_config.json", "config.json", "config.toml", "agents.json",
)
MCP_KEYS = ("mcpServers", "mcp_servers", "servers", "McpServers")


def _skill_host(d: Path) -> str:
    """该目录下有没有 skills/ 且里面确实躺着 SKILL.md."""
    for sub in ("skills", "Skills", "SKILLS", "skill"):
        p = d / sub
        try:
            if not p.is_dir():
                continue
            for child in p.iterdir():
                if child.is_dir() and (child / "SKILL.md").exists():
                    return str(p)
        except OSError:
            continue
    return ""


def _mcp_file(p: Path) -> tuple[str, str]:
    """(format, key) —— 只认"真能读出 MCP 段"的文件, 不认文件名."""
    try:
        if not p.is_file() or p.stat().st_size > 8_000_000:
            return "", ""
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""

    if p.suffix.lower() == ".toml":
        return ("toml", "mcp_servers") if "[mcp_servers" in text else ("", "")

    try:
        data = json.loads(text)
    except ValueError:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    for key in MCP_KEYS:
        val = data.get(key)
        if isinstance(val, dict) and val:
            return "json", key
    return "", ""


def _mcp_host(d: Path) -> tuple[str, str, str]:
    """(path, format, key); 没有则三个空串."""
    for name in MCP_FILENAMES:
        fmt, key = _mcp_file(d / name)
        if fmt:
            return str(d / name), fmt, key
    try:
        for f in sorted(d.glob("*mcp*.json")):
            fmt, key = _mcp_file(f)
            if fmt:
                return str(f), fmt, key
    except OSError:
        pass
    return "", "", ""


def _norm_key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lstrip(".").lower()).strip("_")
    return k or "tool"


def discover() -> list[dict[str, Any]]:
    """扫主目录, 找出"像 AI 工具配置"的东西.

    判定只看证据, 不看名字:
      · 有 <目录>/skills/<某个>/SKILL.md  → 是 skill 宿主
      · 有能读出 mcpServers / [mcp_servers] 段的 json/toml → 是 MCP 宿主

    所以在册的(内置四家、已纳入的、真源自己)全部排除后, 剩下什么就报什么 ——
    名单里没有的工具也能被发现。
    """
    taken: set[str] = {str(SKILLS_SOURCE).lower(), str(SKILLS_SOURCE.parent).lower()}
    for a in AGENTS:
        taken |= {str(a.skills_dir).lower(), str(a.mcp_path).lower(), str(a.skills_dir.parent).lower()}

    found: dict[str, dict[str, Any]] = {}

    def add(key: str, item: dict[str, Any]) -> None:
        base, i = key, 2
        while key in found or key in AGENT_MAP:
            key = f"{base}{i}"
            i += 1
        item["key"] = key
        found[key] = item

    # 1) 主目录下的一级目录(含点目录)
    try:
        children = sorted((c for c in HOME.iterdir() if c.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        children = []
    for d in children:
        if d.name.lower() in SCAN_SKIP:
            continue
        skills = _skill_host(d)
        mcp, fmt, key = _mcp_host(d)
        if not skills and not mcp:
            continue
        if skills and skills.lower() in taken:
            continue
        if mcp and mcp.lower() in taken:
            continue
        add(
            _norm_key(d.name),
            {
                "name": d.name.lstrip(".") or d.name,
                "root": str(d),
                "skills_dir": skills,
                "mcp_path": mcp,
                "format": fmt,
                "mcp_key": key,
                "evidence": "+".join(x for x in ("skills" if skills else "", "mcp" if mcp else "") if x),
            },
        )

    # 2) 主目录下散落的 *.json —— 有些工具把 MCP 放单个文件
    try:
        for f in sorted(HOME.iterdir(), key=lambda p: p.name.lower()):
            if not f.is_file() or f.suffix.lower() != ".json" or str(f).lower() in taken:
                continue
            fmt, key = _mcp_file(f)
            if not fmt:
                continue
            add(
                _norm_key(f.stem),
                {
                    "name": f.stem.lstrip(".") or f.stem,
                    "root": str(f.parent),
                    "skills_dir": "",
                    "mcp_path": str(f),
                    "format": fmt,
                    "mcp_key": key,
                    "evidence": "mcp",
                },
            )
    except OSError:
        pass

    return sorted(found.values(), key=lambda x: str(x["name"]).lower())


def custom_agents() -> list[dict[str, Any]]:
    """已经纳入管理的名单外工具."""
    out = []
    for key, c in (read_locations().get("custom") or {}).items():
        if isinstance(c, dict):
            out.append({"key": key, "name": c.get("name") or key, **{k: v for k, v in c.items() if k != "name"}})
    return out


def locations_snapshot() -> dict[str, Any]:
    """当前生效的位置 + 默认值 + 来源, 供界面展示与编辑."""
    ov = read_locations()
    agents_over = ov.get("agents") or {}
    live = {d["key"]: d for d in detect_now()}

    rows = []
    for spec, ag in zip(SPECS, AGENTS):
        o = agents_over.get(spec.key) or {}
        env_val = os.environ.get(spec.env_var, "") if spec.env_var else ""
        now = live.get(spec.key, {})
        rows.append(
            {
                "key": spec.key,
                "name": spec.name,
                "skills_dir": str(ag.skills_dir),
                "mcp_path": str(ag.mcp_path),
                # 默认值取"探测到的那个", 而不是写死的第一个候选 ——
                # 否则 Qoder 只有 ~/.qoder 时, 占位符显示 ~/.qoder-cn, 与生效值不一致
                "default_skills": now.get("skills_dir") or str(resolve(spec.roots[0]) / spec.skills_rel),
                "default_mcp": now.get("mcp_path") or "",
                "root": o.get("root", ""),
                "overridden": bool(o.get("skills_dir") or o.get("mcp_path") or o.get("root")),
                "skills_overridden": bool(o.get("skills_dir")),
                "mcp_overridden": bool(o.get("mcp_path")),
                "detected": bool(now.get("detected")),
                "env_var": spec.env_var,
                "env_value": env_val,
                "skills_exists": ag.skills_dir.exists(),
                "mcp_exists": ag.mcp_path.exists(),
                "available": ag.available,
                "format": spec.mcp_format,
                "hint": spec.hint,
            }
        )

    return {
        "source": str(SKILLS_SOURCE),
        "default_source": str(resolve(DEFAULT_SOURCE)),
        "source_exists": SKILLS_SOURCE.exists(),
        "source_overridden": bool(ov.get("skills_source")),
        "file": str(LOCATIONS_FILE),
        "home": str(HOME),
        "agents": rows,
        # 名单外的: 已经纳入的 + 这次扫到的新候选
        "custom": custom_agents(),
        "discovered": discover(),
    }


#: 允许写进 locations.json 的字段, 界面上就这三个
ALLOWED_FIELDS = ("root", "skills_dir", "mcp_path")


def _atomic_write(data: dict[str, Any]) -> None:
    """原子写 locations.json: 同目录临时文件 + replace, 中途崩了也不留半截 json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".locations-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, LOCATIONS_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_locations(payload: dict[str, Any]) -> None:
    """写 data/locations.json.

    只接受 source 与 agents.{key}.{root,skills_dir,mcp_path}; 其他键忽略,
    免得界面传什么就存什么, 把文件写成垃圾。空值表示"用默认"。
    custom 段(已纳入的名单外工具)原样保留 —— 它由 add_custom_agent 单独维护。
    """
    data: dict[str, Any] = {}
    src = str(payload.get("source") or "").strip()
    if src:
        data["skills_source"] = src

    agents_in = payload.get("agents") or {}
    agents_out: dict[str, Any] = {}
    for spec in SPECS:
        o = agents_in.get(spec.key) or {}
        item = {f: str(o.get(f) or "").strip() for f in ALLOWED_FIELDS if str(o.get(f) or "").strip()}
        if item:
            agents_out[spec.key] = item
    if agents_out:
        data["agents"] = agents_out

    custom = read_locations().get("custom")
    if custom:
        data["custom"] = custom
    _atomic_write(data)


def add_custom_agent(item: dict[str, Any]) -> str:
    """把发现到的工具纳入管理. 返回它的 key（可能因重名被加了后缀）."""
    data = read_locations()
    custom = dict(data.get("custom") or {})
    key = _norm_key(str(item.get("key") or item.get("name") or "tool"))
    base, i = key, 2
    while key in custom or key in AGENT_MAP:
        key = f"{base}{i}"
        i += 1

    entry = {
        "name": str(item.get("name") or key),
        "skills_dir": str(item.get("skills_dir") or ""),
        "mcp_path": str(item.get("mcp_path") or ""),
        "format": str(item.get("format") or "json"),
        "mcp_key": str(item.get("mcp_key") or "mcpServers"),
    }
    if item.get("enable_field"):
        entry["enable_field"] = str(item["enable_field"])
    custom[key] = entry
    data["custom"] = custom
    _atomic_write(data)
    return key


def remove_custom_agent(key: str) -> bool:
    """撤销纳入: 只从配置里摘掉, 不碰那个工具的任何文件."""
    data = read_locations()
    custom = dict(data.get("custom") or {})
    if key not in custom:
        return False
    custom.pop(key)
    if custom:
        data["custom"] = custom
    else:
        data.pop("custom", None)
    _atomic_write(data)
    return True


def ensure_dirs() -> None:
    for p in (DATA_DIR, BACKUP_DIR, LOG_DIR, SKILLS_SOURCE):
        p.mkdir(parents=True, exist_ok=True)


def short(path: Path) -> str:
    """把 home 折叠成 ~, 项目内路径折叠成相对路径, 便于展示."""
    for base, prefix in ((HOME, "~"), (PROJECT_ROOT, "")):
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            continue
        return f"{prefix}/{rel}" if prefix else rel
    return path.as_posix()


def is_windows() -> bool:
    return os.name == "nt"
