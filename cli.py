"""SkillHub 命令行入口.

    python cli.py status            看当前状态
    python cli.py sync              只补缺失的链接(安全, 不动存量)
    python cli.py sync --force      连副本/断链一起改造成链接(会备份原内容)
    python cli.py watch             常驻监听真源, 新增 skill 自动补链
    python cli.py mcp-sync          把 MCP manifest 推给各工具
    python cli.py gui               打开桌面界面
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillhub import mcp, paths, skills  # noqa: E402
from skillhub.safeio import recent_actions  # noqa: E402

# Windows 控制台默认 GBK, 中文/符号会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

STATE_LABEL = {
    skills.LINKED: "已链接",
    skills.COPY: "副本",
    skills.MISSING: "未装",
    skills.BROKEN: "断链",
    skills.FOREIGN: "外部",
}


def cmd_status(_: argparse.Namespace) -> int:
    paths.ensure_dirs()
    sk = skills.scan()
    mc = mcp.scan_mcp()

    print(f"\n真源  {sk['sources_dir']}   ({sk['total']} 个 skill)")
    print(f"MCP   {mc['manifest']}   ({mc['total']} 个服务)\n")

    print(f"{'工具':<14}{'Skills':>7}{'已链接':>8}{'副本':>6}{'异常':>6}{'MCP':>6}")
    print("-" * 47)
    for a in sk["agents"]:
        m = next((x for x in mc["agents"] if x["key"] == a["key"]), {})
        flag = "" if a["available"] else "  (未安装)"
        print(
            f"{a['name']:<14}{a['count']:>7}{a['linked']:>8}{a['copies']:>6}{a['broken']:>6}"
            f"{m.get('count', 0):>6}{flag}"
        )

    drift = [r for r in sk["rows"] if any(r["drift"].values())]
    if drift:
        print("\n副本内容与真源不同:")
        for r in drift:
            tools = [STATE_LABEL.get(k) for k, v in r["drift"].items() if v]
            names = [a["name"] for a in sk["agents"] if r["drift"].get(a["key"])]
            print(f"  {r['name']}  <- {', '.join(names)}")

    if sk["unmanaged"]:
        total = len(sk["unmanaged"])
        print(f"\n未纳管 skill {total} 个 (只在某个工具里):")
        for u in sk["unmanaged"][:10]:
            print(f"  {u['name']}  <- {', '.join(u['agents'])}")
        if total > 10:
            print(f"  … 还有 {total - 10} 个 (界面里可一键纳管)")

    if mc["orphans"]:
        print(f"\n游离 MCP {len(mc['orphans'])} 个:")
        for o in mc["orphans"]:
            print(f"  {o['name']}  <- {', '.join(o['agents'])}")

    print()
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    targets = args.agent or None

    if args.force:
        results = skills.sync_all(targets=targets)
        print(f"强制同步: {len(results)} 项变更")
    else:
        results = skills.sync_new(targets=targets)
        if not results:
            print("没有需要补的链接 (存量副本需在界面上确认后才会改)")
            return 0
        print(f"自动补齐: {len(results)} 项")

    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['agent_name']:<13} {r['skill']:<28} {r['message']}")

    failed = [r for r in results if not r["ok"]]
    return 1 if failed else 0


def cmd_watch(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    print("监听真源目录, Ctrl+C 退出。")
    skills.watch(
        interval=args.interval,
        targets=args.agent or None,
        on_event=lambda kind, msg: print(f"  [{kind}] {msg}"),
    )
    return 0


def cmd_mcp_sync(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    man = mcp.load_manifest()
    if not man["servers"]:
        print("真源为空, 先从各工具现有配置导入…")
        mcp.import_from_agents()
    results = mcp.sync_mcp(args.agent or None)
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['agent_name']:<13} {r['message']} ({r['count']} 个)")
    return 0 if all(r["ok"] for r in results) else 1


def cmd_log(args: argparse.Namespace) -> int:
    for rec in recent_actions(args.limit):
        mark = "✓" if rec["ok"] else "✗"
        print(f"  {rec['time']}  {mark}  {rec['action']:<12} {rec['detail']}")
    return 0


def cmd_gui(_: argparse.Namespace) -> int:
    import app

    return app.main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skillhub", description="统一管理 Skill 与 MCP")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="查看当前状态").set_defaults(func=cmd_status)

    s = sub.add_parser("sync", help="补齐缺失的链接 (默认不碰已有副本)")
    s.add_argument("--force", action="store_true", help="连副本/断链一起改造成链接(原内容会备份)")
    s.add_argument("--agent", action="append", help="只处理指定工具, 可重复: --agent claude --agent qoder")
    s.set_defaults(func=cmd_sync)

    w = sub.add_parser("watch", help="常驻监听真源, 新增 skill 自动补链")
    w.add_argument("--interval", type=float, default=3.0, help="轮询间隔秒数, 默认 3")
    w.add_argument("--agent", action="append", help="只处理指定工具")
    w.set_defaults(func=cmd_watch)

    m = sub.add_parser("mcp-sync", help="把 MCP manifest 推给各工具")
    m.add_argument("--agent", action="append", help="只处理指定工具")
    m.set_defaults(func=cmd_mcp_sync)

    lg = sub.add_parser("log", help="查看最近操作")
    lg.add_argument("--limit", type=int, default=20)
    lg.set_defaults(func=cmd_log)

    sub.add_parser("gui", help="打开桌面界面").set_defaults(func=cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
