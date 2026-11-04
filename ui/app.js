/* SkillHub 前端 —— 无框架, 直接操作 DOM. */

const AGENT_ORDER = ['claude', 'codex', 'qoder', 'workbuddy'];

const STATE = {
  boot: null,
  skills: null,
  mcp: null,
  logs: [],
  stats: null,
  view: 'overview',
};

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

/* ---------------------------------------------------------------- 通信 */
async function call(method, ...args) {
  if (!window.pywebview || !window.pywebview.api) throw new Error('桥接尚未就绪');
  const res = await window.pywebview.api[method](...args);
  if (!res || res.ok !== true) throw new Error((res && res.error) || '调用失败');
  return res.data;
}

let toastTimer = null;
function toast(msg, isErr) {
  const t = el('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = 'toast'), 2600);
}

async function busy(btn, fn) {
  if (btn) btn.disabled = true;
  try {
    await fn();
  } catch (e) {
    toast(e.message || String(e), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- 加载 */
async function loadAll() {
  const [boot, data] = await Promise.all([call('bootstrap'), call('refresh')]);
  STATE.boot = boot;
  STATE.skills = data.skills;
  STATE.mcp = data.mcp;
  STATE.logs = data.logs;
  STATE.stats = data.stats;
  renderAll();

  // 启动即自动补齐新装 skill 的链接(只补缺失, 不动已有副本)
  try {
    const auto = await call('auto_sync');
    if (auto && auto.changed > 0) {
      const names = [...new Set((auto.results || []).map((r) => r.skill))].slice(0, 3).join('、');
      toast(`已自动补齐 ${auto.changed} 处链接：${names}`);
      await refresh();
    }
  } catch (e) {
    console.warn('自动同步跳过:', e);
  }
}

function renderAll() {
  renderSide();
  renderOverview();
  renderSkills();
  renderMcp();
  renderLogs();
}

/* ---------------------------------------------------------------- 侧栏 */
function renderSide() {
  const s = STATE.stats;
  el('navSkillCount').textContent = s ? s.skills : '0';
  el('navMcpCount').textContent = s ? s.mcp : '0';
  if (STATE.boot) {
    el('footSource').textContent = STATE.boot.home;
    el('footVersion').textContent = 'v' + STATE.boot.version;
  }
}

function setView(view) {
  STATE.view = view;
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === view));
  ['overview', 'skills', 'mcp', 'logs'].forEach((v) => (el('view-' + v).hidden = v !== view));

  const titles = {
    overview: ['总览', '四个工具的 Skill 与 MCP 是否一致'],
    skills: ['Skills', '同一份真源，靠目录链接分发到各工具'],
    mcp: ['MCP', '一处维护，按各家格式渲染写入'],
    logs: ['操作记录', '所有改动都可回溯，备份在 data/backups'],
  };
  el('pageTitle').textContent = titles[view][0];
  el('pageSub').textContent = titles[view][1];
  const hide = view === 'logs';
  el('btnSyncAll').style.display = hide ? 'none' : '';
  el('btnAutoSync').style.display = hide ? 'none' : '';
}

/* ---------------------------------------------------------------- 总览 */
function renderOverview() {
  const s = STATE.stats;
  const sk = STATE.skills;
  const mc = STATE.mcp;
  if (!s) return;

  el('pageSub').textContent = `${sk.total} 个 Skill · ${mc.total} 个 MCP 服务 · 真源 ${sk.sources_dir}`;

  // 三项足够: 总数、已经打通了多少、还有多少要处理。
  // 原来的"待处理不一致"其实是"副本偏离"与 MCP 漂移的加总, 属于同一件事算两遍。
  const pending = s.skills_drift + s.mcp_drift + s.unmanaged + s.orphans;
  const pendingDetail = `副本偏离 ${s.skills_drift} · MCP 不一致 ${s.mcp_drift} · 未纳管 ${s.unmanaged + s.orphans}`;
  el('healthStrip').innerHTML = `
    <div class="health-item"><div class="health-num">${s.skills}</div><div class="health-label">Skill 总数</div></div>
    <div class="health-item"><div class="health-num is-ok">${s.skills_linked}</div><div class="health-label">已链接份数</div></div>
    <div class="health-item" title="${esc(pendingDetail)}">
      <div class="health-num ${pending ? 'is-warn' : 'is-ok'}">${pending}</div>
      <div class="health-label">待处理</div>
    </div>
  `;

  el('agentGrid').innerHTML = sk.agents
    .map((a) => {
      const mcpAgent = mc.agents.find((x) => x.key === a.key) || {};
      const auto = a.auto_sync !== false;
      return `
      <div class="agent-card ${a.available ? '' : 'is-off'} ${auto ? '' : 'is-muted'}">
        <div class="agent-head">
          <span class="dot ${a.available ? '' : 'off'}"></span>
          <span class="agent-name">${esc(a.name)}</span>
          <label class="toggle" title="${auto ? '参与自动同步：新 skill 会自动铺进来' : '不参与自动同步：不会再收到其他 skill'}">
            <input type="checkbox" data-auto="${a.key}" ${auto ? 'checked' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </label>
        </div>
        <div class="agent-metrics">
          <div class="metric"><span class="metric-num">${a.count}</span><span class="metric-label">Skills</span></div>
          <div class="metric"><span class="metric-num ok">${a.linked}</span><span class="metric-label">已链接</span></div>
          <div class="metric"><span class="metric-num ${a.copies ? 'warn' : ''}">${a.copies}</span><span class="metric-label">副本</span></div>
          <div class="metric"><span class="metric-num ${a.broken ? 'bad' : ''}">${mcpAgent.count || 0}</span><span class="metric-label">MCP</span></div>
        </div>
        <div class="agent-path" title="${esc(a.skills_dir)}">${esc(a.skills_dir)}</div>
        <div class="agent-foot">
          <button class="mini-btn" data-openpath="${esc(a.skills_dir)}">打开目录</button>
          ${a.available ? '' : '<span class="mini-btn" style="color:var(--ink-3)">未安装</span>'}
          ${auto ? '' : '<span class="auto-note">不参与自动同步</span>'}
        </div>
      </div>`;
    })
    .join('');

  // 待办
  const todos = [];
  sk.rows.forEach((r) => {
    if (r.drift && Object.values(r.drift).some(Boolean)) {
      const bad = Object.entries(r.drift).filter(([, v]) => v).map(([k]) => nameOf(k));
      todos.push({ tag: '副本偏离', text: `${r.name} 在 ${bad.join('、')} 里的内容与真源不同，建议改为链接` });
    }
  });
  sk.unmanaged.forEach((u) => todos.push({ tag: '未纳管', text: `${u.name} 只存在于 ${u.agents.map(nameOf).join('、')}，收进真源后可共享` }));
  mc.rows.forEach((r) => {
    Object.entries(r.states).forEach(([k, v]) => {
      if (v === 'drift' || v === 'missing') {
        todos.push({ tag: 'MCP 不一致', text: `${r.name} 在 ${nameOf(k)} 中${v === 'missing' ? '缺失' : '与真源不同'}` });
      }
    });
  });
  mc.orphans.forEach((o) => todos.push({ tag: 'MCP 游离', text: `${o.name} 存在于 ${o.agents.map(nameOf).join('、')}，尚未纳入真源` }));

  const panel = el('attentionPanel');
  if (todos.length) {
    panel.hidden = false;
    // 按类型分组: 原来每行都重复一遍"未纳管", 12 行就是 12 次同样的标签
    const byTag = new Map();
    todos.forEach((t) => {
      if (!byTag.has(t.tag)) byTag.set(t.tag, []);
      byTag.get(t.tag).push(t.text);
    });
    el('todoList').innerHTML = [...byTag.entries()]
      .map(([tag, items]) => {
        const shown = items.slice(0, 4);
        const more = items.length - shown.length;
        return `
        <li class="todo-group">
          <div class="todo-group-head">
            <span class="todo-tag">${esc(tag)}</span>
            <span class="todo-count">${items.length} 条</span>
          </div>
          <ul class="todo-items">
            ${shown.map((x) => `<li>${esc(x)}</li>`).join('')}
            ${more > 0 ? `<li class="todo-more">还有 ${more} 条，到对应页面处理</li>` : ''}
          </ul>
        </li>`;
      })
      .join('');
  } else {
    panel.hidden = true;
  }
}

function nameOf(key) {
  const a = STATE.skills && STATE.skills.agents.find((x) => x.key === key);
  return a ? a.name : key;
}

/* ---------------------------------------------------------------- Skills */
const SKILL_LABEL = { linked: '已链接', copy: '副本', missing: '未装', broken: '断链', foreign: '外部' };

function renderSkills() {
  const sk = STATE.skills;
  if (!sk) return;
  const q = (el('skillSearch').value || '').trim().toLowerCase();
  const rows = sk.rows.filter((r) => !q || r.name.toLowerCase().includes(q) || (r.description || '').toLowerCase().includes(q));

  el('skillNote').textContent = `共 ${sk.total} 个，真源 ${sk.sources_dir}`;

  const tbody = el('skillTable').querySelector('tbody');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty">没有匹配的 skill</div></td></tr>`;
  } else {
    tbody.innerHTML = rows
      .map((r) => {
        const cells = AGENT_ORDER.map((k) => {
          const st = r.states[k];
          const drifted = r.drift && r.drift[k];
          const label = st === 'copy' && drifted ? '副本·已改' : SKILL_LABEL[st] || st;
          const clickable = st !== 'missing' || true;
          return `<td><span class="pill st-${st} clickable" data-skill="${esc(r.name)}" data-agent="${k}"
                    title="${st === 'copy' ? '副本：点击改为链接（原内容会备份）' : st === 'linked' ? '已链接：点击断开' : '点击建立链接'}">${label}</span></td>`;
        }).join('');
        const canFix = AGENT_ORDER.some((k) => r.states[k] === 'copy');
        const alias =
          r.display_name && r.display_name !== r.name
            ? `<span class="skill-alias">${esc(r.display_name)}</span>`
            : '';
        return `
          <tr>
            <td>
              <div class="skill-name">${esc(r.name)}${alias}</div>
              ${r.description ? `<div class="skill-desc" title="${esc(r.description)}">${esc(r.description)}</div>` : ''}
            </td>
            ${cells}
            <td class="col-op">${canFix ? `<button class="mini-btn" data-fixskill="${esc(r.name)}">改链接</button>` : ''}</td>
          </tr>`;
      })
      .join('');
  }

  // 未纳管: 按平台分组, 每个 skill 只在自己所在的平台下出现
  const panel = el('unmanagedPanel');
  if (sk.unmanaged.length) {
    panel.hidden = false;
    el('unmanagedGroups').innerHTML = groupByAgent(sk.unmanaged, sk.agents, (u, agentKey) =>
      `<span class="chip">${esc(u.name)}
         <button class="chip-btn" data-adopt="${esc(u.name)}" data-agent="${agentKey}" title="收进真源, 之后所有平台共享">收进真源</button>
       </span>`
    );
  } else {
    panel.hidden = true;
  }
}

/** 按"归属哪些平台"分组.

    同一项在多个平台都有时只出现一次, 归到「A + B」组里,
    而不是在两个平台组里各出现一次 —— 否则同一项会有两个「收进真源」按钮,
    点哪个效果一样, 看着像要收两次。
 */
function groupByAgent(items, agents, makeChip) {
  const order = (agents || []).map((a) => a.key);
  const buckets = new Map();

  items.forEach((it) => {
    const owners = order.filter((k) => (it.agents || []).includes(k));
    if (!owners.length) return;
    const key = owners.join('|');
    if (!buckets.has(key)) buckets.set(key, { owners, list: [] });
    buckets.get(key).list.push(it);
  });

  const groups = [...buckets.values()].sort((a, b) => {
    const ai = order.indexOf(a.owners[0]);
    const bi = order.indexOf(b.owners[0]);
    if (ai !== bi) return ai - bi;
    return b.owners.length - a.owners.length;
  });
  if (!groups.length) return '';

  return groups
    .map((g) => {
      const title =
        g.owners.length > 1 ? g.owners.map(nameOf).join(' + ') : '仅 ' + nameOf(g.owners[0]);
      return `
      <div class="group">
        <div class="group-head">
          <span class="group-name">${esc(title)}</span>
          <span class="group-count">${g.list.length} 个</span>
        </div>
        <div class="chip-row">${g.list.map((it) => makeChip(it, g.owners[0])).join('')}</div>
      </div>`;
    })
    .join('');
}

/* ---------------------------------------------------------------- MCP */
const MCP_LABEL = { synced: '已同步', drift: '有差异', missing: '未写入', excluded: '未启用', disabled: '已关闭', unavailable: '未安装' };

function renderMcp() {
  const mc = STATE.mcp;
  if (!mc) return;
  el('mcpNote').textContent = `共 ${mc.total} 个，来自 ${mc.manifest} · 点击格子可切换该工具是否启用`;

  const tbody = el('mcpTable').querySelector('tbody');
  if (!mc.rows.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty">真源里还没有 MCP，点右上角导入现有配置</div></td></tr>`;
  } else {
    tbody.innerHTML = mc.rows
      .map((r) => {
        const cells = AGENT_ORDER.map((k) => {
          const st = r.states[k];
          const clickable = st !== 'unavailable';
          const title = r.disabled
            ? '已被全局停用'
            : st === 'excluded'
            ? '未启用：点击加入该工具'
            : st === 'disabled'
            ? '已关闭：点击恢复'
            : '点击在该工具关闭';
          return `<td><span class="pill st-${st} ${clickable ? 'clickable' : ''}" ${clickable ? `data-mcp="${esc(r.name)}" data-agent="${k}"` : ''} title="${title}">${MCP_LABEL[st] || st}</span></td>`;
        }).join('');
        const cmd = (r.command || '').split(/[\\/]/).pop();
        return `
          <tr>
            <td>
              <div class="skill-name">${esc(r.name)}</div>
              <div class="skill-path" title="${esc(r.command)}">${esc(cmd || r.command)} ${esc((r.args || []).slice(0, 2).join(' '))}</div>
            </td>
            ${cells}
            <td class="col-op">
              <button class="mini-btn" data-mcpglobal="${esc(r.name)}">${r.disabled ? '启用' : '停用'}</button>
            </td>
          </tr>`;
      })
      .join('');
  }

  const panel = el('orphanPanel');
  if (mc.orphans.length) {
    panel.hidden = false;
    el('orphanGroups').innerHTML = groupByAgent(mc.orphans, mc.agents, (o, agentKey) =>
      `<span class="chip">${esc(o.name)}
         <button class="chip-btn" data-adoptmcp="${esc(o.name)}" data-agents="${agentKey}" title="收进真源">收进真源</button>
       </span>`
    );
  } else {
    panel.hidden = true;
  }
}

/* ---------------------------------------------------------------- 日志 */
function renderLogs() {
  const rows = STATE.logs || [];
  el('logBody').innerHTML = rows.length
    ? rows
        .map(
          (l) =>
            `<tr><td>${esc(l.time)}</td><td>${esc(l.action)}</td><td>${esc(l.detail)}</td>
             <td class="${l.ok ? 'tag-ok' : 'tag-fail'}">${l.ok ? '成功' : '失败'}</td></tr>`
        )
        .join('')
    : `<tr><td colspan="4"><div class="empty">暂无记录</div></td></tr>`;
}

/* ---------------------------------------------------------------- 交互 */
async function refresh() {
  const data = await call('refresh');
  STATE.skills = data.skills;
  STATE.mcp = data.mcp;
  STATE.logs = data.logs;
  STATE.stats = data.stats;
  renderAll();
}

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-view],[data-skill],[data-mcp],[data-adopt],[data-adoptmcp],[data-fixskill],[data-mcpglobal],[data-openpath],[data-open]');
  if (!t) return;

  // 导航
  if (t.dataset.view) return setView(t.dataset.view);

  if (t.dataset.open) {
    return busy(null, async () => {
      const r = await call('open_path', t.dataset.open);
      toast(r.message || '已打开');
    });
  }

  if (t.dataset.openpath) {
    return call('open_path', 'source').catch((e) => toast(e.message, true));
  }

  // skill 状态徽标
  if (t.dataset.skill) {
    return busy(null, async () => {
      const r = await call('toggle_skill', t.dataset.agent, t.dataset.skill);
      toast(r.message, !r.ok);
      await refresh();
    });
  }

  // 一键改链接
  if (t.dataset.fixskill) {
    const name = t.dataset.fixskill;
    const row = STATE.skills.rows.find((r) => r.name === name);
    const targets = AGENT_ORDER.filter((k) => row.states[k] === 'copy' || row.states[k] === 'broken');
    return busy(t, async () => {
      let done = 0;
      for (const k of targets) {
        const r = await call('link_skill', k, name);
        if (r.ok) done += 1;
      }
      toast(`已完成 ${done}/${targets.length} 个工具`);
      await refresh();
    });
  }

  // 纳管 skill
  if (t.dataset.adopt) {
    return busy(t, async () => {
      const r = await call('adopt_skill', t.dataset.agent, t.dataset.adopt);
      toast(r.message, !r.ok);
      await refresh();
    });
  }

  // MCP 格子轮转
  if (t.dataset.mcp) {
    return busy(null, async () => {
      const r = await call('mcp_cycle', t.dataset.mcp, t.dataset.agent);
      if (!r.ok) return toast(r.message, true);
      toast(r.message);
      await refresh();
    });
  }

  // MCP 全局开关
  if (t.dataset.mcpglobal) {
    return busy(null, async () => {
      const r = await call('mcp_toggle_disabled', t.dataset.mcpglobal, null);
      toast(r.message, !r.ok);
      await refresh();
    });
  }

  // 纳管 MCP
  if (t.dataset.adoptmcp) {
    return busy(t, async () => {
      const r = await call('mcp_import_orphan', t.dataset.adoptmcp, (t.dataset.agents || '').split(',').filter(Boolean));
      toast(r.message, !r.ok);
      await refresh();
    });
  }
});

