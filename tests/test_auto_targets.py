"""验证「参与自动同步」开关: 关掉的工具不应收到自动推送的 skill.

不影响任何现有 skill, 用临时探针; 结束后恢复原设置.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillhub import paths, settings, skills  # noqa: E402

PROBE = "_auto_target_probe"


def cleanup(agents, src: Path) -> None:
    for ag in agents:
        entry = ag.skills_dir / PROBE
        if os.path.lexists(entry):
            try:
                skills.unlink_skill(ag, PROBE)
            except Exception:
                pass
    if src.exists():
        shutil.rmtree(src, ignore_errors=True)


def main() -> int:
    paths.ensure_dirs()
    src = paths.SKILLS_SOURCE / PROBE
    agents = [a for a in paths.AGENTS if a.available and a.skills_dir.exists()]
    if len(agents) < 2:
        print("可用工具不足, 跳过")
        return 0

    original = settings.snapshot()
    cleanup(agents, src)
    fails = 0

    try:
        # 关掉除第一个之外的所有工具, 只留 claude 参与
        keeper = agents[0]
        muted = agents[1:]
        for ag in muted:
            settings.set_auto(ag.key, False)
        settings.set_auto(keeper.key, True)

        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("---\nname: probe\n---\nprobe\n", encoding="utf-8")

        results = skills.sync_new()
        touched = {r["agent"] for r in results}
        print(f"sync_new 实际动到: {sorted(touched)}")
        for r in results:
            print("   ", r)
        keeper_entry = keeper.skills_dir / PROBE
        print(f"    keeper={keeper.key} entry_lexists={os.path.lexists(keeper_entry)} "
              f"src_exists={src.exists()} state={skills.probe(keeper_entry, src)}")
        print()

        for ag in agents:
            st = skills.probe(ag.skills_dir / PROBE, src)
            should_link = ag.key == keeper.key
            ok = (st == skills.LINKED) if should_link else (st == skills.MISSING)
            fails += 0 if ok else 1
            role = "参与" if should_link else "不参与"
            print(f"[{'OK  ' if ok else 'FAIL'}] {ag.name:12} ({role}) 状态={st}")

        # 显式 targets 应能覆盖设置(命令行 --agent 用)
        settings.set_auto(muted[0].key, False)
        forced = skills._link_missing(PROBE, targets=[muted[0].key])
        st = skills.probe(muted[0].skills_dir / PROBE, src)
        ok = st == skills.LINKED and any(r["agent"] == muted[0].key for r in forced)
        fails += 0 if ok else 1
        print(f"[{'OK  ' if ok else 'FAIL'}] 显式 --agent 可覆盖设置 ({muted[0].name}: {st})")

    finally:
        cleanup(agents, src)
        for key, val in original.items():
            settings.set_auto(key, val)
        print(f"\n已恢复原设置: {settings.snapshot()}")
        print(f"探针残留: {src.exists() or any(os.path.lexists(a.skills_dir / PROBE) for a in agents)}")

    print("结果:", "全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
