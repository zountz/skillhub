"""路径常量与 AI 工具（agent）定义.

所有路径在启动时探测一次; 工具不存在时 available=False, UI 会置灰.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()

# ---- 项目自身路径 ----------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UI_DIR = PROJECT_ROOT / "ui"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
MCP_MANIFEST = DATA_DIR / "mcp-servers.json"

# ---- Skill 真源 ------------------------------------------------------------
SKILLS_SOURCE = HOME / ".agents" / "skills"


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


def _d(p: str) -> Path:
    return HOME / p


# 顺序即 UI 展示顺序
AGENTS: tuple[Agent, ...] = (
    Agent(
        key="claude",
        name="Claude Code",
        skills_dir=_d(".claude/skills"),
        mcp_path=_d(".claude.json"),
        mcp_format="json",
        mcp_key="mcpServers",
        hint="全局 MCP 存在 ~/.claude.json 顶层 mcpServers",
    ),
    Agent(
        key="codex",
        name="Codex",
        skills_dir=_d(".codex/skills"),
        mcp_path=_d(".codex/config.toml"),
        mcp_format="toml",
        mcp_key="mcp_servers",
        enable_field="enabled",
        hint="TOML 配置, 需保留注释与其他段落",
    ),
    Agent(
        key="qoder",
        name="Qoder CN",
        skills_dir=_d(".qoder-cn/skills"),
        mcp_path=_d(".qoder-cn/settings.json"),
        mcp_format="json",
        mcp_key="mcpServers",
    ),
    Agent(
        key="workbuddy",
        name="WorkBuddy",
        skills_dir=_d(".workbuddy/skills"),
        mcp_path=_d(".workbuddy/mcp.json"),
        mcp_format="json",
        mcp_key="mcpServers",
        enable_field="disabled",
    ),
)

AGENT_MAP: dict[str, Agent] = {a.key: a for a in AGENTS}


def agent(key: str) -> Agent:
    return AGENT_MAP[key]


def ensure_dirs() -> None:
    for p in (DATA_DIR, BACKUP_DIR, LOG_DIR, SKILLS_SOURCE):
        p.mkdir(parents=True, exist_ok=True)


def short(path: Path) -> str:
    """把 home 前缀折叠成 ~ , 便于展示."""
    try:
        rel = path.relative_to(HOME)
        return "~/" + rel.as_posix()
    except ValueError:
        return path.as_posix()


def is_windows() -> bool:
    return os.name == "nt"
