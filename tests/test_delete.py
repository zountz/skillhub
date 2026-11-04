"""删除功能测试.

两块:
  A. skill —— 在真实目录里造链接 + 一份独立副本, 然后删真源, 验证
       · 真源进备份区(可恢复), 不是被抹掉
       · 各工具的链接一并拆掉
       · 副本默认保留(它可能是唯一版本), 之后能从未纳管里清掉
  B. mcp —— 四家配置全指向临时文件, 不碰任何真实配置.
       重点验证"从 manifest 摘掉"之后, 各工具里的条目必须真的消失,
       不能退化成非托管条目残留.

运行: python tests/test_delete.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillhub import mcp, paths, skills  # noqa: E402
from skillhub.paths import Agent  # noqa: E402

PROBE = "_skillhub_del_probe"
CONTENT = "# del probe\n\nskillhub delete check\n"

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    if not ok:
        fails += 1
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))


# --------------------------------------------------------------------------- #
# A. skill
# --------------------------------------------------------------------------- #
def test_skill_delete() -> None:
    print("\n=== A. Skill 删除 ===")
    paths.ensure_dirs()
    src = paths.SKILLS_SOURCE / PROBE
    if os.path.lexists(src):
        shutil.rmtree(src)
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(CONTENT, encoding="utf-8")

    agents = [a for a in paths.AGENTS if a.available]
    if not agents:
        check("有可用工具", False, "四个工具都没装, 跳过")
        return

    # 第一个工具留独立副本(模拟"用户改过的那一份"), 其余建链接
    copy_agent = agents[0]
    for ag in agents:
        entry = ag.skills_dir / PROBE
        if os.path.lexists(entry):
            skills.unlink_skill(ag, PROBE)
        if ag.key == copy_agent.key:
            shutil.copytree(src, entry)
        else:
            skills.link_skill(ag, PROBE)

    linked = [a for a in agents if skills.probe(a.skills_dir / PROBE, src) == skills.LINKED]
    copied = skills.probe(copy_agent.skills_dir / PROBE, src)
    check(
        "准备: 链接 + 独立副本都已就位",
        len(linked) == len(agents) - 1 and copied == skills.COPY,
        f"{len(linked)} 处链接, {copy_agent.name} 是副本",
    )

    r = skills.delete_skill(PROBE)
    backup = Path(r.get("backup_path") or "")

    check("返回成功", bool(r.get("ok")), r.get("message", ""))
    check("真源目录已消失", not src.exists())
    check("真源进了备份区且内容完整", (backup / "SKILL.md").exists(), str(backup))

    gone = [a for a in agents if a.key != copy_agent.key and not os.path.lexists(a.skills_dir / PROBE)]
    check(
        "各工具的链接已全部拆除",
        len(gone) == len(agents) - 1,
        f"{len(gone)}/{len(agents) - 1}",
    )
    check(
        "副本没被连坐删除",
        os.path.lexists(copy_agent.skills_dir / PROBE),
        f"{copy_agent.name} 的副本仍在",
    )
    check("结果里记下了被保留的副本", any(k["agent"] == copy_agent.key for k in r.get("kept", [])))

    # 删完的副本会出现在"未纳管"里, 那里可以一键清掉
    sk = skills.scan()
    check(
        "残留副本出现在未纳管列表",
        any(u["name"] == PROBE for u in sk["unmanaged"]),
    )

    r2 = skills.delete_unmanaged(PROBE)
    check(
        "未纳管清理成功",
        bool(r2.get("ok")) and not os.path.lexists(copy_agent.skills_dir / PROBE),
        r2.get("message", ""),
    )

    # 再删一次应该明确失败, 而不是静默成功
    r3 = skills.delete_skill(PROBE)
    check("重复删除被拒绝", not r3.get("ok"), r3.get("message", ""))


# --------------------------------------------------------------------------- #
# B. mcp
# --------------------------------------------------------------------------- #
def test_mcp_delete() -> None:
    print("\n=== B. MCP 删除 ===")
    tmp = Path(tempfile.mkdtemp(prefix="skillhub_del_"))
    try:
        json_a = tmp / "a.json"
        json_b = tmp / "b.json"
        toml_c = tmp / "c.toml"

        # 用户手配的条目: 删除时绝不能被误伤
        json_a.write_text(
            json.dumps({"mcpServers": {"keep-me": {"command": "a"}, "gone": {"command": "x"}}}),
            encoding="utf-8",
        )
        json_b.write_text(
            json.dumps({"mcpServers": {"keep-me": {"command": "b"}, "gone": {"command": "x"}}}),
            encoding="utf-8",
        )
        toml_c.write_text(
            '# 我的注释\nmodel = "x"\n\n[mcp_servers.keep-me]\ncommand = "c"\n\n'
            '[mcp_servers.gone]\ncommand = "x"\n',
            encoding="utf-8",
        )

        def mk(key: str, name: str, path: Path, fmt: str, mkey: str, field: str = "") -> Agent:
            d = tmp / "skills" / key
            d.mkdir(parents=True, exist_ok=True)
            return Agent(key=key, name=name, skills_dir=d, mcp_path=path, mcp_format=fmt, mcp_key=mkey, enable_field=field)

        fake = [
            mk("claude", "Claude Code", json_a, "json", "mcpServers"),
            mk("workbuddy", "WorkBuddy", json_b, "json", "mcpServers", "disabled"),
            mk("codex", "Codex", toml_c, "toml", "mcp_servers", "enabled"),
        ]

        manifest = tmp / "manifest.json"
        archive = tmp / "deleted.json"

        # 临时替换掉全局, 保证一个字节都不碰真实配置
        saved = (mcp.AGENTS, mcp.MCP_MANIFEST, mcp.DELETED_ARCHIVE)
        mcp.AGENTS = tuple(fake)  # type: ignore[misc]
        mcp.MCP_MANIFEST = manifest  # type: ignore[assignment]
        mcp.DELETED_ARCHIVE = archive  # type: ignore[assignment]

        try:
            mcp.upsert_server("gone", {"command": "x", "targets": ["claude", "workbuddy", "codex"]})
            mcp.sync_mcp()

            before = [("gone" in mcp.read_agent_mcp(a)) for a in fake]
            check("准备: 三个工具里都已写入", all(before), str(before))

            res = mcp.delete_server("gone")
            check("返回成功", bool(res.get("ok")), res.get("message", ""))

            man = mcp.load_manifest()
            check("manifest 里已摘掉", "gone" not in man["servers"])

            after = [("gone" in mcp.read_agent_mcp(a)) for a in fake]
            check("三个工具配置里都已抹掉", not any(after), str(after))

            # 非托管条目与注释必须原样健在
            kept = [("keep-me" in mcp.read_agent_mcp(a)) for a in fake]
            check("手配条目 keep-me 未被误伤", all(kept), str(kept))
            check("TOML 注释保留", "# 我的注释" in toml_c.read_text(encoding="utf-8"))
            check("TOML 顶层其他键保留", 'model = "x"' in toml_c.read_text(encoding="utf-8"))

            data = json.loads(archive.read_text(encoding="utf-8"))
            check("定义已存档, 可手写恢复", data.get("gone", {}).get("spec", {}).get("command") == "x")

            # 游离条目: 不进真源, 直接从各工具删
            res2 = mcp.drop_orphan("keep-me")
            check(
                "游离条目清理成功",
                bool(res2.get("ok")) and not any("keep-me" in mcp.read_agent_mcp(a) for a in fake),
                res2.get("message", ""),
            )
            check(
                "真源里没有的条目不允许用删除流程",
                not mcp.delete_server("keep-me").get("ok"),
            )
        finally:
            mcp.AGENTS, mcp.MCP_MANIFEST, mcp.DELETED_ARCHIVE = saved  # type: ignore[assignment]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    test_skill_delete()
    test_mcp_delete()
    print("\n结果:", "全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