document.querySelectorAll('.nav-item').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
el('btnRefresh').addEventListener('click', (e) => busy(e.target, refresh));

/* 补齐缺失: 安全操作, 只给空位建链接 */
el('btnAutoSync').addEventListener('click', (e) =>
  busy(e.target, async () => {
    const r = await call('auto_sync');
    if (!r.changed) {
      toast('没有需要补的链接 —— 各工具都已是最新');
      return;
    }
    const names = [...new Set((r.results || []).map((x) => x.skill))].slice(0, 3).join('、');
    toast(`已补齐 ${r.changed} 处链接：${names}`);
    await refresh();
  })
);

/* 全量同步: 会改造副本, 需要二次确认 */
let syncArmTimer = null;
function disarmSync(btn) {
  delete btn.dataset.armed;
  btn.classList.remove('danger');
  btn.textContent = '全量同步';
}

el('btnSyncAll').addEventListener('click', (e) => {
  const btn = e.target;

  if (!btn.dataset.armed) {
    // 第一次点: 先算清楚会被改造多少个副本, 让数字说话
    const copies = STATE.skills
      ? STATE.skills.rows.reduce(
          (n, r) => n + Object.values(r.states).filter((s) => s === 'copy' || s === 'broken').length,
          0
        )
      : 0;
    btn.dataset.armed = '1';
    btn.classList.add('danger');
    btn.textContent = copies ? `确认改造 ${copies} 个副本？` : '确认全量同步？';
    clearTimeout(syncArmTimer);
    syncArmTimer = setTimeout(() => disarmSync(btn), 4000);
    return;
  }

  clearTimeout(syncArmTimer);
  disarmSync(btn);
  busy(btn, async () => {
    const a = await call('sync_skills');
    const b = await call('mcp_sync');
    const skillFail = a.failed.length;
    const mcpFail = (b.results || []).filter((r) => !r.ok).length;
    const msg = `Skill 变更 ${a.changed} 项，MCP 写入 ${(b.results || []).filter((r) => r.status === 'written').length} 个`;
    toast(skillFail + mcpFail ? `${msg}，失败 ${skillFail + mcpFail} 项` : msg, skillFail + mcpFail > 0);
    await refresh();
  });
});

