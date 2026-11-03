"""写回安全性测试: 用真实配置的副本验证「不破坏原有内容」.

运行: python tests/test_roundtrip.py
绝不修改真实配置文件 —— 全部在临时目录里操作.

验收标准(按重要性):
  1. 写回后文件仍能被正常解析
  2. 文件里与 MCP 无关的内容(其他顶层键/段落)一个都不能少
  3. manifest 托管条目要能一字不差地读回来
  4. 非托管的 MCP 条目(用户手配的)要原样保留
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillhub import mcp  # noqa: E402
from skillhub.paths import AGENTS, Agent  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="skillhub-test-"))


def _agent_for_copy(real: Agent, path: Path) -> Agent:
    return Agent(
        key=real.key,
        name=real.name,
        skills_dir=TMP / real.key / "skills",
        mcp_path=path,
        mcp_format=real.mcp_format,
        mcp_key=real.mcp_key,
        enable_field=real.enable_field,
    )


def top_level_keys(path: Path, fmt: str) -> set[str]:
    if fmt == "toml":
        import tomlkit

        return set(tomlkit.parse(path.read_text(encoding="utf-8")).keys())
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.keys())


def main() -> int:
    fails = 0
    servers = mcp.load_manifest()["servers"]
    if not servers:
        print("! manifest 为空, 先跑一次 import_from_agents()")
        return 1

    for real in AGENTS:
        if not real.mcp_path.exists():
            print(f"- {real.name}: 配置不存在, 跳过")
            continue

        dst = TMP / real.key / real.mcp_path.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(real.mcp_path, dst)
        ag = _agent_for_copy(real, dst)

        keys_before = top_level_keys(dst, real.mcp_format)
        before_servers = mcp.read_agent_mcp(ag)
        managed = set(servers)
        orphans_before = {k: v for k, v in before_servers.items() if k not in managed}

        status = mcp.write_agent_mcp(ag, servers)
        after_text = dst.read_text(encoding="utf-8")

        # 1) 语法完好
        parse_err = ""
        try:
            if real.mcp_format == "toml":
                import tomlkit

                tomlkit.parse(after_text)
            else:
                json.loads(after_text)
        except Exception as exc:
            parse_err = str(exc)

        # 2) 非 MCP 内容未丢
        keys_after = top_level_keys(dst, real.mcp_format)
        lost_keys = keys_before - keys_after

        # 3) 托管条目能读回且一致
        after_servers = mcp.read_agent_mcp(ag)
        desired = mcp.desired_for(ag, servers)
        missing = [n for n in desired if n not in after_servers]
        mismatched = [n for n in desired if n in after_servers and not mcp.same_spec(after_servers[n], desired[n])]

        # 4) 非托管条目原样保留
        orphans_after = {k: v for k, v in after_servers.items() if k not in managed}
        orphans_ok = set(orphans_before) == set(orphans_after)

        ok = not parse_err and not lost_keys and not missing and not mismatched and orphans_ok
        fails += 0 if ok else 1
        print(f"[{'OK  ' if ok else 'FAIL'}] {real.name:12} {status:9} 托管 {len(desired)} 个, 非托管保留 {len(orphans_after)} 个")
        if parse_err:
            print(f"       ! 解析失败: {parse_err}")
        if lost_keys:
            print(f"       ! 顶层键丢失: {sorted(lost_keys)}")
        if missing:
            print(f"       ! 未写入: {missing}")
        if mismatched:
            print(f"       ! 内容不一致: {mismatched}")
        if not orphans_ok:
            print(f"       ! 非托管条目被改动: {sorted(set(orphans_before) ^ set(orphans_after))}")

        if real.mcp_format == "toml":
            kept = [s for s in ("[desktop]", "[projects.", "[shell_environment_policy]", "[marketplaces.") if s in after_text]
            print(f"        保留段落: {', '.join(kept) or '无'}")

    print(f"\n临时目录: {TMP}")
    print("结果:", "全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
