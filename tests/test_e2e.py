"""端到端测试: 在真实的四个工具目录里建链接 -> 验证 -> 拆掉.

用一个临时探针 skill (名字带 _probe 前缀), 全程只碰它自己,
不会动到任何现有 skill.

运行: python tests/test_e2e.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillhub import paths, skills  # noqa: E402

PROBE = "_skillhub_probe"
CONTENT = "# probe\n\nskillhub end-to-end check\n"


def main() -> int:
    paths.ensure_dirs()
    src = paths.SKILLS_SOURCE / PROBE
    agents = [a for a in paths.AGENTS if a.available]
    fails = 0

    print(f"探针: {src}")
    print(f"目标工具: {', '.join(a.name for a in agents)}\n")

    # 准备探针
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(CONTENT, encoding="utf-8")
    (src / "references").mkdir()
    (src / "references" / "note.md").write_text("nested file\n", encoding="utf-8")

    try:
        # 1) 建链接
        for ag in agents:
            r = skills.link_skill(ag, PROBE)
            ok = r.ok and skills.probe(ag.skills_dir / PROBE, src) == skills.LINKED
            fails += 0 if ok else 1
            print(f"[{'OK  ' if ok else 'FAIL'}] {ag.name:12} 建链: {r.message}")

        # 2) 通过链接能读到真源内容
        print()
        for ag in agents:
            entry = ag.skills_dir / PROBE
            try:
                body = (entry / "SKILL.md").read_text(encoding="utf-8")
                nested = (entry / "references" / "note.md").read_text(encoding="utf-8")
                ok = body == CONTENT and nested.strip() == "nested file"
            except OSError as exc:
                ok = False
                print(f"       读取出错: {exc}")
            fails += 0 if ok else 1
            print(f"[{'OK  ' if ok else 'FAIL'}] {ag.name:12} 透过链接读取内容")

        # 3) 改真源, 链接侧应立即反映
        print()
        (src / "SKILL.md").write_text(CONTENT + "changed\n", encoding="utf-8")
        for ag in agents:
            body = (ag.skills_dir / PROBE / "SKILL.md").read_text(encoding="utf-8")
            ok = "changed" in body
            fails += 0 if ok else 1
            print(f"[{'OK  ' if ok else 'FAIL'}] {ag.name:12} 修改真源后立刻可见")

        # 4) 重复建链应幂等
        print()
        ag0 = agents[0]
        r = skills.link_skill(ag0, PROBE)
        ok = r.ok and "已经是链接" in r.message
        fails += 0 if ok else 1
        print(f"[{'OK  ' if ok else 'FAIL'}] {ag0.name}: 重复建链幂等 ({r.message})")

        # 5) 拆链接, 真源必须还在
        print()
        for ag in agents:
            r = skills.unlink_skill(ag, PROBE)
            gone = not os.path.lexists(ag.skills_dir / PROBE)
            source_alive = (src / "SKILL.md").exists()
            ok = r.ok and gone and source_alive
            fails += 0 if ok else 1
            print(f"[{'OK  ' if ok else 'FAIL'}] {ag.name:12} 拆链后入口消失={gone} 真源健在={source_alive}")

    finally:
        # 清理: 确保所有入口都拆掉, 再删探针真身
        for ag in agents:
            entry = ag.skills_dir / PROBE
            if os.path.lexists(entry):
                try:
                    skills.unlink_skill(ag, PROBE)
                except Exception:
                    pass
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        print(f"\n清理完成, 探针残留: {src.exists()}")

    print("结果:", "全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
