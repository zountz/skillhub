"""验证 watch 自动补链: 真源里新建 skill -> 各工具自动出现链接.

用 daemon 线程跑监听, 测试结束随进程退出. 全程只碰 _watch_probe.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillhub import paths, skills  # noqa: E402

PROBE = "_watch_probe"


def main() -> int:
    paths.ensure_dirs()
    src = paths.SKILLS_SOURCE / PROBE
    agents = [a for a in paths.AGENTS if a.available]

    # 先确保干净
    if src.exists():
        shutil.rmtree(src)
    for ag in agents:
        entry = ag.skills_dir / PROBE
        if os.path.lexists(entry):
            try:
                skills.unlink_skill(ag, PROBE)
            except Exception:
                pass

    events: list[tuple[str, str]] = []
    thread = threading.Thread(
        target=skills.watch,
        kwargs={"interval": 0.5, "on_event": lambda k, m: events.append((k, m))},
        daemon=True,
    )
    thread.start()
    time.sleep(1.2)

    fails = 0
    try:
        # 模拟"刚装好一个 skill": 只往真源里放, 不碰任何工具目录
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("---\nname: _watch_probe\ndescription: watch test\n---\n", encoding="utf-8")
        print(f"已向真源写入 {PROBE}, 等待自动补链…")

        deadline = time.time() + 6
        while time.time() < deadline:
            if all(
                skills.probe(ag.skills_dir / PROBE, src) == skills.LINKED for ag in agents
            ):
                break
            time.sleep(0.4)

        for ag in agents:
            st = skills.probe(ag.skills_dir / PROBE, src)
            ok = st == skills.LINKED
            fails += 0 if ok else 1
            print(f"[{'OK  ' if ok else 'FAIL'}] {ag.name:12} 状态={st}")

        got_added = any(k == "added" for k, _ in events)
        fails += 0 if got_added else 1
        print(f"[{'OK  ' if got_added else 'FAIL'}] 收到 added 事件: {[m for k, m in events if k == 'added']}")

    finally:
        for ag in agents:
            if os.path.lexists(ag.skills_dir / PROBE):
                try:
                    skills.unlink_skill(ag, PROBE)
                except Exception:
                    pass
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        print(f"\n清理完成, 探针残留: {src.exists() or any(os.path.lexists(a.skills_dir / PROBE) for a in agents)}")

    print("结果:", "全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
