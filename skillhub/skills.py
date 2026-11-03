"""Skill 扫描与目录联接 (junction) 同步.

状态机:
    linked   已链接到真源, 内容天然一致
    copy     是真实目录副本, 可能与真源漂移
    missing  该工具下没有这个 skill
    broken   链接存在但目标已删除
    foreign  链接指向真源以外的位置
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import settings
from .paths import AGENTS, BACKUP_DIR, SKILLS_SOURCE, Agent, is_windows, short
from .safeio import log_action

LINKED, COPY, MISSING, BROKEN, FOREIGN = "linked", "copy", "missing", "broken", "foreign"

STATE_LABEL = {
    LINKED: "已链接",
    COPY: "副本",
    MISSING: "未安装",
    BROKEN: "断链",
    FOREIGN: "外部链接",
}


# --------------------------------------------------------------------------- #
# 元数据
# --------------------------------------------------------------------------- #
@dataclass
class SkillMeta:
    #: 目录名 —— 一切路径操作的唯一依据, 绝不使用 frontmatter 里的 name
    name: str
    path: Path
    description: str = ""
    version: str = ""
    #: frontmatter 里的 name, 仅供展示. 很多 skill 会写成 "My Skill" 这种带空格的名字,
    #: 拿它拼路径必然找不到目录.
    display_name: str = ""

    @property
    def title(self) -> str:
        return self.display_name or self.name


def _parse_frontmatter(text: str) -> dict[str, str]:
    """极简 YAML frontmatter 解析, 只认 key: value 与 >- / | 折叠块."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}

    body = lines[1:end]
    out: dict[str, str] = {}
    i = 0
    while i < len(body):
        raw = body[i]
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            i += 1
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if val in (">-", ">", "|", "|-"):
            # 折叠/字面块: 吃掉后续缩进行
            buf: list[str] = []
            i += 1
            while i < len(body) and (body[i].startswith((" ", "\t")) or not body[i].strip()):
                buf.append(body[i].strip())
                i += 1
            joiner = " " if val.startswith(">") else "\n"
            out[key] = joiner.join(x for x in buf if x).strip()
            continue
        out[key] = val.strip("\"'")
        i += 1
    return out


def is_skill_dir(p: Path) -> bool:
    """严格判定: 必须是目录且含 SKILL.md(大小写不敏感).

    各工具的 skills 目录常年混入 .json 密钥、迁移标记、缓存等杂项,
    不做严格判定会把 agnes-secrets.json 当成 skill 铺到别家去。
    """
    if not p.is_dir() or p.name.startswith("."):
        return False
    try:
        return any(f.name.lower() in ("skill.md",) for f in p.iterdir() if f.is_file())
    except OSError:
        return False


def read_meta(skill_dir: Path) -> SkillMeta:
    meta = SkillMeta(name=skill_dir.name, path=skill_dir)
    md = skill_dir / "SKILL.md"
    if not md.exists():
        # 兜底: 有的包只有 README 或大小写不同
        for cand in ("skill.md", "README.md", "readme.md"):
            alt = skill_dir / cand
            if alt.exists():
                md = alt
                break
    if md.exists():
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return meta
        fm = _parse_frontmatter(text)
        # 注意: 只把 frontmatter 的 name 记成 display_name, 不覆盖目录名.
        # 目录名才是磁盘上的真实标识, 用 display_name 拼路径会找不到目录.
        meta.display_name = (fm.get("name") or "").strip() or meta.name
        meta.description = fm.get("description", "")
        meta.version = fm.get("version", "")
        if not meta.description:
            # 无 frontmatter 时取首个正文段落
            parts = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith(("#", "-", "|"))]
            meta.description = parts[0][:240] if parts else ""
    return meta


def dir_hash(path: Path, limit: int = 4000, max_file: int = 4 * 1024 * 1024) -> str:
    """内容指纹: 相对路径 + 文件内容.

    不能用 mtime —— 复制目录会把时间戳改成复制时刻, 内容明明一样却会被
    判成"已偏离", 天天弹假警报. 超过 max_file 的文件只记大小, 避免拖慢扫描.
    """
    import hashlib

    h = hashlib.sha256()
    n = 0
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            rel = fp.relative_to(path).as_posix()
            h.update(rel.encode())
            if st.st_size > max_file:
                h.update(f"|big|{st.st_size}".encode())
            else:
                try:
                    h.update(fp.read_bytes())
                except OSError:
                    h.update(f"|unreadable|{st.st_size}".encode())
            n += 1
            if n >= limit:
                return h.hexdigest()[:16]
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 链接探测
# --------------------------------------------------------------------------- #
def _is_link(p: Path) -> bool:
    if os.path.islink(p):
        return True
    isj = getattr(os.path, "isjunction", None)
    return bool(isj and isj(p))


