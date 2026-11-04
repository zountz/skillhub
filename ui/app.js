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

/* 写进 HTML 属性里的文本: 除了转义, 还要把换行变成字符引用,
   否则属性值里的裸换行会被解析器吃掉, 多行说明就挤成一行了。 */
const escAttr = (s) => esc(s).replace(/\r?\n/g, '&#10;');

/* 悬停只给一段"回忆钩子", 太长的说明会把整张表盖住, 全文留给详情面板 */
const clip = (s, n = 240) => {
  const t = String(s || '');
  return t.length > n ? t.slice(0, n).trimEnd() + '…\n点开看全文' : t;
};

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

/* 二段确认按钮：第一次点只把按钮变红并改成一句问话，4.5 秒内再点才真执行。
   删除类操作全走这里 —— 列表里误点一下不该直接毁东西。 */
function armButton(btn, question, run) {
  if (!btn.dataset.label) btn.dataset.label = btn.textContent.trim();

  if (btn.dataset.armed) {
    clearTimeout(Number(btn.dataset.timer));
    delete btn.dataset.armed;
    btn.classList.remove('is-danger');
    btn.textContent = btn.dataset.label;
    return run();
  }

  btn.dataset.armed = '1';
  btn.classList.add('is-danger');
  btn.textContent = question;
  btn.dataset.timer = String(
    setTimeout(() => {
      delete btn.dataset.armed;
      btn.classList.remove('is-danger');
      btn.textContent = btn.dataset.label;
    }, 4500)
  );
}

/* ---------------------------------------------------------------- 悬停说明 */
let tipTarget = null;
let tipTimer = null;

function hideTip() {
  clearTimeout(tipTimer);
  tipTarget = null;
  el('tip').classList.remove('show');
}

function placeTip(t) {
  const box = el('tip');
  const r = t.getBoundingClientRect();
  let x = r.left;
  let y = r.bottom + 8;
  if (x + box.offsetWidth > window.innerWidth - 12) x = window.innerWidth - box.offsetWidth - 12;
  if (y + box.offsetHeight > window.innerHeight - 12) y = r.top - box.offsetHeight - 8;
  box.style.left = Math.max(12, Math.round(x)) + 'px';
  box.style.top = Math.max(12, Math.round(y)) + 'px';
}

document.addEventListener('mouseover', (ev) => {
  const t = ev.target.closest && ev.target.closest('[data-tip]');
  if (!t || t === tipTarget) return;
  clearTimeout(tipTimer);
  tipTarget = t;
  tipTimer = setTimeout(() => {
    if (tipTarget !== t) return;
    el('tip').textContent = t.dataset.tip;
    placeTip(t); // 先定位再显示, 避免在错位置闪一下
    el('tip').classList.add('show');
  }, 220);
});

document.addEventListener('mouseout', (ev) => {
  const t = ev.target.closest && ev.target.closest('[data-tip]');
  if (!t) return;
  if (ev.relatedTarget && t.contains(ev.relatedTarget)) return;
  if (tipTarget === t) hideTip();
});

window.addEventListener('scroll', hideTip, true);

/* ---------------------------------------------------------------- 详情面板 */
const SIZE = (n) =>
  n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? (n / 1024).toFixed(1) + ' KB' : n + ' B';