el('btnMcpImport').addEventListener('click', (e) =>
  busy(e.target, async () => {
    const r = await call('mcp_import');
    toast(`导入完成：新增 ${r.added.length} 个，冲突 ${r.conflicts.length} 个`);
    await refresh();
  })
);

/* 自动同步开关: 某个工具不参与时, 不会再接收自动推送的 skill */
document.addEventListener('change', async (e) => {
  const cb = e.target.closest && e.target.closest('[data-auto]');
  if (!cb) return;
  const key = cb.dataset.auto;
  const on = cb.checked;
  try {
    const r = await call('set_auto_sync', key, on);
    toast(r.message);
    await refresh();
  } catch (err) {
    cb.checked = !on;
    toast(err.message || String(err), true);
  }
});

el('skillSearch').addEventListener('input', renderSkills);

/* ---------------------------------------------------------------- 启动 */
function boot() {
  setView('overview');
  busy(null, loadAll).catch((e) => {
    el('pageSub').textContent = '初始化失败：' + e.message;
  });
}

if (window.pywebview && window.pywebview.api) {
  boot();
} else {
  window.addEventListener('pywebviewready', boot);
  // 兜底：某些版本事件不触发
  setTimeout(() => {
    if (!STATE.boot && window.pywebview && window.pywebview.api) boot();
  }, 1200);
}
