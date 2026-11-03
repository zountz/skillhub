"""安全的文件读写: 写前备份 + 原子替换 + 操作日志.

原则: 绝不半途写出坏文件. 任何一次同步都必须可回滚.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import BACKUP_DIR, LOG_DIR

MAX_BACKUPS_PER_FILE = 30
LOG_FILE = LOG_DIR / "actions.jsonl"


# --------------------------------------------------------------------------- #
# 备份
# --------------------------------------------------------------------------- #
def backup_file(path: Path, tag: str = "") -> Path | None:
    """把文件复制一份到 backups/<tag>/<时间戳>_<文件名>. 返回备份路径."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    target_dir = BACKUP_DIR / (tag or "misc")
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / f"{stamp}_{path.name}"
    shutil.copy2(path, dst)
    _prune(target_dir)
    return dst


def _prune(folder: Path) -> None:
    files = sorted(
        (p for p in folder.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[MAX_BACKUPS_PER_FILE:]:
        try:
            old.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 原子写
# --------------------------------------------------------------------------- #
def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """同目录临时文件 + os.replace, 避免写一半崩溃留下坏配置."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default


# --------------------------------------------------------------------------- #
# 操作日志
# --------------------------------------------------------------------------- #
def log_action(action: str, detail: str, ok: bool = True, agent: str = "") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "time": datetime.now().strftime("%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail,
        "agent": agent,
        "ok": ok,
    }
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_actions(limit: int = 60) -> list[dict]:
    if not LOG_FILE.exists():
        return []
    out: list[dict] = []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines[-400:]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