function kv(pairs) {
  const rows = pairs.filter(([, v]) => v).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`);
  return rows.length ? `<dl class="kv">${rows.join('')}</dl>` : '';
}

function stateGrid(states) {
  return `<div class="state-grid">${AGENT_ORDER.map((k) => {
    const st = states[k] || 'missing';
    return `<div class="state-cell"><span>${esc(nameOf(k))}</span>
      <span class="pill st-${st}">${esc((SKILL_LABEL[st] || MCP_LABEL[st] || st))}</span></div>`;
  }).join('')}</div>`;
}

function closeDetail() {
  el('detail').hidden = true;
  el('detailBody').innerHTML = '';
}

async function openSkill(name) {
  hideTip();
  const d = await call('skill_detail', name);
  el('detailTitle').textContent = d.display_name || d.name;
  el('detailSub').textContent = [d.name, d.version ? 'v' + d.version : '', d.path].filter(Boolean).join(' · ');

  const skip = new Set(['name', 'description', 'version']);
  const extra = Object.entries(d.frontmatter || {}).filter(([k, v]) => !skip.has(k) && v);
  const files = (d.files || [])
    .map((f) => `<span class="file-chip" title="${escAttr(SIZE(f.size))}">${esc(f.path)}</span>`)
    .join('');

  el('detailBody').innerHTML = `
    <div class="detail-sec">
      <p class="detail-desc">${esc(d.description || '这个 skill 没写 description，看下面正文开头。')}</p>
    </div>
    <div class="detail-sec">
      <h3>装在哪</h3>
      ${stateGrid(d.states)}
    </div>
    <div class="detail-sec">
      <h3>信息</h3>
      ${kv([
        ['体积', `${d.file_total} 个文件 · ${SIZE(d.size)}`],
        ...extra.map(([k, v]) => [k, v]),
        ['位置', d.path],
      ])}
    </div>
    <div class="detail-sec">
      <h3>目录内容 <span class="hint">${d.file_total} 个文件</span></h3>
      <div class="file-list">${files || '<span class="hint">空</span>'}</div>
    </div>
    <div class="detail-sec">
      <h3>SKILL.md <span class="hint">${d.body_truncated ? '只显示前 9000 字' : ''}</span></h3>
      <pre class="doc">${esc(d.body || '（没有正文）')}</pre>
    </div>`;
  el('detail').hidden = false;
}

async function openMcp(name) {
  hideTip();
  const d = await call('mcp_detail', name);
  el('detailTitle').textContent = d.name;
  el('detailSub').textContent = [
    d.disabled ? '已全局停用' : '',
    '写进 ' + d.targets.map(nameOf).join('、'),
  ]
    .filter(Boolean)
    .join(' · ');

  const cmd = [d.command, ...d.args].filter(Boolean).join(' ');
  const env = d.env.length
    ? `<div class="env-list">${d.env
        .map(
          (e) =>
            `<div class="env-item"><code class="env-key">${esc(e.key)}</code>
              <span class="env-val">${esc(e.value)}${e.hidden ? ' <span class="hint">已遮住</span>' : ''}</span></div>`
        )
        .join('')}</div>`
    : '<div class="hint">没有环境变量</div>';

  el('detailBody').innerHTML = `
    <div class="detail-sec">
      <h3>备注 <span class="hint">只存在真源里，不会写进各工具配置</span></h3>
      <textarea class="note-input" id="noteInput" placeholder="记一句它是干什么的，比如：连公司内网的 Postgres">${esc(
        d.note
      )}</textarea>
      <div class="note-foot"><button class="btn primary" data-savenote="${escAttr(d.name)}">保存备注</button></div>
    </div>
    <div class="detail-sec">
      <h3>启动命令</h3>
      <pre class="doc">${esc(cmd || d.url || '（没有 command）')}</pre>
    </div>
    <div class="detail-sec">
      <h3>环境变量 <span class="hint">${d.env.length} 个</span></h3>
      ${env}
    </div>
    <div class="detail-sec">
      <h3>各工具</h3>
      ${stateGrid(d.states)}
    </div>
    <div class="detail-sec">
      <h3>真源</h3>
      ${kv([['文件', d.manifest]])}
    </div>`;
  el('detail').hidden = false;
}

document.addEventListener('click', (ev) => {
  const t = ev.target.closest && ev.target.closest('[data-close],[data-skillinfo],[data-mcpinfo],[data-savenote]');
  if (!t) return;
  if (t.hasAttribute('data-close')) return closeDetail();
  if (t.dataset.skillinfo) return busy(null, () => openSkill(t.dataset.skillinfo).catch((e) => toast(e.message, true)));
  if (t.dataset.mcpinfo) return busy(null, () => openMcp(t.dataset.mcpinfo).catch((e) => toast(e.message, true)));
  if (t.dataset.savenote) {
    const input = el('noteInput');
    if (!input) return;
    return busy(t, async () => {
      const r = await call('mcp_set_note', t.dataset.savenote, input.value);
      toast(r.message);
      await refresh();
    });
  }
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !el('detail').hidden) closeDetail();
});

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
  ['overview', 'skills', 'mcp', 'logs', 'paths'].forEach((v) => (el('view-' + v).hidden = v !== view));

  const titles = {
    overview: ['总览', '四个工具的 Skill 与 MCP 是否一致'],
    skills: ['Skills', '同一份真源，靠目录链接分发到各工具'],
    mcp: ['MCP', '一处维护，按各家格式渲染写入'],
    logs: ['操作记录', '所有改动都可回溯，备份在 data/backups'],
    paths: ['路径设置', '真源与各工具配置在哪，探测不到时可以手动指路'],
  };
  el('pageTitle').textContent = titles[view][0];
  el('pageSub').textContent = titles[view][1];
  const hide = view === 'logs' || view === 'paths';
  el('btnSyncAll').style.display = hide ? 'none' : '';
  el('btnAutoSync').style.display = hide ? 'none' : '';

  if (view === 'paths') busy(null, () => renderPaths(true).catch((e) => toast(e.message, true)));
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

  el('skillNote').textContent = `共 ${sk.total} 个，真源 ${sk.sources_dir} · 点名字看详情`;

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
        const links = AGENT_ORDER.filter((k) => r.states[k] === 'linked').length;
        const copies = AGENT_ORDER.filter((k) => r.states[k] === 'copy').length;
        const delHint = [`从真源删除（备份到 data/backups/source）`];
        if (links) delHint.push(`拆掉 ${links} 处链接`);
        if (copies) delHint.push(`${copies} 处副本保留`);
        const alias =
          r.display_name && r.display_name !== r.name
            ? `<span class="skill-alias">${esc(r.display_name)}</span>`
            : '';
        return `
          <tr>
            <td>
              <button class="skill-name link-name" data-skillinfo="${escAttr(r.name)}"
                data-tip="${escAttr(clip(r.description) || '这个 skill 没写 description，点开看正文')}">${esc(r.name)}${alias}</button>
              ${r.description ? `<div class="skill-desc" title="${esc(r.description)}">${esc(r.description)}</div>` : ''}
            </td>
            ${cells}
            <td class="col-op">
              ${canFix ? `<button class="mini-btn" data-fixskill="${esc(r.name)}">改链接</button>` : ''}
              <button class="mini-btn" data-delskill="${esc(r.name)}" title="${esc(delHint.join('，'))}">删除</button>
            </td>
          </tr>`;
      })
      .join('');
  }

  // 未纳管: 按平台分组, 每个 skill 只在自己所在的平台下出现
  const panel = el('unmanagedPanel');
  if (sk.unmanaged.length) {
    panel.hidden = false;
    el('unmanagedGroups').innerHTML = groupByAgent(sk.unmanaged, sk.agents, (u, agentKey) =>
      `<span class="chip"><button class="link-name" data-skillinfo="${escAttr(u.name)}"
         data-tip="${escAttr(clip(u.description) || '这个 skill 没写 description，点开看正文')}">${esc(u.name)}</button>
         <button class="chip-btn" data-adopt="${esc(u.name)}" data-agent="${agentKey}" title="收进真源, 之后所有平台共享">收进真源</button>
         <button class="chip-btn" data-delunmanaged="${esc(u.name)}" title="从各工具清掉, 内容移入备份区">删除</button>
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

/* MCP 没有 SKILL.md 那样的自述，只能靠备注 + 命令行两样东西回忆 */
function mcpTip(r) {
  const cmd = [r.command, ...(r.args || [])].filter(Boolean).join(' ');
  const head = r.note || '还没有备注 · 点开写一句它是干什么的';
  return clip([head, cmd].filter(Boolean).join('\n'), 300);
}

function renderMcp() {
  const mc = STATE.mcp;
  if (!mc) return;
  el('mcpNote').textContent = `共 ${mc.total} 个，来自 ${mc.manifest} · 点格子切换启用，点名字看详情`;

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
        const written = AGENT_ORDER.filter((k) => ['synced', 'drift', 'disabled'].includes(r.states[k])).length;
        const delMcpHint = written
          ? `从真源和 ${written} 处工具配置里删掉，定义存档到 data/backups/deleted-mcp.json`
          : '从真源删掉，定义存档到 data/backups/deleted-mcp.json';
        return `
          <tr>
            <td>
              <button class="skill-name link-name" data-mcpinfo="${escAttr(r.name)}"
                data-tip="${escAttr(mcpTip(r))}">${esc(r.name)}</button>
              <div class="skill-path" title="${esc(r.command)}">${esc(cmd || r.command)} ${esc((r.args || []).slice(0, 2).join(' '))}</div>
            </td>
            ${cells}
            <td class="col-op">
              <button class="mini-btn" data-mcpglobal="${esc(r.name)}">${r.disabled ? '启用' : '停用'}</button>
              <button class="mini-btn" data-delmcp="${esc(r.name)}" title="${esc(delMcpHint)}">删除</button>
            </td>
          </tr>`;
      })
      .join('');
  }

  const panel = el('orphanPanel');
  if (mc.orphans.length) {
    panel.hidden = false;
    el('orphanGroups').innerHTML = groupByAgent(mc.orphans, mc.agents, (o, agentKey) =>
      `<span class="chip"><span class="chip-name" data-tip="${escAttr(clip(o.command, 300))}">${esc(o.name)}</span>
         <button class="chip-btn" data-adoptmcp="${esc(o.name)}" data-agents="${agentKey}" title="收进真源">收进真源</button>
         <button class="chip-btn" data-delmcporphan="${esc(o.name)}" title="从各工具配置里删掉, 不进真源">删除</button>
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

/* ---------------------------------------------------------------- 路径设置 */
let LOC = null;

function pathRow(o) {
  const chip = o.exists ? '<span class="pill st-linked">存在</span>' : '<span class="pill st-missing">不存在</span>';
  const extra = o.note ? ` · ${esc(o.note)}` : '';
  return `
    <tr>
      <td>
        <div class="skill-name">${esc(o.label)}</div>
        <div class="skill-path">默认 ${esc(o.def)}${extra}</div>
      </td>
      <td>
        <div class="path-cell">
          <input class="path-input" spellcheck="false" autocomplete="off"
            data-path-key="${escAttr(o.key)}" data-path-field="${escAttr(o.field)}"
            value="${escAttr(o.value)}" placeholder="${escAttr(o.def)}">
          <button class="mini-btn" data-pick="1" title="打开系统目录选择框">浏览</button>
        </div>
      </td>
      <td class="col-op">${chip}</td>
    </tr>`;
}

/* 空输入框 = 用默认值。只回填"确实被手改过"的那几项,
   否则一进页面每个框都填满绝对路径, 保存一次就全变成"手填"了。 */
async function renderPaths(force) {
  if (!LOC || force) LOC = await call('locations');
  el('pathNote').textContent = `主目录 ${LOC.home} · 配置文件 ${LOC.file}`;

  const rows = [
    pathRow({
      key: '__source',
      field: 'source',
      label: '真源 Skills 目录',
      value: LOC.source_overridden ? LOC.source : '',
      def: LOC.default_source,
      exists: LOC.source_exists,
    }),
  ];
  LOC.agents.forEach((a) => {
    rows.push(
      pathRow({
        key: a.key,
        field: 'skills_dir',
        label: a.name + ' · Skills',
        value: a.skills_overridden ? a.skills_dir : '',
        def: a.default_skills,
        exists: a.skills_exists,
        note: a.env_var && a.env_value ? `${a.env_var}=${a.env_value}` : '',
      })
    );
    rows.push(
      pathRow({
        key: a.key,
        field: 'mcp_path',
        label: a.name + ' · MCP 配置',
        value: a.mcp_overridden ? a.mcp_path : '',
        def: a.default_mcp,
        exists: a.mcp_exists,
      })
    );
  });
  el('pathBody').innerHTML = rows.join('');
}

el('btnPathSave').addEventListener('click', (e) =>
  busy(e.target, async () => {
    const payload = { source: '', agents: {} };
    document.querySelectorAll('[data-path-key]').forEach((inp) => {
      const key = inp.dataset.pathKey;
      const field = inp.dataset.pathField;
      const val = inp.value.trim();
      if (key === '__source') {
        payload.source = val;
        return;
      }
      payload.agents[key] = payload.agents[key] || {};
      payload.agents[key][field] = val;
    });
    const r = await call('save_locations', payload);
    await renderPaths(true);
    const banner = el('pathBanner');
    banner.hidden = false;
    banner.textContent = `${r.message}：已写入 ${r.file}。关掉这个窗口，重新双击「启动 SkillHub.bat」。`;
    toast('路径已保存，重启后生效');
  })
);

el('btnPathReset').addEventListener('click', () => {
  document.querySelectorAll('[data-path-key]').forEach((inp) => (inp.value = ''));
  toast('已清空，点「保存」后全部回到自动探测的默认值');
});

/* 自动检测: 现场重探环境变量与默认目录, 不重启也能看到现在装了哪些 */
el('btnPathDetect').addEventListener('click', (e) =>
  busy(e.target, async () => {
    const r = await call('detect_paths');
    LOC = null;
    await renderPaths(true);
    const hit = r.agents.filter((a) => a.detected).length;
    const miss = r.agents.filter((a) => !a.detected).map((a) => a.name);
    toast(
      miss.length
        ? `检测到 ${hit}/${r.agents.length} 个工具，没找到：${miss.join('、')}`
        : `四个工具全部检测到`
    );
  })
);

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
  const t = ev.target.closest(
    '[data-view],[data-skill],[data-mcp],[data-adopt],[data-adoptmcp],[data-fixskill],[data-mcpglobal],[data-openpath],[data-open],[data-delskill],[data-delunmanaged],[data-delmcp],[data-delmcporphan],[data-pick]'
  );
  if (!t) return;

  // 导航
  if (t.dataset.view) return setView(t.dataset.view);

  // 目录选择框
  if (t.dataset.pick) {
    const holder = t.parentElement;
    const inp = holder ? holder.querySelector('.path-input') : null;
    return busy(t, async () => {
      const r = await call('pick_dir', inp ? inp.value.trim() : '');
      if (r.path && inp) {
        inp.value = r.path;
        toast('已选择，点「保存」后生效');
      } else if (r.message) {
        toast(r.message, true);
      }
    });
  }

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

  /* 删除 skill（从真源）—— 真源整目录先进备份区，再拆各工具里的链接；
     副本默认保留，删完后它们会出现在「未纳管」里，想清再清。 */
  if (t.dataset.delskill) {
    const name = t.dataset.delskill;
    return armButton(t, '确认删除', () =>
      busy(t, async () => {
        const r = await call('delete_skill', name, []);
        toast(r.message, !r.ok);
        await refresh();
      })
    );
  }

  // 清理未纳管的 skill（副本移进备份区）
  if (t.dataset.delunmanaged) {
    return armButton(t, '确认清理', () =>
      busy(t, async () => {
        const r = await call('delete_unmanaged_skill', t.dataset.delunmanaged);
        toast(r.message, !r.ok);
        await refresh();
      })
    );
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

  /* 删除 MCP —— 真源摘掉 + 各工具配置里抹掉，定义存档在 data/backups/deleted-mcp.json */
  if (t.dataset.delmcp) {
    return armButton(t, '确认删除', () =>
      busy(t, async () => {
        const r = await call('mcp_remove', t.dataset.delmcp);
        toast(r.message, !r.ok);
        await refresh();
      })
    );
  }

  // 清理游离 MCP（不进真源，直接从各工具配置里删）
  if (t.dataset.delmcporphan) {
    return armButton(t, '确认清理', () =>
      busy(t, async () => {
        const r = await call('mcp_drop_orphan', t.dataset.delmcporphan);
        toast(r.message, !r.ok);
        await refresh();
      })
    );
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
