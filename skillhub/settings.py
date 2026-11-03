"""用户偏好设置.

data/settings.json:
  {
    "auto_sync": { "claude": true, "codex": true, "qoder": false }
  }

auto_sync 缺省的键一律视为 true —— 新加工具时不会因为"没写进配置"
就静默不接收 skill。这是刻意的默认方向。
"""

from __future__ import annotations

from typing import Any

from .paths import AGENTS, DATA_DIR, Agent
from .safeio import atomic_write_json, backup_file, read_json

SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT: dict[str, Any] = {"auto_sync": {}}


def load() -> dict[str, Any]:
    data = read_json(SETTINGS_FILE, None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("auto_sync", {})
    if not isinstance(data["auto_sync"], dict):
        data["auto_sync"] = {}
    return data


def save(data: dict[str, Any]) -> None:
    if SETTINGS_FILE.exists():
        backup_file(SETTINGS_FILE, "settings")
    atomic_write_json(SETTINGS_FILE, data)


def is_auto(agent_key: str) -> bool:
    """该工具是否参与自动同步。配置里没写 = 参与。"""
    return bool(load()["auto_sync"].get(agent_key, True))


def set_auto(agent_key: str, enabled: bool) -> None:
    data = load()
    data["auto_sync"][agent_key] = bool(enabled)
    save(data)


def auto_targets() -> list[str]:
    """参与自动同步的工具 key 列表."""
    return [a.key for a in AGENTS if is_auto(a.key)]


def snapshot() -> dict[str, bool]:
    return {a.key: is_auto(a.key) for a in AGENTS}


def apply_to(ag: Agent, targets: list[str] | None) -> bool:
    """判断某工具是否应在本次自动同步范围内.

    targets 显式传入时以其为准(命令行 --agent), 否则看设置。
    """
    if targets is not None:
        return ag.key in targets
    return is_auto(ag.key)