def _real(p: Path) -> Path | None:
    try:
        return Path(os.path.realpath(p))
    except OSError:
        return None


def probe(link: Path, source: Path) -> str:
    """判定单个条目相对真源的状态."""
    if not os.path.lexists(link):
        return MISSING
    exists = link.exists()
    if _is_link(link):
        if not exists:
            return BROKEN
        real = _real(link)
        if real and real == Path(os.path.realpath(source)):
            return LINKED
        return FOREIGN
    return COPY


# --------------------------------------------------------------------------- #
# 链接创建 / 移除
# --------------------------------------------------------------------------- #
@dataclass
class OpResult:
    ok: bool
    message: str
    backup: str = ""


def _create_junction(link: Path, target: Path) -> tuple[bool, str]:
    link.parent.mkdir(parents=True, exist_ok=True)
    if is_windows():
        try:
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
            )
            if r.returncode == 0 and os.path.lexists(link):
                return True, "junction"
            detail = (r.stdout or b"") + (r.stderr or b"")
            err = detail.decode("gbk", errors="replace").strip()
        except OSError as exc:  # cmd 不可用
            err = str(exc)
    else:
        err = ""

    # 退路: 符号链接
    try:
        os.symlink(target, link, target_is_directory=True)
        if os.path.lexists(link):
            return True, "symlink"
    except OSError as exc:
        return False, f"junction 失败 ({err or '未知'}); symlink 也失败 ({exc})"
    return False, err or "未知错误"


def _stash_existing(entry: Path, agent_key: str, label: str) -> Path | None:
    """把已存在的副本(或错误链接)移到备份区, 而不是删除."""
    if not os.path.lexists(entry):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = BACKUP_DIR / agent_key / "skills"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{label}_{stamp}"
    i = 1
    while dest.exists():
        dest = dest_dir / f"{label}_{stamp}_{i}"
        i += 1
    if _is_link(entry):
        # 链接直接删掉即可(不能 move 链接本身以外的东西), 先记录
        try:
            os.unlink(entry)
        except OSError:
            os.rmdir(entry)
        return None
    shutil.move(str(entry), str(dest))
    return dest


def link_skill(ag: Agent, name: str) -> OpResult:
    """在该 agent 下建立指向真源的链接."""
    src = SKILLS_SOURCE / name
    if not src.exists():
        return OpResult(False, f"真源中不存在 skill: {name}")
    entry = ag.skills_dir / name

    state = probe(entry, src)
    if state == LINKED:
        return OpResult(True, "已经是链接状态")

    backup_note = ""
    stashed: Path | None = None
    if state in (COPY, FOREIGN, BROKEN):
        stashed = _stash_existing(entry, ag.key, name)
        if stashed:
            backup_note = f"原内容已备份至 {short(stashed)}"
        else:
            backup_note = "原有链接已移除"

    ok, how = _create_junction(entry, src)
    if not ok:
        # 建链接失败必须把副本放回去, 否则用户的 skill 就凭空消失了
        if stashed and stashed.exists():
            try:
                shutil.move(str(stashed), str(entry))
                backup_note = "原内容已自动还原"
            except OSError:
                backup_note = f"建链失败且自动还原失败, 内容在 {short(stashed)}"
        log_action("link_fail", f"{ag.name} / {name}: {how}", ok=False, agent=ag.key)
        return OpResult(False, f"创建链接失败: {how}. {backup_note}" if backup_note else f"创建链接失败: {how}")

    msg = f"已通过 {how} 链接到真源"
    if backup_note:
        msg += f", {backup_note}"
    log_action("link", f"{ag.name} / {name}: {how}", ok=True, agent=ag.key)
    return OpResult(True, msg, backup=backup_note)


