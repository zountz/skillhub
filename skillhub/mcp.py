"""MCP 单一真源 -> 各家配置的渲染与回写.

真源: data/mcp-servers.json
  {
    "version": 1,
    "servers": {
      "ssh-vm": {
        "command": "...", "args": [...], "env": {...},
        "disabled": false,
        "targets": ["claude", "codex", "qoder", "workbuddy"]   // 省略 = 全部
      }
    }
  }

各家差异:
  Claude Code  ~/.claude.json        顶层 mcpServers, JSON
  Codex        ~/.codex/config.toml  [mcp_servers.x], 用 enabled 而非 disabled, TOML
  Qoder CN     ~/.qoder-cn/settings.json  mcpServers, JSON
  WorkBuddy    ~/.workbuddy/mcp.json      mcpServers, JSON, 有 disabled 字段
"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit

from .paths import AGENTS, BACKUP_DIR, MCP_MANIFEST, Agent, short
from .safeio import atomic_write_json, atomic_write_text, backup_file, log_action, read_json

#: 只属于 manifest 的元字段, 不写进任何工具配置
META_KEYS = {"targets", "note", "disable_in"}


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def empty_manifest() -> dict[str, Any]:
    return {"version": 1, "updated": "", "servers": {}}


def load_manifest() -> dict[str, Any]:
    data = read_json(MCP_MANIFEST)
    if not isinstance(data, dict):
        return empty_manifest()
    data.setdefault("version", 1)
    data.setdefault("servers", {})
    if not isinstance(data["servers"], dict):
        data["servers"] = {}
    return data


def save_manifest(data: dict[str, Any]) -> None:
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    backup_file(MCP_MANIFEST, "manifest")
    atomic_write_json(MCP_MANIFEST, data)


# --------------------------------------------------------------------------- #
# 通用 <-> 各家格式
# --------------------------------------------------------------------------- #
def _clean(spec: dict[str, Any]) -> dict[str, Any]:
    """去掉元字段, 得到纯 MCP 定义."""
    return {k: v for k, v in spec.items() if k not in META_KEYS}


def spec_to_agent(ag: Agent, spec: dict[str, Any]) -> dict[str, Any]:
    """把 manifest 条目渲染成某个工具认识的形状.

    开关字段只在"关闭"时写出 —— 各家的默认值都是启用,
    显式写 enabled=true / disabled=false 会让文件与工具自动生成的内容不一致,
    导致每次同步都判定为"有变化"而重复重写整份配置.
    """
    out = copy.deepcopy(_clean(spec))
    disabled = bool(out.pop("disabled", False))
    if disabled and ag.enable_field == "enabled":
        out["enabled"] = False
    elif disabled and ag.enable_field == "disabled":
        out["disabled"] = True
    if "env" in out and not out["env"]:
        out.pop("env")
    return out


def agent_to_spec(ag: Agent, conf: dict[str, Any]) -> dict[str, Any]:
    """反过来: 把某工具里的条目归一成 manifest 形状."""
    out = {k: copy.deepcopy(v) for k, v in conf.items()}
    if "enabled" in out:
        out["disabled"] = not bool(out.pop("enabled"))
    if out.get("env") == {}:
        out.pop("env")
    return out


def _normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """比较用的规范化: 忽略顺序/空 env, 统一 args 为 list[str]."""
    s = _clean(copy.deepcopy(spec))
    if not s.get("disabled"):
        s.pop("disabled", None)
    # 显式写的"默认值"与缺省等价
    if s.get("enabled") is True:
        s.pop("enabled", None)
    if s.get("disabled") is False:
        s.pop("disabled", None)
    if not s.get("env"):
        s.pop("env", None)
    if "args" in s and s["args"] is None:
        s.pop("args")
    if "args" in s:
        s["args"] = [str(x) for x in s["args"]]
    if "env" in s:
        s["env"] = {str(k): str(v) for k, v in s["env"].items()}
    return s


def same_spec(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _normalize(a) == _normalize(b)


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def read_agent_mcp(ag: Agent) -> dict[str, dict[str, Any]]:
    """读出该工具当前的 MCP 条目 (归一化格式). 不做任何写入."""
    p = ag.mcp_path
    if not p.exists():
        return {}
    try:
        if ag.mcp_format == "toml":
            doc = tomlkit.parse(p.read_text(encoding="utf-8"))
            table = doc.get(ag.mcp_key)
            if table is None:
                return {}
            return {name: agent_to_spec(ag, _toml_to_dict(val)) for name, val in table.items()}
        data = read_json(p, {}) or {}
        section = data.get(ag.mcp_key) or {}
        if not isinstance(section, dict):
            return {}
        return {name: agent_to_spec(ag, val) for name, val in section.items() if isinstance(val, dict)}
    except Exception as exc:  # 配置坏了也不能让整个工具挂掉
        log_action("mcp_read_fail", f"{ag.name}: {exc}", ok=False, agent=ag.key)
        return {}


def _toml_to_dict(v: Any) -> dict[str, Any]:
    if hasattr(v, "unwrap"):
        v = v.unwrap()
    return dict(v) if isinstance(v, dict) else {}


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def write_agent_mcp(
    ag: Agent, servers: dict[str, dict[str, Any]], drop: set[str] | None = None
) -> str:
    """把 manifest 的托管条目渲染进该工具配置.

    关键: servers 传的是 manifest **全量**。
      - 托管条目 = manifest 里有的, 一律按 manifest 决定(增/改/删)
      - 非托管条目 = 用户在这个工具里手配的, 原样保留, 绝不碰
    这样"manifest 里没有适用于本工具的项"就不会被误解为"清空整段".

    drop = 已经从 manifest 摘掉、但仍要从本工具抹掉的名字.
      只从 manifest 删除是不够的 —— 条目会退化成"非托管", 下次同步时被
      当作用户手配的原样保留, 于是"删了却还在". 删除流程必须把它带进来.

    返回 'written' / 'unchanged'.
    """
    p = ag.mcp_path
    p.parent.mkdir(parents=True, exist_ok=True)

    if ag.mcp_format == "toml":
        return _write_toml(ag, servers, drop)
    return _write_json(ag, servers, drop)


def _same_section(current: dict[str, Any], final: dict[str, Any]) -> bool:
    """两段配置在语义上是否等价(忽略空 env / 键序 / 数字字符串化)."""
    if set(current) != set(final):
        return False
    for key in current:
        a, b = current[key], final[key]
        if isinstance(a, dict) and isinstance(b, dict):
            if not same_spec(a, b):
                return False
        elif _normalize({"v": a}) != _normalize({"v": b}):
            return False
    return True


def _write_json(ag: Agent, managed: dict[str, dict[str, Any]], drop: set[str] | None = None) -> str:
    p = ag.mcp_path
    data = read_json(p, None)
    if not isinstance(data, dict):
        data = {}
    current = data.get(ag.mcp_key) or {}
    if not isinstance(current, dict):
        current = {}

    # 要"归本工具管"的名字: manifest 里的 + 本次显式抹掉的
    names = set(managed) | (drop or set())

    rendered = {name: spec_to_agent(ag, spec) for name, spec in desired_for(ag, managed).items()}
    final = {k: v for k, v in current.items() if k not in names}
    final.update(rendered)

    if p.exists() and _same_section(current, final):
        return "unchanged"

    data[ag.mcp_key] = final
    if p.exists():
        backup_file(p, ag.key)
    atomic_write_json(p, data)
    return "written"


def _write_toml(ag: Agent, managed: dict[str, dict[str, Any]], drop: set[str] | None = None) -> str:
    p = ag.mcp_path
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    doc = tomlkit.parse(text) if text.strip() else tomlkit.document()

    names = set(managed) | (drop or set())
    rendered = {name: spec_to_agent(ag, spec) for name, spec in desired_for(ag, managed).items()}

    tbl = doc.get(ag.mcp_key)
    if tbl is None or not hasattr(tbl, "items"):
        doc[ag.mcp_key] = tomlkit.table()
        tbl = doc[ag.mcp_key]

    before = {name: _toml_to_dict(v) for name, v in tbl.items()}
    expected = {k: v for k, v in before.items() if k not in names}
    expected.update(rendered)
    if p.exists() and _same_section(before, expected):
        return "unchanged"

    # 托管但本次不该存在的(比如 targets 不含本工具 / 本次要删除) -> 移除
    for name in list(tbl.keys()):
        if name in names and name not in rendered:
            del tbl[name]

    # 就地更新, 保留用户手配的 server 与注释
    for name, conf in rendered.items():
        if name in tbl:
            _update_toml_table(tbl[name], conf)
        else:
            t = tomlkit.table()
            _update_toml_table(t, conf)
            tbl[name] = t

    if p.exists():
        backup_file(p, ag.key)
    atomic_write_text(p, tomlkit.dumps(doc))
    return "written"


def _update_toml_table(table: Any, conf: dict[str, Any]) -> None:
    """把 conf 的字段同步进一个 tomlkit 表, 多余键删掉, 注释尽量保留."""
    for key in list(table.keys()):
        if key not in conf:
            del table[key]
    for key, val in conf.items():
        if key == "env" and isinstance(val, dict):
            from tomlkit import table as mk_table

            sub = mk_table()
            for k, v in val.items():
                sub[str(k)] = str(v)
            table["env"] = sub
        else:
            table[key] = val


# --------------------------------------------------------------------------- #
# 同步
# --------------------------------------------------------------------------- #
def _is_off_for(ag: Agent, spec: dict[str, Any]) -> bool:
    """该 server 在这个工具里是否应当关闭."""
    if bool(spec.get("disabled")):
        return True
    return ag.key in (spec.get("disable_in") or [])


def desired_for(ag: Agent, servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """该工具配置里应当出现的托管条目(含显式禁用).

    规则:
      - targets 存在时只考虑列出的工具
      - 该工具被关闭(disabled / disable_in)时:
          · 支持开关字段的工具 -> 照样写入并标为关闭(保留配置, 随时可开)
          · 不支持开关的工具   -> 不写入(否则"显示禁用实际在跑")
      - 其余原样
    """
    out: dict[str, dict[str, Any]] = {}
    for name, spec in servers.items():
        targets = spec.get("targets")
        if targets and ag.key not in targets:
            continue
        off = _is_off_for(ag, spec)
        if off and not ag.enable_field:
            continue
        item = copy.deepcopy(_clean(spec))
        if off:
            item["disabled"] = True
        out[name] = item
    return out


def scan_mcp() -> dict[str, Any]:
    """返回 MCP 矩阵: manifest 里每个 server 在 4 个工具的状态."""
    man = load_manifest()
    servers = man["servers"]
    per_agent = {ag.key: read_agent_mcp(ag) for ag in AGENTS}

    rows: list[dict[str, Any]] = []
    for name, spec in sorted(servers.items()):
        states: dict[str, str] = {}
        for ag in AGENTS:
            if not ag.available:
                states[ag.key] = "unavailable"
                continue
            if _is_off_for(ag, spec):
                states[ag.key] = "disabled"
                continue
            targets = spec.get("targets")
            if targets and ag.key not in targets:
                states[ag.key] = "excluded"
                continue
            cur = per_agent[ag.key].get(name)
            if cur is None:
                states[ag.key] = "missing"
            elif same_spec(cur, spec):
                states[ag.key] = "synced"
            else:
                states[ag.key] = "drift"
        rows.append(
            {
                "name": name,
                "command": spec.get("command") or spec.get("url") or "",
                "args": spec.get("args") or [],
                "env_keys": sorted((spec.get("env") or {}).keys()),
                "disabled": bool(spec.get("disabled")),
                "targets": spec.get("targets") or [a.key for a in AGENTS],
                "states": states,
                "spec": _clean(spec),
            }
        )

    # 各工具里存在但 manifest 没有的
    orphans: list[dict[str, Any]] = []
    seen = set()
    for ag in AGENTS:
        for name in per_agent[ag.key]:
            if name in servers:
                continue
            key = name
            if key in seen:
                continue
            seen.add(key)
            holders = [a.key for a in AGENTS if name in per_agent[a.key]]
            orphans.append({"name": name, "agents": holders})

    return {
        "manifest": short(MCP_MANIFEST),
        "total": len(rows),
        "rows": rows,
        "orphans": orphans,
        "agents": [
            {
                "key": a.key,
                "name": a.name,
                "available": a.available,
                "count": len(per_agent[a.key]),
                "path": short(a.mcp_path),
                "format": a.mcp_format,
            }
            for a in AGENTS
        ],
    }


def sync_mcp(targets: list[str] | None = None) -> list[dict[str, Any]]:
    """把 manifest 渲染进指定工具(默认全部). 返回逐工具结果."""
    man = load_manifest()
    servers = man["servers"]
    results: list[dict[str, Any]] = []

    for ag in AGENTS:
        if not ag.available:
            continue
        if targets and ag.key not in targets:
            continue
        desired = desired_for(ag, servers)
        try:
            status = write_agent_mcp(ag, servers)
            ok = True
            msg = "已写入" if status == "written" else "本来就一致"
        except Exception as exc:
            ok = False
            msg = f"写入失败: {exc}"
        results.append(
            {
                "agent": ag.key,
                "agent_name": ag.name,
                "ok": ok,
                "status": "written" if ok and msg == "已写入" else ("unchanged" if ok else "error"),
                "message": msg,
                "count": len(desired),
            }
        )
        log_action("mcp_sync", f"{ag.name}: {msg} ({len(desired)} 个)", ok=ok, agent=ag.key)

    return results


# --------------------------------------------------------------------------- #
# 从现有配置反向导入
# --------------------------------------------------------------------------- #
def import_from_agents(overwrite: bool = False) -> dict[str, Any]:
    """首次运行: 把各工具现有的 MCP 收进 manifest.

    安全默认: 新导入的 server 的 targets 只包含"发现它的那些工具",
    绝不自动扩散到其他工具 —— 像 Codex 的 node_repl/cua_repl 绑定了
    本机运行时管道, 铺到别家必炸. 共享交给用户在界面上显式勾选.

    同名但内容不一致时视为冲突, 按多数派取值并报告差异.
    """
    man = load_manifest()
    servers: dict[str, Any] = man["servers"]

    # name -> {agent_key: spec}
    found: dict[str, dict[str, dict[str, Any]]] = {}
    for ag in AGENTS:
        for name, spec in read_agent_mcp(ag).items():
            found.setdefault(name, {})[ag.key] = spec

    added: list[str] = []
    conflicts: list[dict[str, Any]] = []

    for name, by_agent in found.items():
        holders = list(by_agent.keys())
        if name in servers:
            # 已存在: 只补充 targets, 不动定义
            if overwrite:
                spec = next(iter(by_agent.values()))
                servers[name] = {**spec, "targets": holders}
            else:
                cur_targets = servers[name].get("targets")
                if cur_targets:
                    servers[name]["targets"] = sorted(set(cur_targets) | set(holders))
                variants = list(by_agent.values())
                if any(not same_spec(variants[0], v) for v in variants[1:]):
                    conflicts.append({"name": name, "agents": holders, "detail": "各工具定义不一致"})
            continue

        # 按"去掉开关后的定义"分组: 同一定义但开关不同, 视为同一份配置
        def shape(s: dict[str, Any]) -> dict[str, Any]:
            n = _normalize(s)
            n.pop("disabled", None)
            return n

        groups: list[list[tuple[str, dict[str, Any]]]] = []
        for ag_key, spec in by_agent.items():
            for grp in groups:
                if shape(grp[0][1]) == shape(spec):
                    grp.append((ag_key, spec))
                    break
            else:
                groups.append([(ag_key, spec)])
        groups.sort(key=len, reverse=True)
        main = groups[0]
        spec = copy.deepcopy(main[0][1])
        spec["targets"] = sorted(k for k, _ in main)
        spec.pop("disabled", None)
        off = sorted(k for k, s in main if s.get("disabled"))
        if off:
            spec["disable_in"] = off
        servers[name] = spec
        added.append(name)

        if len(groups) > 1:
            conflicts.append(
                {
                    "name": name,
                    "agents": holders,
                    "detail": f"{len(groups)} 种不同定义, 已取 {len(main)} 票的版本",
                }
            )

    man["servers"] = servers
    save_manifest(man)
    log_action("mcp_import", f"新增 {len(added)} 个, 冲突 {len(conflicts)} 个")
    return {"added": added, "conflicts": conflicts, "total": len(servers)}


def upsert_server(name: str, spec: dict[str, Any]) -> None:
    man = load_manifest()
    man["servers"][name] = spec
    save_manifest(man)
    log_action("mcp_upsert", name)


#: 被删掉的 server 定义存档 —— 后悔时照着它手写回来即可
DELETED_ARCHIVE = BACKUP_DIR / "deleted-mcp.json"


def _archive_deleted(name: str, spec: dict[str, Any]) -> None:
    data = read_json(DELETED_ARCHIVE, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data[name] = {
        "deleted_at": datetime.now().isoformat(timespec="seconds"),
        "spec": copy.deepcopy(spec),
    }
    atomic_write_json(DELETED_ARCHIVE, data)


def delete_server(name: str, agents: list[str] | None = None) -> dict[str, Any]:
    """从真源和各工具配置里彻底删掉一个 MCP server.

    两步缺一不可:
      1. 从 manifest 摘掉, 并先把定义存进 data/backups/deleted-mcp.json
      2. 带着 drop={name} 重写各工具配置 —— 否则它退化成"非托管条目"
         被原样保留, 删了却还在
    """
    man = load_manifest()
    spec = man["servers"].get(name)
    if spec is None:
        return {"ok": False, "message": f"真源里没有 {name}", "agents": []}

    _archive_deleted(name, spec)
    man["servers"].pop(name)
    save_manifest(man)

    rest = man["servers"]
    results: list[dict[str, Any]] = []
    for ag in AGENTS:
        if not ag.available:
            continue
        if agents and ag.key not in agents:
            continue
        had = name in read_agent_mcp(ag)
        try:
            write_agent_mcp(ag, rest, drop={name})
            results.append({"agent": ag.key, "name": ag.name, "ok": True, "had": had})
        except Exception as exc:
            results.append(
                {"agent": ag.key, "name": ag.name, "ok": False, "had": had, "message": f"{type(exc).__name__}: {exc}"}
            )

    cleared = sum(1 for r in results if r["had"])
    failed = sum(1 for r in results if not r["ok"])
    log_action(
        "mcp_delete",
        f"{name}: 从 {cleared} 处抹掉" + (f", {failed} 处失败" if failed else ""),
        ok=failed == 0,
    )
    msg = f"已删除 {name}"
    if cleared:
        msg += f", 清掉了 {cleared} 处配置"
    if failed:
        msg += f", {failed} 处写入失败"
    return {"ok": True, "message": msg, "agents": results, "archive": short(DELETED_ARCHIVE)}


def drop_orphan(name: str, agents: list[str] | None = None) -> dict[str, Any]:
    """抹掉不在 manifest 里的游离条目(用户在某工具里手配的).

    只动这一个名字, 其余手配条目原样保留 —— 用的还是"托管项之外一律不碰"
    那套规则, 只是把名字临时塞进托管集合里以便写空.
    """
    man = load_manifest()
    if name in man["servers"]:
        return {"ok": False, "message": "它在真源里, 应当用删除而不是清理"}

    removed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for ag in AGENTS:
        if not ag.available:
            continue
        if agents and ag.key not in agents:
            continue
        if name not in read_agent_mcp(ag):
            continue
        try:
            write_agent_mcp(ag, man["servers"], drop={name})
            removed.append({"agent": ag.key, "name": ag.name})
        except Exception as exc:
            failed.append({"agent": ag.key, "name": ag.name, "message": str(exc)})

    log_action("mcp_drop_orphan", f"{name}: {len(removed)} 处", ok=not failed)
    return {
        "ok": bool(removed) or not failed,
        "message": f"已从 {len(removed)} 处清除 {name}" if removed else "没有可清除的位置",
        "removed": removed,
        "failed": failed,
    }


def import_orphan(name: str, from_agents: list[str] | None = None) -> bool:
    """把某个工具里的游离条目收进 manifest."""
    for ag in AGENTS:
        if from_agents and ag.key not in from_agents:
            continue
        cur = read_agent_mcp(ag)
        if name in cur:
            upsert_server(name, cur[name])
            return True
    return False
