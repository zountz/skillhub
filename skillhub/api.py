"""暴露给前端 (JS) 的桥接层.

pywebview 会把本类的 public 方法挂到 window.pywebview.api 上。
所有方法统一返回 {ok, data, error}, 前端只需处理这一种形状。
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import traceback
from typing import Any, Callable

from . import __version__, mcp, settings, skills
from .paths import AGENTS, BACKUP_DIR, MCP_MANIFEST, NO_WINDOW, SKILLS_SOURCE, Agent, ensure_dirs, short
from .safeio import log_action, recent_actions


def _wrap(fn: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    def inner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            data = fn(*args, **kwargs)
            return {"ok": True, "data": data, "error": ""}
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}

    inner.__name__ = fn.__name__
    return inner


class Bridge:
    """前端可调用的全部能力."""

    # ---------------- 基础信息 ----------------
    @_wrap
    def bootstrap(self) -> dict[str, Any]:
        ensure_dirs()
        man = mcp.load_manifest()
        if not man["servers"]:
            mcp.import_from_agents()
        return {
            "version": __version__,
            "home": short(SKILLS_SOURCE),
            "manifest": short(MCP_MANIFEST),
            "python": sys.version.split()[0],
            "agents": [
                {
                    "key": a.key,
                    "name": a.name,
                    "available": a.available,
                    "skills_dir": short(a.skills_dir),
                    "mcp_path": short(a.mcp_path),
                    "mcp_format": a.mcp_format,
                }
                for a in AGENTS
            ],
        }

    @_wrap
    def refresh(self) -> dict[str, Any]:
        """一次性拿全量状态, 界面刷新就调这个."""
        sk = skills.scan()
        mc = mcp.scan_mcp()
        auto = settings.snapshot()
        for a in sk["agents"]:
            a["auto_sync"] = auto.get(a["key"], True)
        return {
            "skills": sk,
            "mcp": mc,
            "logs": recent_actions(40),
            "auto_sync": auto,
            "stats": {
                "skills": sk["total"],
                "skills_linked": sum(r["linked_count"] for r in sk["rows"]),
                "skills_drift": sum(1 for r in sk["rows"] if any(r["drift"].values())),
                "unmanaged": len(sk["unmanaged"]),
                "mcp": mc["total"],
                "mcp_drift": sum(
                    1 for r in mc["rows"] if any(v in ("drift", "missing") for v in r["states"].values())
                ),
                "orphans": len(mc["orphans"]),
            },
        }

    # ---------------- Skill ----------------
    @_wrap
    def link_skill(self, agent_key: str, name: str) -> dict[str, Any]:
        r = skills.link_skill(skills_agent(agent_key), name)
        return {"ok": r.ok, "message": r.message}

    @_wrap
    def unlink_skill(self, agent_key: str, name: str) -> dict[str, Any]:
        r = skills.unlink_skill(skills_agent(agent_key), name)
        return {"ok": r.ok, "message": r.message}

    @_wrap
    def skill_detail(self, name: str) -> dict[str, Any]:
        """完整介绍: frontmatter + 正文 + 文件清单 + 各工具状态."""
        return skills.detail(name)

    @_wrap
    def adopt_skill(self, agent_key: str, name: str) -> dict[str, Any]:
        r = skills.adopt_skill(skills_agent(agent_key), name)
        return {"ok": r.ok, "message": r.message}

    @_wrap
    def sync_skills(self, targets: list[str] | None = None) -> dict[str, Any]:
        results = skills.sync_all(targets=targets)
        return {
            "changed": len(results),
            "failed": [r for r in results if not r["ok"]],
            "results": results[:80],
        }

    @_wrap
    def auto_sync(self) -> dict[str, Any]:
        """启动时自动补齐新 skill 的链接.

        只处理"目标位置为空"的情况, 已有副本一律不动 —— 界面上启动即跑,
        所以绝对不能有副作用。
        """
        rows = skills.sync_new()
        return {"changed": len(rows), "results": rows[:20]}

    @_wrap
    def set_auto_sync(self, agent_key: str, enabled: bool) -> dict[str, Any]:
        """设置某工具是否参与自动同步.

        关掉之后它不会再收到自动推送的 skill, 手里已有的也不动;
        想在它那里单独装某个 skill, 仍然可以点那一格的徽标手动建链接。
        """
        settings.set_auto(agent_key, enabled)
        ag = skills_agent(agent_key)
        log_action(
            "set_auto",
            f"{ag.name} {'参与' if enabled else '不参与'}自动同步",
            ok=True,
            agent=agent_key,
        )
        return {
            "ok": True,
            "message": f"{ag.name} 现在{'参与' if enabled else '不参与'}自动同步",
        }

    @_wrap
    def delete_skill(self, name: str, also_agents: list[str] | None = None) -> dict[str, Any]:
        """从真源删除 skill.

        真源目录先整块搬进 data/backups/source（可逆），再拆各工具里的链接；
        副本默认保留，只有点名列进 also_agents 的才一起清。
        """
        return skills.delete_skill(name, also_agents)

    @_wrap
    def delete_unmanaged_skill(self, name: str) -> dict[str, Any]:
        """清掉未纳管的 skill: 副本移进备份区, 外部链接直接拆."""
        return skills.delete_unmanaged(name)

    @_wrap
    def toggle_skill(self, agent_key: str, name: str) -> dict[str, Any]:
        """点一下徽标: 已链接则断开, 否则建立链接."""
        ag = skills_agent(agent_key)
        state = skills.probe(ag.skills_dir / name, SKILLS_SOURCE / name)
        if state == skills.LINKED:
            r = skills.unlink_skill(ag, name)
        else:
            r = skills.link_skill(ag, name)
        return {"ok": r.ok, "message": r.message, "state": state}

    # ---------------- MCP ----------------
    @_wrap
    def mcp_sync(self, targets: list[str] | None = None) -> dict[str, Any]:
        return {"results": mcp.sync_mcp(targets)}

    @_wrap
    def mcp_detail(self, name: str) -> dict[str, Any]:
        """完整说明: 定义 + 各工具现状 + 备注."""
        return mcp.server_detail(name)

    @_wrap
    def mcp_set_note(self, name: str, note: str) -> dict[str, Any]:
        """备注只存在 manifest 里, 不写进任何工具配置."""
        mcp.set_note(name, note)
        return {"ok": True, "message": "备注已保存" if note.strip() else "备注已清空"}

    @_wrap
    def mcp_import(self) -> dict[str, Any]:
        return mcp.import_from_agents()

    @_wrap
    def mcp_import_orphan(self, name: str, agents: list[str] | None = None) -> dict[str, Any]:
        ok = mcp.import_orphan(name, agents)
        return {"ok": ok, "message": "已收进真源" if ok else "没找到该条目"}

    @_wrap
    def mcp_set_targets(self, name: str, targets: list[str]) -> dict[str, Any]:
        man = mcp.load_manifest()
        spec = man["servers"].get(name)
        if spec is None:
            return {"ok": False, "message": "manifest 中没有该条目"}
        spec["targets"] = sorted(set(targets))
        # 从 targets 里移出的工具, 顺带清掉 disable_in 里的残留
        off = [a for a in (spec.get("disable_in") or []) if a in targets]
        if off:
            spec["disable_in"] = off
        else:
            spec.pop("disable_in", None)
        mcp.save_manifest(man)
        return {"ok": True, "message": "已更新目标工具, 点同步后生效"}

    @_wrap
    def mcp_toggle_disabled(self, name: str, agent_key: str | None = None) -> dict[str, Any]:
        """全局开关或单工具开关."""
        man = mcp.load_manifest()
        spec = man["servers"].get(name)
        if spec is None:
            return {"ok": False, "message": "manifest 中没有该条目"}
        if agent_key is None:
            spec["disabled"] = not bool(spec.get("disabled"))
        else:
            off = set(spec.get("disable_in") or [])
            off.symmetric_difference_update({agent_key})
            if off:
                spec["disable_in"] = sorted(off)
            else:
                spec.pop("disable_in", None)
        mcp.save_manifest(man)
        return {"ok": True, "message": "已更新开关, 点同步后生效"}

    @_wrap
    def mcp_cycle(self, name: str, agent_key: str) -> dict[str, Any]:
        """点一下某工具格子: 未启用 -> 启用 -> 关闭 -> ...

        这是界面上最常用的操作, 所以把"加入目标 / 恢复 / 关闭"合成一次轮转。
        """
        man = mcp.load_manifest()
        spec = man["servers"].get(name)
        if spec is None:
            return {"ok": False, "message": "manifest 中没有该条目"}
        if bool(spec.get("disabled")):
            return {"ok": False, "message": "该服务已被全局停用, 请先用右侧总开关恢复"}

        targets = set(spec.get("targets") or [a.key for a in AGENTS])
        off = set(spec.get("disable_in") or [])

        if agent_key not in targets:
            targets.add(agent_key)
            off.discard(agent_key)
            act = "已加入该工具"
        elif agent_key in off:
            off.discard(agent_key)
            act = "已在该工具启用"
        else:
            off.add(agent_key)
            act = "已在该工具关闭"

        spec["targets"] = sorted(targets)
        if off:
            spec["disable_in"] = sorted(off)
        else:
            spec.pop("disable_in", None)
        mcp.save_manifest(man)
        return {"ok": True, "message": f"{act}, 点同步后生效"}

    @_wrap
    def mcp_upsert(self, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        mcp.upsert_server(name, copy.deepcopy(spec))
        return {"ok": True, "message": "已保存"}

    @_wrap
    def mcp_remove(self, name: str, agents: list[str] | None = None) -> dict[str, Any]:
        """彻底删除一个 MCP: 真源摘掉 + 各工具配置里抹掉.

        只从 manifest 删除是不够的 —— 那样它在各工具里会退化成"非托管条目"
        被原样保留, 看着删了其实还在跑.
        """
        return mcp.delete_server(name, agents)

    @_wrap
    def mcp_drop_orphan(self, name: str, agents: list[str] | None = None) -> dict[str, Any]:
        """抹掉游离条目（用户在某工具里手配、真源里没有的）."""
        return mcp.drop_orphan(name, agents)

    # ---------------- 杂项 ----------------
    @_wrap
    def open_path(self, which: str) -> dict[str, Any]:
        table = {
            "source": SKILLS_SOURCE,
            "manifest": MCP_MANIFEST.parent,
            "backups": BACKUP_DIR,
        }
        p = table.get(which)
        if p is None:
            return {"ok": False, "message": "未知路径"}
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))  # noqa: S606  Windows 专用
        return {"ok": True, "message": str(p)}

    @_wrap
    def reveal_file(self, agent_key: str) -> dict[str, Any]:
        ag = skills_agent(agent_key)
        if not ag.mcp_path.exists():
            return {"ok": False, "message": "该配置文件还不存在"}
        subprocess.Popen(["explorer", "/select,", str(ag.mcp_path)], creationflags=NO_WINDOW)
        return {"ok": True, "message": "已在资源管理器中定位"}


def skills_agent(key: str) -> Agent:
    from .paths import AGENT_MAP

    if key not in AGENT_MAP:
        raise KeyError(f"未知工具: {key}")
    return AGENT_MAP[key]
