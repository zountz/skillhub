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
        """工具是否安装过 (配置根目录存在)."""
        return self.skills_dir.parent.exists()

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


def _build() -> tuple[Path, tuple[Agent, ...]]:
    ov = read_locations()
    source = resolve(ov.get("skills_source") or DEFAULT_SOURCE)
    over = ov.get("agents") or {}

    out: list[Agent] = []
    for spec in SPECS:
        o = over.get(spec.key) or {}
        root = _pick_root(spec, o)
        skills = resolve(o["skills_dir"]) if o.get("skills_dir") else root / spec.skills_rel
        if o.get("mcp_path"):
            mcp = resolve(o["mcp_path"])
        else:
            default_root = resolve(spec.roots[0]) if spec.roots else HOME
            tpl = spec.mcp_path_moved if (spec.mcp_path_moved and root != default_root) else spec.mcp_path
            mcp = resolve(tpl.format(root=root.as_posix(), home=HOME.as_posix()))
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
    return source, tuple(out)


SKILLS_SOURCE, AGENTS = _build()
AGENT_MAP: dict[str, Agent] = {a.key: a for a in AGENTS}


def agent(key: str) -> Agent:
    return AGENT_MAP[key]


# --------------------------------------------------------------------------- #
# 给界面看的快照 / 保存
# --------------------------------------------------------------------------- #
def locations_snapshot() -> dict[str, Any]:
    """当前生效的位置 + 默认值 + 来源, 供界面展示与编辑."""
    ov = read_locations()
    agents_over = ov.get("agents") or {}

    rows = []
    for spec, ag in zip(SPECS, AGENTS):
        o = agents_over.get(spec.key) or {}
        env_val = os.environ.get(spec.env_var, "") if spec.env_var else ""
        default_root = resolve(spec.roots[0]) if spec.roots else HOME
        rows.append(
            {
                "key": spec.key,
                "name": spec.name,
                "skills_dir": str(ag.skills_dir),
                "mcp_path": str(ag.mcp_path),
                "default_skills": str(default_root / spec.skills_rel),
                "default_mcp": str(
                    resolve(spec.mcp_path.format(root=default_root.as_posix(), home=HOME.as_posix()))
                ),
                "env_var": spec.env_var,
                "env_value": env_val,
                "root": o.get("root", ""),
                "overridden": bool(o.get("skills_dir") or o.get("mcp_path") or o.get("root")),
                "skills_overridden": bool(o.get("skills_dir")),
                "mcp_overridden": bool(o.get("mcp_path")),
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
    }


#: 允许写进 locations.json 的字段, 界面上就这三个
ALLOWED_FIELDS = ("root", "skills_dir", "mcp_path")


def save_locations(payload: dict[str, Any]) -> None:
    """写 data/locations.json.

    只接受 source 与 agents.{key}.{root,skills_dir,mcp_path}; 其他键忽略,
    免得界面传什么就存什么, 把文件写成垃圾。空值表示"用默认"。
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

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    # 原子写: 同目录临时文件 + replace, 中途崩了也不会留半截 json
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
