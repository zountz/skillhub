"""隐私检查: 入库的文件里不许出现本机的真实路径, 敏感配置也不许被跟踪.

这个仓库是 public 的, 一旦把 data/mcp-servers.json(里面是 SSH 的 env)或
带用户名的绝对路径提交上去, 就再也收不回来了 —— git 历史会一直留着。

所以每次改完都跑一遍:
    python tests/test_no_leak.py

刻意不写死用户名/盘符: 用 Path.home() 现取. 写死就等于把要保护的东西
本身登记进仓库了。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

#: 绝对不该被跟踪的路径(前缀匹配)
FORBIDDEN_PATHS = (
    "data/mcp-servers.json",  # 含各 MCP 服务的 env(SSH 账号密码)
    "data/settings.json",     # 本机偏好
    "data/locations.json",    # 本机路径
    "data/backups/",          # 各家配置的完整副本
    "data/logs/",             # 操作日志里全是本机绝对路径
)

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    if not ok:
        fails += 1
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def main() -> int:
    files = tracked_files()
    check("能读到跟踪文件清单", bool(files), f"{len(files)} 个")

    # 1) 敏感文件没被跟踪
    bad = [f for f in files if any(f == p or f.startswith(p) for p in FORBIDDEN_PATHS)]
    check("敏感配置未被跟踪", not bad, ", ".join(bad))

    # 2) 被跟踪的文件里没有本机 home 路径
    #    Windows 上同一个路径可能写成 C:\Users\x 或 C:/Users/x, 两种都查
    needles = {str(HOME), HOME.as_posix()}
    hits: list[str] = []
    for rel in files:
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n in needles:
            if n and n in text:
                line = next((i + 1 for i, ln in enumerate(text.splitlines()) if n in ln), "?")
                hits.append(f"{rel}:{line}")
                break
    check("跟踪文件里没有本机绝对路径", not hits, ", ".join(hits[:5]))

    # 3) 用户名单独出现(不是整条路径)也算线索, 但只查非示例文件
    user = HOME.name
    if len(user) >= 4 and user.lower() not in ("user", "admin", "public", "default"):
        rx = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(user)}(?![A-Za-z0-9_-])")
        weak = []
        for rel in files:
            if rel.endswith(".example.json"):
                continue
            p = ROOT / rel
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if rx.search(text):
                weak.append(rel)
        check(f"跟踪文件里没有用户名 {user!r}", not weak, ", ".join(weak[:5]))
    else:
        print("[SKIP] 用户名太短或太通用, 跳过单词级检查")

    print("\n结果:", "全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