def unlink_skill(ag: Agent, name: str) -> OpResult:
    """断开链接. 只删链接本身, 绝不碰真源."""
    entry = ag.skills_dir / name
    src = SKILLS_SOURCE / name
    if not os.path.lexists(entry):
        return OpResult(True, "本就不存在")

    state = probe(entry, src)
    if state == LINKED:
        try:
            os.unlink(entry)
        except OSError:
            try:
                os.rmdir(entry)  # junction 用 rmdir 删除, 不会删内容
            except OSError as exc:
                return OpResult(False, f"删除链接失败: {exc}")
        log_action("unlink", f"{ag.name} / {name}", ok=True, agent=ag.key)
        return OpResult(True, "已断开链接")

    if state == COPY:
        stashed = _stash_existing(entry, ag.key, name)
        log_action("remove_copy", f"{ag.name} / {name}", ok=True, agent=ag.key)
        return OpResult(True, f"副本已移入备份区: {short(stashed) if stashed else '-'}")

    # broken / foreign
    try:
        os.unlink(entry)
    except OSError:
        with_err = None
        try:
            os.rmdir(entry)
        except OSError as exc:
            with_err = exc
        if with_err:
            return OpResult(False, str(with_err))
    log_action("unlink", f"{ag.name} / {name} (异常链接)", ok=True, agent=ag.key)
    return OpResult(True, "已清理异常链接")


# --------------------------------------------------------------------------- #
# 迁移纳管: 把某工具下的独有副本搬进真源
# --------------------------------------------------------------------------- #
def adopt_skill(ag: Agent, name: str) -> OpResult:
    entry = ag.skills_dir / name
    dst = SKILLS_SOURCE / name
    if not entry.exists():
        return OpResult(False, "该工具下没有这个 skill")
    if _is_link(entry):
        return OpResult(False, "这已经是个链接, 无需纳管")
    if dst.exists():
        return OpResult(False, f"真源中已存在同名 skill: {name}")

    try:
        shutil.move(str(entry), str(dst))
    except OSError as exc:
        return OpResult(False, f"搬移失败: {exc}")

    ok, how = _create_junction(entry, dst)
    log_action("adopt", f"从 {ag.name} 纳管 {name}", ok=True, agent=ag.key)
    if not ok:
        return OpResult(True, f"已纳入真源, 但回链失败: {how}")
    return OpResult(True, f"已纳入真源并回链 ({how})")


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def list_sources() -> list[SkillMeta]:
    if not SKILLS_SOURCE.exists():
        return []
    out = []
    for child in sorted(SKILLS_SOURCE.iterdir(), key=lambda p: p.name.lower()):
        if not is_skill_dir(child):
            continue
        out.append(read_meta(child))
    return out


def source_hashes() -> dict[str, str]:
    return {m.name: dir_hash(m.path) for m in list_sources()}


def scan() -> dict[str, Any]:
    """返回完整矩阵: 每个 skill 在 4 个工具下的状态."""
    sources = list_sources()
    src_names = [m.name for m in sources]
    src_set = set(src_names)

    # 每个工具目录下实际有啥
    per_agent_entries: dict[str, dict[str, str]] = {}
    extras: dict[str, list[str]] = {}
    for ag in AGENTS:
        entries: dict[str, str] = {}
        extra: list[str] = []
        if ag.skills_dir.exists():
            for child in sorted(ag.skills_dir.iterdir(), key=lambda p: p.name.lower()):
                if not is_skill_dir(child):
                    continue
                st = probe(child, SKILLS_SOURCE / child.name)
                entries[child.name] = st
                if child.name not in src_set:
                    extra.append(child.name)
        per_agent_entries[ag.key] = entries
        extras[ag.key] = extra

    rows: list[dict[str, Any]] = []
    for meta in sources:
        states: dict[str, str] = {}
        drift: dict[str, bool] = {}
        for ag in AGENTS:
            st = per_agent_entries[ag.key].get(meta.name, MISSING)
            states[ag.key] = st
            if st == COPY:
                try:
                    drift[ag.key] = dir_hash(ag.skills_dir / meta.name) != dir_hash(meta.path)
                except OSError:
                    drift[ag.key] = True
        rows.append(
            {
                "name": meta.name,
                "display_name": meta.display_name or meta.name,
                "description": meta.description,
                "version": meta.version,
                "path": short(meta.path),
                "states": states,
                "drift": drift,
                "linked_count": sum(1 for v in states.values() if v == LINKED),
                "agent_total": sum(1 for a in AGENTS if a.available),
            }
        )

    # 只在某些工具里存在、尚未纳管的
    unmanaged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ag in AGENTS:
        for nm in extras[ag.key]:
            if nm in seen:
                continue
            seen.add(nm)
            holders = [a.key for a in AGENTS if nm in per_agent_entries[a.key]]
            unmanaged.append({"name": nm, "agents": holders, "description": ""})

    return {
        "sources_dir": short(SKILLS_SOURCE),
        "total": len(rows),
        "rows": rows,
        "unmanaged": unmanaged,
        "agents": [
            {
                "key": a.key,
                "name": a.name,
                "available": a.available,
                "skills_dir": short(a.skills_dir),
                "count": sum(1 for v in per_agent_entries[a.key].values() if v in (LINKED, COPY)),
                "linked": sum(1 for v in per_agent_entries[a.key].values() if v == LINKED),
                "copies": sum(1 for v in per_agent_entries[a.key].values() if v == COPY),
                "broken": sum(1 for v in per_agent_entries[a.key].values() if v in (BROKEN, FOREIGN)),
            }
            for a in AGENTS
        ],
    }


def sync_all(targets: list[str] | None = None, only: list[str] | None = None) -> list[dict[str, Any]]:
    """把真源里所有 skill 铺到指定工具(默认全部可用工具)."""
    results: list[dict[str, Any]] = []
    agents = [a for a in AGENTS if a.available and (targets is None or a.key in targets)]
    names = [m.name for m in list_sources()]
    if only:
        names = [n for n in names if n in set(only)]
    for ag in agents:
        for name in names:
            st = probe(ag.skills_dir / name, SKILLS_SOURCE / name)
            if st == LINKED:
                continue
            r = link_skill(ag, name)
            results.append({"agent": ag.key, "agent_name": ag.name, "skill": name, "ok": r.ok, "message": r.message})
    log_action("sync_all", f"{len(results)} 项变更", ok=all(x["ok"] for x in results) if results else True)
    return results


# --------------------------------------------------------------------------- #
# 自动同步: 只补缺失, 不动存量
# --------------------------------------------------------------------------- #
def _link_missing(name: str, targets: list[str] | None = None) -> list[dict[str, Any]]:
    """给单个 skill 在缺失它的工具里建链接. 返回实际动作.

    targets 为 None 时按用户设置决定哪些工具参与自动同步;
    显式传入时以传入为准(命令行 --agent 用)。
    """
    done: list[dict[str, Any]] = []
    for ag in AGENTS:
        if not ag.available or not ag.skills_dir.exists():
            continue
        if not settings.apply_to(ag, targets):
            continue
        if probe(ag.skills_dir / name, SKILLS_SOURCE / name) != MISSING:
            continue
        r = link_skill(ag, name)
        done.append({"agent": ag.key, "agent_name": ag.name, "skill": name, "ok": r.ok, "message": r.message})
    return done


def sync_new(targets: list[str] | None = None) -> list[dict[str, Any]]:
    """给新出现的 skill 补上链接. 已存在的一切(副本/断链/外部链接)原样不动.

    这是给"装完自动同步"用的入口, 所以必须**绝对安全**:
      - missing  -> 建链接(目标位置本来就是空的, 不覆盖任何东西)
      - copy     -> 跳过. 用户目录里已有内容, 自动改造成链接等于静默搬走文件,
                    必须由人在界面上确认
      - broken   -> 跳过, 留给人工处理
      - foreign  -> 跳过
    """
    results: list[dict[str, Any]] = []
    for meta in list_sources():
        results.extend(_link_missing(meta.name, targets))

    if results:
        log_action(
            "auto_sync",
            f"自动补齐 {len(results)} 项: " + ", ".join(f"{x['skill']}@{x['agent']}" for x in results[:8]),
            ok=all(x["ok"] for x in results),
        )
    return results


def watch(interval: float = 3.0, targets: list[str] | None = None, on_event=None) -> None:
    """轮询真源目录, 发现新 skill 就自动补齐链接. 阻塞运行直到 Ctrl+C.

    用轮询而不是 watchdog: 只列一次目录, 开销可忽略, 还省掉一个依赖.
    """
    import time

    def emit(kind: str, msg: str) -> None:
        if on_event:
            on_event(kind, msg)

    known = {m.name for m in list_sources()}
    emit("start", f"已监听 {short(SKILLS_SOURCE)}, 当前 {len(known)} 个 skill")

    try:
        while True:
            time.sleep(interval)
            current = {m.name for m in list_sources()}
            added, removed = current - known, known - current

            if added:
                linked: list[str] = []
                for name in sorted(added):
                    for rec in _link_missing(name, targets):
                        if rec["ok"]:
                            linked.append(f"{name}@{rec['agent']}")
                if linked:
                    emit("added", f"新增 {len(added)} 个 skill, 已链接 {len(linked)} 处: " + ", ".join(linked[:6]))
                else:
                    emit("added", f"新增 {len(added)} 个 skill (无需补链)")

            if removed:
                emit("removed", f"真源中移除 {len(removed)} 个: {', '.join(sorted(removed))}")

            known = current
    except KeyboardInterrupt:
        emit("stop", "已停止监听")
