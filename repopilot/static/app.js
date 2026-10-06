/* Local-first UI. No model credentials in browser storage; no raw model HTML. */
'use strict';
const $ = id => document.getElementById(id);
const state = {
  workspaces: [],
  workspace: null,
  snapshot: null,
  settings: null,
  run: null,
  source: null,
  evidence: [],
  draft: '',
  fullAnswer: '',
  stream: null,
  historyOffset: 0,
  historyTotal: 0,
  follow: true,
  timeline: new Map(),
  clock: null,
  started: 0,
  proposal: null,
  publish: null,
  submitting: false,
  testing: false,
  capabilities: null,
  historyRequest: 0,
  renderVersion: 0,
  renderTimer: null,
  testId: null,
  testStream: null,
  session: ''
};
const labels = {
  retrieve: '源码检索',
  plan: '规划检索',
  tools: '执行只读工具',
  specialists: '分析与复核',
  researcher: '源码分析',
  reviewer: '独立复核',
  coder: '生成修改提案',
  generate: '生成回答',
  verify: '检查源码引用',
  proposal: '保存待审批提案',
  docker: 'Docker 隔离测试'
};
const statusNames = {
  queued: '排队中',
  running: '运行中',
  done: '已完成',
  error: '失败',
  cancelled: '已停止',
  interrupted: '已中断',
  awaiting_approval: '等待审批',
  not_applicable: '没有可检查的文件',
  pending: '待审批',
  applied: '已应用到副本',
  rejected: '已拒绝',
  passed: '通过',
  failed: '检查失败',
  timeout: '超时',
  infrastructure_error: '环境错误'
};

function el(tag, text = '', cls = '') {
  const n = document.createElement(tag);
  if (text !== null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}

function button(text, fn, cls = '') {
  const n = el('button', text, cls);
  n.type = 'button';
  n.addEventListener('click', () => Promise.resolve().then(fn).catch(fail));
  return n;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}

function toast(message, error = false) {
  $('toast').textContent = message;
  $('toast').className = 'toast' + (error ? ' error' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('toast').classList.add('hidden'), error ? 9000 : 4500);
}

function fail(e) {
  console.error(e?.name || 'Error');
  toast(e?.message || String(e), true);
}
async function api(path, options = {}) {
  const headers = {
    'X-RepoPilot-Client': 'browser',
    ...(options.headers || {})
  };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const r = await fetch('/api' + path, {
    ...options,
    headers
  });
  if (!r.ok) {
    let data;
    try {
      data = await r.json();
    } catch {
      data = {
        detail: `HTTP ${r.status}`
      };
    }
    throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  }
  if (r.status === 204) return null;
  return r.json();
}

function showDialog(id) {
  const d = $(id);
  if (!d.open) d.showModal();
}

function dateText(value) {
  if (!value) return '';
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function closeDialogs() {
  document.querySelectorAll('dialog[open]').forEach(d => d.close());
}

function tab(name) {
  document.querySelectorAll('.tabs button').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
    if (b.dataset.tab === name) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  document.querySelectorAll('.tab-view').forEach(v => v.classList.toggle('active', v.id === 'tab-' + name));
  if (name === 'changes') refreshProposals().catch(fail);
  if (name === 'tests') {
    refreshTests().catch(fail);
    refreshCapabilities().catch(fail);
  }
}

function busy(value) {
  state.submitting = value;
  $('send-question').disabled = value || !state.workspace;
  $('question-input').disabled = value;
  $('intent').disabled = value;
  $('stop-run').classList.toggle('hidden', !value || !state.run);
  updateComposer();
}

function scrollAnswer() {
  if (state.follow) $('conversation').scrollTop = $('conversation').scrollHeight;
  $('follow-output').classList.toggle('hidden', state.follow);
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制');
  } catch {
    toast('浏览器未允许剪贴板；可以使用导出。', true);
  }
}
async function download(path, name) {
  const r = await fetch('/api' + path);
  if (!r.ok) {
    let j = await r.json();
    throw new Error(j.detail || '导出失败');
  }
  const url = URL.createObjectURL(await r.blob());
  const a = el('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function renderAnswer(html, provisional = false) {
  RepoMarkdown.render($('answer'), html, {
    labels: state.evidence.map(e => e.label),
    onCitation: openEvidence,
    copy,
    provisional
  });
  scrollAnswer();
}
async function renderDraft() {
  const version = ++state.renderVersion;
  const id = state.run?.id;
  const data = await api('/markdown', {
    method: 'POST',
    body: {
      text: state.draft
    }
  });
  if (version === state.renderVersion && state.run?.id === id && !['done', 'error', 'cancelled'].includes(state.run?.status)) renderAnswer(data.html, true);
}

function scheduleRender() {
  if (state.renderTimer) return;
  state.renderTimer = setTimeout(() => {
    state.renderTimer = null;
    renderDraft().catch(fail);
  }, 200);
}

async function refreshSettings() {
  state.settings = await api('/settings');
  const ai = state.settings.mode === 'openai';
  $('mode-badge').textContent = ai ? 'AI 助手' : '本地检索';
  $('mode-badge').classList.toggle('live', ai);
  $('url-help').textContent = '支持的文档站点：' + state.settings.documentation_hosts.join('、');
  updateComposer();
  renderStarters();
  syncWorkspaceControls();
}
async function openSettings() {
  await refreshSettings();
  const form = $('settings-form');
  for (const field of form.elements) {
    if (!field.name) continue;
    const v = state.settings[field.name];
    if (field.type === 'password') {
      field.value = '';
      const note = form.querySelector(`[data-key="${field.name}"]`);
      if (note) note.textContent = state.settings[field.name + '_configured'] ? '已保存，留空保持不变' : '未配置';
    } else if (field.type === 'checkbox') field.checked = Boolean(v);
    else field.value = v ?? '';
  }
  $('settings-status').textContent = '';
  showDialog('settings-dialog');
}

function settingsPayload() {
  const data = {};
  for (const field of $('settings-form').elements) {
    if (!field.name) continue;
    if (field.type === 'password') {
      if (field.value) data[field.name] = field.value;
    } else if (field.type === 'checkbox') data[field.name] = field.checked;
    else if (field.type === 'number') data[field.name] = Number(field.value);
    else data[field.name] = field.value.trim();
  }
  return data;
}
async function saveSettings(event) {
  event.preventDefault();
  const save = $('settings-form').querySelector('button[type=submit]');
  save.disabled = true;
  try {
    await api('/settings', {
      method: 'PATCH',
      body: settingsPayload()
    });
    await refreshSettings();
    $('settings-status').textContent = '已保存，新任务将使用这些设置。';
    document.querySelectorAll('#settings-form input[type=password]').forEach(x => {
      x.value = '';
      const note = $('settings-form').querySelector(`[data-key="${x.name}"]`);
      if (note) note.textContent = state.settings[x.name + '_configured'] ? '已保存，留空保留' : '未配置';
    });
    toast('设置已保存');
  } catch (e) {
    $('settings-status').textContent = e.message;
    throw e;
  } finally {
    save.disabled = false;
  }
}
async function checkModel() {
  if (!$('settings-form').reportValidity()) return;
  $('check-model').disabled = true;
  $('settings-status').textContent = '正在测试当前填写的连接…';
  try {
    const r = await api('/settings/check-model', {
      method: 'POST',
      body: settingsPayload()
    });
    $('settings-status').textContent = `连接成功 · ${r.model} · ${(r.latency_ms / 1000).toFixed(1)} 秒。点击“保存设置”后用于新任务。`;
  } catch (e) {
    $('settings-status').textContent = e.message;
  } finally {
    $('check-model').disabled = false;
  }
}

async function refreshWorkspaces(preferred) {
  state.workspaces = await api('/workspaces');
  const pick = $('workspace-picker');
  pick.replaceChildren(new Option('选择工作区…', ''));
  for (const w of state.workspaces) pick.append(new Option(w.name, w.id));
  const id = preferred || state.workspace?.id || localStorage.getItem('studio-workspace');
  if (id && state.workspaces.some(w => w.id === id)) await selectWorkspace(id);
  else if (state.workspaces.length) await selectWorkspace(state.workspaces[0].id);
  else {
    state.workspace = null;
    state.snapshot = null;
    state.source = null;
    state.proposal = null;
    newQuestion();
    pick.value = '';
    $('empty-state').classList.remove('hidden');
    $('investigation').classList.add('hidden');
    $('workspace-title').textContent = '欢迎使用';
    $('workspace-meta').textContent = '导入仓库后查看文件';
    $('source-panel').classList.add('hidden');
    $('proposal-detail').classList.add('hidden');
    $('test-output').classList.add('hidden');
    $('test-summary').classList.add('hidden');
    $('change-count').textContent = '';
    $('test-version').replaceChildren();
    $('file-tree').replaceChildren(el('div', '导入后在这里浏览源码', 'side-empty'));
    $('recent-list').replaceChildren(el('div', '暂无任务记录', 'side-empty'));
  }
  syncWorkspaceControls();
}
async function selectWorkspace(id, reset = true) {
  state.workspace = await api('/workspaces/' + id);
  state.snapshot = state.workspace.snapshot;
  if (state.source && (state.source.workspace_id !== id || state.source.snapshot !== state.snapshot.id)) {
    state.source = null;
    $('source-panel').classList.add('hidden');
  }
  $('workspace-picker').value = id;
  localStorage.setItem('studio-workspace', id);
  state.session = localStorage.getItem('studio-session-' + id) || '';
  $('workspace-title').textContent = state.workspace.name;
  $('workspace-meta').textContent = `${state.snapshot.file_count} 个文件 · ${formatBytes(state.snapshot.bytes)}`;
  $('empty-state').classList.add('hidden');
  $('investigation').classList.remove('hidden');
  renderFiles();
  await refreshRecent();
  if (reset) {
    newQuestion();
    $('proposal-detail').classList.add('hidden');
    $('test-output').classList.add('hidden');
    $('test-summary').classList.add('hidden');
    $('change-count').textContent = '';
  }
  await refreshVersions();
  syncWorkspaceControls();
}

function renderFiles() {
  const query = $('file-filter').value.toLowerCase();
  $('file-tree').replaceChildren();
  for (const f of state.snapshot?.files || []) {
    if (!f.path.toLowerCase().includes(query)) continue;
    const b = button('', () => openFile(f.path), 'file-row');
    b.title = f.path;
    b.append(icon('file'), el('span', f.path));
    b.classList.toggle('selected', state.source?.path === f.path);
    $('file-tree').append(b);
  }
  if (!$('file-tree').children.length) $('file-tree').append(el('div', '没有匹配的文件', 'side-empty'));
}
async function refreshVersions() {
  if (!state.workspace) return;
  const rows = await api('/workspaces/' + state.workspace.id + '/versions');
  $('test-version').replaceChildren();
  for (const s of rows) {
    const label = `${s.id.slice(0,10)} · ${dateText(s.created_at)}${s.id===state.snapshot.id?' · 当前':''}`;
    $('test-version').append(new Option(label, s.id));
  }
  $('test-version').value = state.snapshot.id;
}

function newQuestion() {
  state.stream?.abort();
  state.stream = null;
  state.run = null;
  state.draft = '';
  state.fullAnswer = '';
  state.evidence = [];
  state.renderVersion++;
  clearInterval(state.clock);
  clearTimeout(state.renderTimer);
  state.renderTimer = null;
  state.timeline.clear();
  $('timeline').replaceChildren();
  $('timeline-status').textContent = '等待任务';
  $('elapsed').textContent = '—';
  $('current-question').textContent = state.workspace ? '准备调查 ' + state.workspace.name : '仓库已就绪';
  $('run-subtitle').textContent = `${state.snapshot?.file_count||0} 个文件 · 可浏览源码、检索或创建编辑提案`;
  $('answer').replaceChildren(el('p', '输入问题开始，或打开文件查看和编辑。', 'muted'));
  $('answer-actions').classList.add('hidden');
  $('draft-warning').classList.add('hidden');
  $('answer-warning').classList.add('hidden');
  $('question-input').value = '';
  state.session = '';
  if (state.workspace) localStorage.removeItem('studio-session-' + state.workspace.id);
  localStorage.removeItem('studio-active-run');
  renderEvidence();
  busy(false);
  renderStarters();
  $('task-starters').classList.toggle('hidden', !state.workspace);
  $('timeline-box').classList.add('hidden');
  $('answer').classList.add('hidden');
  tab('investigate');
}

function openImport(kind = 'github') {
  document.querySelectorAll('.segmented button').forEach(b => b.classList.toggle('active', b.dataset.import === kind));
  document.querySelectorAll('.import-method').forEach(x => x.classList.toggle('hidden', x.id !== 'import-' + kind));
  $('import-status').textContent = '';
  showDialog('import-dialog');
}
async function importTask(fn) {
  $('import-status').textContent = '正在导入源码并建立文件索引…';
  $('import-dialog').querySelectorAll('button,input').forEach(x => x.disabled = true);
  try {
    const w = await fn();
    $('import-dialog').close();
    await refreshWorkspaces(w.id);
    const skipped = Object.values(w.skipped || {}).reduce((a, b) => a + b, 0);
    toast(`已导入 ${w.name}${skipped?' · 跳过 '+skipped+' 个文件':''}`);
  } catch (e) {
    $('import-status').textContent = e.message;
    throw e;
  } finally {
    $('import-dialog').querySelectorAll('button,input').forEach(x => x.disabled = false);
    $('folder-input').value = '';
    $('zip-input').value = '';
  }
}

function clientAllowed(path) {
  return !path.split('/').some(x => ['node_modules', '.git', '.venv', 'venv', '__pycache__', 'dist', 'build', '.repopilot'].includes(x)) && !/(^|\/)\.env($|\.)|\.(pem|key|p12|pfx)$/i.test(path);
}
async function importFolder() {
  const all = [...$('folder-input').files];
  if (!all.length) return;
  const name = (all[0].webkitRelativePath || all[0].name).split('/')[0];
  const selected = all.map(f => [f, (f.webkitRelativePath || f.name).split('/').slice(1).join('/') || f.name]).filter(([f, p]) => clientAllowed(p) && f.size <= 1000000);
  if (!selected.length) throw new Error('没有可导入文件');
  if (selected.length > 3000 || selected.reduce((a, [f]) => a + f.size, 0) > 64000000) throw new Error('目录过大，请选择更小的子项目');
  const data = new FormData();
  data.append('name', name);
  for (const [f, p] of selected) data.append('files', f, p);
  await importTask(() => api('/workspaces/folder', {
    method: 'POST',
    body: data
  }));
}
async function importZip() {
  const f = $('zip-input').files[0];
  if (!f) return;
  if (f.size > 32000000) throw new Error('ZIP 最大 32 MB');
  const data = new FormData();
  data.append('name', f.name.replace(/\.zip$/i, ''));
  data.append('archive', f);
  await importTask(() => api('/workspaces/zip', {
    method: 'POST',
    body: data
  }));
}

function timeline(node, status, message = '', duration) {
  const toolLabels = {search:'搜索源码',read_file:'阅读文件',symbols:'查找符号',read_issue:'阅读 Issue',read_docs:'阅读文档'};
  const title = labels[node] || toolLabels[node.replace(/^tool:/, '')] ||
    (node.startsWith('retry:') ? '重试请求' : node.startsWith('fallback:') ? '调整检索或模型服务' : node.startsWith('warning:') ? '任务提示' : node);
  let row = state.timeline.get(node);
  if (!row || status === 'running' && row.dataset.status === 'done') {
    row = el('div', null, 'step');
    row.dataset.status = status;
    row.append(el('span', status === 'done' ? '✓' : '·', 'step-bullet'));
    const body = el('div');
    body.append(el('strong', title), el('small', message === node ? '' : message));
    row.append(body, el('time', ''));
    $('timeline').append(row);
    state.timeline.set(node, row);
  }
  row.dataset.status = status;
  row.querySelector('.step-bullet').textContent = status === 'done' ? '✓' : status === 'error' ? '!' : '·';
  if (message && message !== node) row.querySelector('small').textContent = message;
  if (duration !== undefined) row.querySelector('time').textContent = (duration / 1000).toFixed(1) + ' s';
  $('timeline-status').textContent = status === 'running' ? title : '阶段已更新';
  scrollAnswer();
}
async function consumeEvents(id, handler, signal) {
  let last = 0;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const r = await fetch(`/api/events/${id}?after=${last}`, {
        signal,
        headers: {
          'X-RepoPilot-Client': 'browser'
        }
      });
      if (!r.ok) throw new Error('事件流 HTTP ' + r.status);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {
          value,
          done
        } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {
          stream: true
        }).replace(/\r\n/g, '\n');
        let pos;
        while ((pos = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, pos);
          buffer = buffer.slice(pos + 2);
          let name = 'message',
            data = [],
            seq = last;
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) name = line.slice(6).trim();
            else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
            else if (line.startsWith('id:')) seq = Number(line.slice(3));
          }
          if (data.length) {
            last = seq;
            await handler(name, JSON.parse(data.join('\n')));
          }
        }
      }
      return;
    } catch (e) {
      if (e.name === 'AbortError' || signal?.aborted) return;
      if (attempt === 4) throw e;
      toast('连接中断，正在重新连接到已有任务…');
      await new Promise(r => setTimeout(r, 700 * (attempt + 1)));
    }
  }
}
async function startInvestigation(event) {
  event.preventDefault();
  if (state.submitting) return;
  if (!state.workspace) {
    openImport();
    return;
  }
  const question = $('question-input').value.trim();
  if (!question) return;
  const urls = $('external-urls').value.split('\n').map(x => x.trim()).filter(Boolean);
  if (urls.length > 4) throw new Error('最多提供 4 个资料 URL');
  const intent = $('intent').value;
  const session = state.session;
  state.stream?.abort();
  state.run = null;
  state.timeline.clear();
  $('timeline').replaceChildren();
  state.draft = '';
  state.fullAnswer = '';
  state.evidence = [];
  state.follow = true;
  state.renderVersion++;
  $('answer').replaceChildren();
  $('answer-actions').classList.add('hidden');
  $('answer-warning').classList.add('hidden');
  $('draft-warning').classList.add('hidden');
  $('current-question').textContent = question;
  $('task-starters').classList.add('hidden');
  $('timeline-box').classList.remove('hidden');
  $('answer').classList.remove('hidden');
  $('timeline-box').open = true;
  $('timeline-status').textContent = '正在提交任务';
  busy(true);
  try {
    const r = await api('/runs', {
      method: 'POST',
      body: {
        workspace_id: state.workspace.id,
        question,
        intent,
        session_id: session,
        urls
      }
    });
    state.run = r;
    busy(true);
    state.session = r.session_id;
    localStorage.setItem('studio-session-' + state.workspace.id, r.session_id);
    localStorage.setItem('studio-active-run', r.id);
    $('question-input').value = '';
    $('run-subtitle').textContent = `版本 ${r.snapshot_id.slice(0,8)} · ${dateText(r.created_at)}`;
    await watchRun(r.id);
  } catch (e) {
    busy(false);
    throw e;
  }
}
async function watchRun(id) {
  state.stream?.abort();
  state.stream = new AbortController();
  state.started = performance.now();
  clearInterval(state.clock);
  state.clock = setInterval(() => {
    $('elapsed').textContent = ((performance.now() - state.started) / 1000).toFixed(1) + ' s';
  }, 250);
  const stream = state.stream;
  try {
    await consumeEvents(id, async (event, data) => {
      if (state.run?.id !== id) return;
      if (event === 'stage') timeline(data.node, data.status, data.message || '', data.duration_ms);
      else if (event === 'plan') {
        timeline('plan', 'running', `第 ${data.round} 轮 · ${data.reason||''}`);
      } else if (event === 'tool_started') {
        timeline('tool:' + data.tool, 'running', data.args.path || data.args.query || data.args.url || '');
      } else if (event === 'tool_finished') {
        timeline('tool:' + data.action.tool, data.ok ? 'done' : 'error', data.ok ? `新增 ${data.added} 个证据片段` : data.error);
      } else if (event === 'agent_started') timeline(data.agent, 'running', '独立上下文分支');
      else if (event === 'agent_finished') timeline(data.agent, 'done', `报告完成 · ${data.chars} 字符`);
      else if (event === 'evidence') {
        state.evidence = data.items;
        renderEvidence();
      } else if (event === 'answer_delta') {
        state.draft += data.text;
        $('draft-warning').classList.remove('hidden');
        scheduleRender();
      } else if (event === 'answer_reset') {
        state.draft = '';
        state.renderVersion++;
        $('answer').replaceChildren();
        $('draft-warning').classList.add('hidden');
      } else if (event === 'retry' || event === 'fallback' || event === 'warning') {
        timeline(event + ':' + (data.role || ''), 'done', data.message || (event === 'retry' ? '正在重试模型请求' : '切换到备用模型服务'));
      } else if (event === 'approval_required') {
        toast('修改提案已生成，等待你审查和批准。');
      } else if (event === 'error') toast(data.message, true);
    }, stream.signal);
    if (state.run?.id === id && !stream.signal.aborted) await loadRun(id, false);
  } finally {
    if (state.run?.id === id) {
      clearInterval(state.clock);
      busy(false);
    }
  }
}
async function loadRun(id, watch = true) {
  const differentRun = state.run?.id !== id;
  state.stream?.abort();
  const r = await api('/runs/' + id);
  if (!state.workspace || state.workspace.id !== r.workspace_id) {
    await selectWorkspace(r.workspace_id, false);
  }
  state.run = r;
  if (differentRun) {
    state.timeline.clear();
    $('timeline').replaceChildren();
    const trace = await api('/runs/' + id + '/export?format=json');
    for (const ev of trace.events || []) {
      if (ev.event === 'stage') timeline(ev.data.node, ev.data.status, ev.data.message || '', ev.data.duration_ms);
    }
  }
  state.session = r.session_id;
  localStorage.setItem('studio-session-' + r.workspace_id, r.session_id);
  localStorage.setItem('studio-active-run', r.id);
  state.renderVersion++;
  state.evidence = r.evidence;
  state.fullAnswer = r.answer || '';
  $('empty-state').classList.add('hidden');
  $('investigation').classList.remove('hidden');
  $('current-question').textContent = r.question;
  $('run-subtitle').textContent = `版本 ${r.snapshot_id.slice(0,8)} · ${dateText(r.created_at)} · ${statusNames[r.status]||r.status}`;
  $('draft-warning').classList.add('hidden');
  renderEvidence();
  renderAnswer(r.answer_html || '');
  $('task-starters').classList.add('hidden');
  $('timeline-box').classList.remove('hidden');
  $('answer').classList.remove('hidden');
  $('answer-actions').classList.toggle('hidden', !r.answer && !['error', 'interrupted'].includes(r.status));
  $('copy-answer').disabled = !r.answer;
  $('export-answer').disabled = !r.answer;
  $('answer-warning').textContent = r.error || (r.warnings || []).join(' ');
  $('answer-warning').classList.toggle('hidden', !$('answer-warning').textContent);
  $('show-proposal').classList.toggle('hidden', !r.proposal_id);
  $('resume-run').classList.toggle('hidden', !['error', 'interrupted'].includes(r.status));
  $('timeline-status').textContent = statusNames[r.status] || r.status;
  const m = r.metrics || {};
  $('run-metrics').textContent = [`运行引擎：${r.runtime}`, m.model_requests !== undefined ? `模型请求：${m.model_requests}` : '', m.input_tokens !== null && m.input_tokens !== undefined ? `输入 / 输出 tokens：${m.input_tokens} / ${m.output_tokens}${m.usage_complete?'':'（部分用量）'}` : '', m.estimated_cost !== null && m.estimated_cost !== undefined ? `费用估算：${m.estimated_cost}` : '', r.notice || ''].filter(Boolean).join(' · ');
  if (r.duration_ms) $('elapsed').textContent = (r.duration_ms / 1000).toFixed(1) + ' s';
  $('timeline-box').open = ['queued', 'running', 'awaiting_approval', 'error', 'interrupted'].includes(r.status);
  busy(['running', 'queued'].includes(r.status));
  tab('investigate');
  await refreshRecent();
  if (watch && ['running', 'queued'].includes(r.status)) await watchRun(id);
}

function renderEvidence() {
  $('evidence-count').textContent = state.evidence.length;
  $('evidence-list').replaceChildren();
  for (const e of state.evidence) {
    const b = button('', () => openEvidence(e.label), 'evidence-card');
    const head = el('div');
    head.append(el('span', e.label, 'evidence-label'), el('strong', e.path));
    b.append(head, el('small', `L${e.start_line||1}–L${e.end_line||1} · ${e.source||e.retrieval||'源码'}`));
    $('evidence-list').append(b);
  }
  if (!state.evidence.length) {
    const d = el('div', null, 'evidence-empty');
    d.append(icon('code'), el('strong', '查看相关源码'), el('p', '打开文件，或选择回答中的证据编号。'));
    $('evidence-list').append(d);
  }
}
async function openEvidence(label) {
  const e = state.evidence.find(x => x.label === label);
  if (!e) return;
  if (e.url) {
    state.source = null;
    showSource(e.path, e.content, 1, e.end_line || 1, '外部资料 · ' + e.source, false);
  } else await openFile(e.path, e.snapshot_id, e.start_line, e.end_line);
}

function showSource(path, content, start = 0, end = 0, meta = '', editable = true) {
  $('source-panel').classList.remove('hidden');
  $('source-path').textContent = path;
  $('source-meta').textContent = meta;
  $('edit-source').disabled = !editable;
  $('delete-source').disabled = !editable;
  $('source-code').replaceChildren();
  const lines = content.split(/\r\n|\n|\r/);
  for (let i = 0; i < Math.min(lines.length, 20000); i++) {
    const n = el('span', null, 'source-line');
    if (i + 1 >= start && i + 1 <= end) n.classList.add('marked');
    n.append(el('span', String(i + 1), 'line-no'), document.createTextNode(lines[i]));
    $('source-code').append(n);
  }
  openPanel('evidence');
  const marked = $('source-code').querySelector('.marked');
  if (marked) $('source-code').scrollTop = Math.max(0, marked.offsetTop - $('source-code').offsetTop - 30);
}
async function openFile(path, snapshot = state.snapshot?.id, start = 0, end = 0) {
  if (!state.workspace) return;
  const f = await api(`/workspaces/${state.workspace.id}/file?path=${encodeURIComponent(path)}&snapshot=${snapshot}`);
  state.source = {
    ...f,
    snapshot,
    workspace_id: state.workspace.id
  };
  showSource(f.path, f.content, start, end, `${snapshot.slice(0,10)} · ${f.lines} 行 · ${f.hash.slice(0,10)}`, snapshot === state.snapshot.id);
  renderFiles();
}

function editor(create = false) {
  if (!state.workspace) {
    openImport();
    return;
  }
  if (!create && !state.source) throw new Error('请先打开源码文件');
  $('editor-path').value = create ? '' : state.source.path;
  $('editor-path').readOnly = !create;
  $('editor-title').value = create ? '' : `修改 ${state.source.path}`;
  $('editor-text').value = create ? '' : state.source.content;
  $('editor-base').textContent = '基线 ' + state.snapshot.id.slice(0, 12);
  $('editor-form').dataset.create = String(create);
  showDialog('editor-dialog');
}
async function submitEditor(event) {
  event.preventDefault();
  const create = $('editor-form').dataset.create === 'true';
  if (!create && state.source.snapshot !== state.snapshot.id) throw new Error('文件不是当前版本，请重新打开');
  const edit = {
    path: $('editor-path').value,
    operation: create ? 'create' : 'replace_file',
    new_text: $('editor-text').value,
    expected_hash: create ? '' : state.source.hash
  };
  const p = await api('/proposals', {
    method: 'POST',
    body: {
      workspace_id: state.workspace.id,
      snapshot_id: state.snapshot.id,
      title: $('editor-title').value,
      edits: [edit]
    }
  });
  $('editor-dialog').close();
  tab('changes');
  await openProposal(p.id);
  await refreshProposals();
  toast('提案已生成，请查看差异。');
}
async function deleteSource() {
  if (!state.source) throw new Error('请先选择文件');
  if (!confirm(`创建删除 ${state.source.path} 的提案？此操作还不会应用删除。`)) return;
  const p = await api('/proposals', {
    method: 'POST',
    body: {
      workspace_id: state.workspace.id,
      snapshot_id: state.snapshot.id,
      title: '删除 ' + state.source.path,
      edits: [{
        path: state.source.path,
        operation: 'delete',
        expected_hash: state.source.hash
      }]
    }
  });
  tab('changes');
  await openProposal(p.id);
}

async function refreshProposals() {
  const list = $('proposals-list');
  list.replaceChildren();
  if (!state.workspace) {
    list.append(el('div', '请先导入工作区', 'empty-hint'));
    return;
  }
  const rows = await api('/proposals?workspace_id=' + state.workspace.id);
  $('change-count').textContent = rows.filter(p => p.status === 'pending').length || '';
  for (const p of rows) {
    const card = el('div', null, 'list-card');
    const text = el('div');
    text.append(el('strong', p.title), el('small', `${dateText(p.created_at)} · ${p.files.length} 个文件 · ${statusNames[p.status]||p.status}`));
    card.append(text, button('查看差异', () => openProposal(p.id), 'secondary'));
    list.append(card);
  }
  if (!rows.length) list.append(el('div', '暂无修改提案。打开文件进行编辑，或连接模型后选择“提出修改”。', 'empty-hint'));
}

function diffNode(text) {
  const pre = el('pre', null, 'diff');
  for (const line of text.split('\n')) pre.append(el('span', line + '\n', line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-remove' : 'diff-context'));
  return pre;
}
async function openProposal(id) {
  const p = await api('/proposals/' + id);
  state.proposal = p;
  const box = $('proposal-detail');
  box.classList.remove('hidden');
  box.replaceChildren();
  box.append(el('span', statusNames[p.status] || p.status, 'status-pill'), el('h2', p.spec.title), el('p', p.spec.summary, 'muted'), el('p', `基线 ${p.snapshot_id.slice(0,12)} · 摘要 ${p.digest.slice(0,16)}`, 'muted'));
  const check = p.static_check;
  box.append(el('div', `语法检查：${check.status==='passed'?'通过':check.status==='not_applicable'?'没有适用的文件':'发现问题'} · ${check.checked} 个文件`, 'check-summary'));
  for (const issue of check.problems || []) box.append(el('p', `${issue.path}:${issue.line||'?'} ${issue.message}`, 'danger-text'));
  box.append(diffNode(p.diff));
  const actions = el('div', null, 'proposal-actions');
  if (p.status === 'pending') {
    const label = el('label', null, 'check');
    const consent = document.createElement('input');
    consent.type = 'checkbox';
    label.append(consent, document.createTextNode('确认此差异，应用到工作副本'));
    box.append(label);
    const approve = button('批准并应用', async () => {
      approve.disabled = true;
      try {
        await api(`/proposals/${id}/decision`, {
          method: 'POST',
          body: {
            approved: true,
            digest: p.digest
          }
        });
        await selectWorkspace(p.workspace_id, false);
        await refreshProposals();
        await openProposal(id);
        toast('已保存为新版本');
      } finally {
        approve.disabled = !consent.checked;
      }
    }, 'primary');
    approve.disabled = true;
    consent.addEventListener('change', () => approve.disabled = !consent.checked);
    actions.append(approve, button('拒绝', async () => {
      await api(`/proposals/${id}/decision`, {
        method: 'POST',
        body: {
          approved: false,
          digest: p.digest
        }
      });
      await refreshProposals();
      await openProposal(id);
    }, 'danger'));
  } else if (p.status === 'applied') {
    actions.append(button('测试这个版本', () => {
      tab('tests');
      $('test-version').value = p.applied_snapshot;
    }, 'primary'), button('生成撤销提案', async () => {
      const reversed = await api(`/proposals/${id}/revert`, {
        method: 'POST'
      });
      await refreshProposals();
      await openProposal(reversed.id);
    }, 'secondary'));
    if (state.workspace?.origin.type === 'github') actions.append(button('创建草稿 PR', () => publishPreview(id), 'secondary'));
  }
  actions.append(button('导出 Patch', () => download(`/proposals/${id}/patch`, 'changes.patch')));
  box.append(actions);
  if (p.published) {
    const a = el('a', '打开已创建的 PR');
    a.href = p.published.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    box.append(el('p', '已发布草稿 PR'), a);
  }
  box.scrollIntoView({
    block: 'start',
    behavior: 'smooth'
  });
}
async function publishPreview(id) {
  const p = await api(`/proposals/${id}/publish-preview`);
  state.publish = p;
  $('publish-info').replaceChildren(el('h3', p.repository), el('p', `基线：${p.base} @ ${p.base_commit.slice(0,12)}`), el('p', `分支：${p.branch}`), el('pre', p.files.join('\n')), el('div', p.notice, 'notice'));
  $('publish-consent').checked = false;
  $('publish-confirm').disabled = true;
  $('publish-status').textContent = '';
  showDialog('publish-dialog');
}
async function publishConfirm() {
  const p = state.publish;
  if (!p || !$('publish-consent').checked) return;
  $('publish-confirm').disabled = true;
  $('publish-status').textContent = '正在创建远端分支与草稿 PR，请勿重复提交…';
  try {
    const result = await api(`/proposals/${p.proposal_id}/publish`, {
      method: 'POST',
      body: {
        fingerprint: p.fingerprint,
        confirmation: 'CREATE_DRAFT_PR'
      }
    });
    $('publish-status').replaceChildren(document.createTextNode('已创建：'));
    const a = el('a', result.url);
    a.href = result.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    $('publish-status').append(a);
    await openProposal(p.proposal_id);
  } catch (e) {
    $('publish-status').textContent = e.message + ' 请先核对远端是否已有分支或 PR，再重试。';
    throw e;
  } finally {
    $('publish-confirm').disabled = !$('publish-consent').checked;
  }
}

async function refreshCapabilities() {
  state.capabilities = await api('/capabilities');
  const s = state.capabilities.sandbox;
  $('docker-status').textContent = !state.settings.tests_enabled ? '容器测试未启用，请先在设置中开启。' :
    s.docker && s.image ? 'Docker 和测试镜像已就绪。' : s.reason;
  updateTestProfile();
}

function updateTestProfile() {
  const dynamic = $('test-profile').value !== 'static';
  $('test-consent-label').classList.toggle('hidden', !dynamic);
  $('docker-guidance').classList.toggle('hidden', !dynamic);
  $('test-consent').disabled = !dynamic;
  $('test-help').textContent = dynamic ? '运行仓库代码，测试依赖需预装到所选镜像中。' : '解析 Python / JSON / TOML 的语法，不运行项目代码。';
  $('start-test').textContent = dynamic ? '运行测试' : '检查语法';
  const s = state.capabilities?.sandbox;
  const ready = !dynamic || Boolean(state.settings?.tests_enabled && s?.docker && s?.image && $('test-consent').checked);
  $('start-test').disabled = state.testing || !state.workspace || !ready;
}
async function refreshTests() {
  const box = $('test-list');
  box.replaceChildren();
  if (!state.workspace) {
    box.append(el('div', '请先导入工作区', 'empty-hint'));
    return;
  }
  const rows = await api('/tests?workspace_id=' + state.workspace.id);
  for (const r of rows) {
    const card = el('div', null, 'list-card');
    const content = el('div');
    const result = r.result;
    const status = testTitle(r);
    content.append(el('strong', status), el('small', `${r.profile} · ${r.snapshot_id.slice(0,10)} · ${dateText(r.created_at)}`));
    card.append(content, button('查看输出', () => showTest(r.id), 'secondary'));
    box.append(card);
  }
  if (!rows.length) box.append(el('div', '尚未运行检查。先静态检查，再在 Docker 环境运行测试。', 'empty-hint'));
}

function testTitle(r) {
  const kind = r.profile === 'static' ? '语法检查' : '容器测试';
  return `${kind} · ${statusNames[r.result?.status || r.status] || r.status}`;
}
async function showTest(id) {
  const r = await api('/tests/' + id);
  const box = $('test-summary');
  box.classList.remove('hidden');
  box.replaceChildren(el('strong', testTitle(r)));
  box.classList.toggle('failed', ['failed', 'timeout', 'infrastructure_error', 'error'].includes(r.result?.status || r.status));
  if (r.result) {
    const result = r.result;
    const detail = r.profile === 'static' ? `检查 ${result.checked} 个文件；这一步未运行测试代码。` :
      `耗时 ${((result.duration_ms || 0) / 1000).toFixed(1)} 秒${result.exit_code !== undefined ? ' · 退出码 ' + result.exit_code : ''}`;
    box.append(el('p', detail));
    const lines = (result.problems || []).map(p => `${p.path}:${p.line || '?'} ${p.message}`);
    $('test-output').textContent = [result.output || '', ...lines].filter(Boolean).join('\n') ||
      (result.status === 'passed' ? '检查完成，未发现问题。' : result.notice || '没有更多输出。');
  } else {
    box.append(el('p', r.error || '任务尚未完成'));
    $('test-output').textContent = r.error || '等待结果…';
  }
  $('test-output').classList.remove('hidden');
}
async function startTest() {
  if (state.testing) return;
  if (!state.workspace) throw new Error('请先导入仓库');
  const profile = $('test-profile').value;
  if (profile !== 'static' && !$('test-consent').checked) throw new Error('动态测试需要明确确认');
  state.testing = true;
  updateTestProfile();
  $('test-summary').classList.add('hidden');
  try {
    const r = await api('/tests', {
      method: 'POST',
      body: {
        workspace_id: state.workspace.id,
        snapshot_id: $('test-version').value,
        profile,
        approved: $('test-consent').checked
      }
    });
    state.testId = r.id;
    state.testStream?.abort();
    state.testStream = new AbortController();
    $('test-output').classList.remove('hidden');
    $('test-output').textContent = '测试任务已提交…\n';
    $('start-test').disabled = true;
    $('cancel-test').classList.remove('hidden');
    try {
      await consumeEvents(r.id, async (name, data) => {
        if (name === 'test_log') $('test-output').textContent = ($('test-output').textContent + data.text).slice(-150000);
        if (name === 'error') toast(data.message, true);
      }, state.testStream.signal);
      await showTest(r.id);
      await refreshTests();
    } finally {
      $('cancel-test').classList.add('hidden');
    }
  } finally {
    state.testing = false;
    updateTestProfile();
  }
}
async function refreshRecent() {
  if (!state.workspace) return;
  const res = await api('/runs?workspace_id=' + state.workspace.id + '&limit=3');
  $('recent-list').replaceChildren();
  for (const r of res.items) {
    const b = button('', () => loadRun(r.id), 'recent-item');
    b.append(el('strong', r.question), el('small', `${dateText(r.created_at)} · ${statusNames[r.status]||r.status}`));
    $('recent-list').append(b);
  }
  if (!res.items.length) $('recent-list').append(el('div', '还没有调查记录', 'side-empty'));
}
async function openHistory() {
  state.historyOffset = 0;
  showDialog('history-dialog');
  await searchHistory(false);
}
async function searchHistory(append) {
  if (!append) {
    state.historyOffset = 0;
    $('history-results').replaceChildren();
  }
  const request = ++state.historyRequest;
  const q = $('history-query').value;
  const res = await api(`/runs?q=${encodeURIComponent(q)}&offset=${state.historyOffset}&limit=30`);
  if (request !== state.historyRequest) return;
  state.historyTotal = res.total;
  $('history-total').textContent = `共 ${res.total} 条调查 · 所有工作区`;
  let day = '';
  for (const r of res.items) {
    const d = new Date(r.created_at).toLocaleDateString('zh-CN');
    if (d !== day) {
      $('history-results').append(el('div', d, 'history-day'));
      day = d;
    }
    const w = state.workspaces.find(w => w.id === r.workspace_id);
    const b = button('', async () => {
      $('history-dialog').close();
      await loadRun(r.id);
    }, 'history-item');
    b.append(el('strong', r.question), el('small', `${w?.name||'工作区'} · ${dateText(r.created_at)} · ${statusNames[r.status]||r.status}`));
    $('history-results').append(b);
  }
  state.historyOffset += res.items.length;
  $('history-more').classList.toggle('hidden', state.historyOffset >= res.total);
  if (!res.total) $('history-results').append(el('div', '没有匹配的分析记录', 'empty-hint'));
}

// Wiring only: actual data and progress are supplied by the backend.
document.querySelectorAll('.close-dialog').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
document.querySelectorAll('[data-import]').forEach(b => b.addEventListener('click', () => openImport(b.dataset.import)));
document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => tab(b.dataset.tab)));
for (const id of ['settings-open', 'top-settings', 'empty-settings', 'starter-settings']) $(id).addEventListener('click', () => openSettings().catch(fail));
for (const id of ['history-open', 'top-history']) $(id).addEventListener('click', () => openHistory().catch(fail));
for (const id of ['import-open', 'mobile-import']) $(id).addEventListener('click', () => openImport());
$('workspace-picker').addEventListener('change', e => {
  if (e.target.value) selectWorkspace(e.target.value).catch(fail);
});
$('file-filter').addEventListener('input', renderFiles);
$('new-question').addEventListener('click', newQuestion);
$('ask-form').addEventListener('submit', e => startInvestigation(e).catch(fail));
$('question-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $('ask-form').requestSubmit();
  }
});
$('toggle-urls').addEventListener('click', () => {
  const hidden = $('urls-wrap').classList.toggle('hidden');
  $('toggle-urls').setAttribute('aria-expanded', String(!hidden));
  if (!hidden) $('external-urls').focus();
});
$('settings-form').addEventListener('submit', e => saveSettings(e).catch(fail));
$('import-github-button').addEventListener('click', () => importTask(() => api('/workspaces/github', {
  method: 'POST',
  body: {
    url: $('github-url').value.trim(),
    ref: $('github-ref').value.trim()
  }
})).catch(fail));
$('folder-input').addEventListener('change', () => importFolder().catch(fail));
$('zip-input').addEventListener('change', () => importZip().catch(fail));
$('check-model').addEventListener('click', () => checkModel().catch(fail));
$('clear-keys').addEventListener('click', async () => {
  if (!confirm('清除后端保存的所有 API Key 和 GitHub Token？')) return;
  try {
    await api('/settings', {
      method: 'PATCH',
      body: {
        api_key: '',
        backup_key: '',
        embedding_key: '',
        github_token: ''
      }
    });
    await openSettings();
    toast('已清除密钥');
  } catch (e) {
    fail(e);
  }
});
$('edit-source').addEventListener('click', () => {
  try {
    editor(false);
  } catch (e) {
    fail(e);
  }
});
$('new-file').addEventListener('click', () => editor(true));
$('editor-form').addEventListener('submit', e => submitEditor(e).catch(fail));
$('delete-source').addEventListener('click', () => deleteSource().catch(fail));
$('publish-consent').addEventListener('change', () => $('publish-confirm').disabled = !$('publish-consent').checked);
$('publish-confirm').addEventListener('click', () => publishConfirm().catch(fail));
$('start-test').addEventListener('click', () => startTest().catch(fail));
$('refresh-docker').addEventListener('click', () => refreshCapabilities().catch(fail));
$('cancel-test').addEventListener('click', () => {
  if (state.testId) api('/tests/' + state.testId + '/cancel', {
    method: 'POST'
  }).catch(fail);
});
$('test-profile').addEventListener('change', () => {
  $('test-consent').checked = false;
  updateTestProfile();
});
$('test-consent').addEventListener('change', updateTestProfile);
$('stop-run').addEventListener('click', () => {
  if (state.run) api('/runs/' + state.run.id + '/cancel', {
    method: 'POST'
  }).catch(fail);
});
$('resume-run').addEventListener('click', async () => {
  if (!state.run) return;
  try {
    await api('/runs/' + state.run.id + '/resume', {
      method: 'POST'
    });
    state.run.status = 'queued';
    busy(true);
    await watchRun(state.run.id);
  } catch (e) {
    fail(e);
  }
});
$('show-proposal').addEventListener('click', () => {
  tab('changes');
  if (state.run?.proposal_id) openProposal(state.run.proposal_id).catch(fail);
});
$('copy-answer').addEventListener('click', () => copy(state.fullAnswer));
$('export-answer').addEventListener('click', () => {
  if (state.run) download(`/runs/${state.run.id}/export`, 'analysis.md').catch(fail);
});
$('export-trace').addEventListener('click', () => {
  if (state.run) download(`/runs/${state.run.id}/export?format=json`, 'analysis.json').catch(fail);
});
$('checkpoints').addEventListener('click', async () => {
  if (!state.run) return;
  try {
    const r = await api(`/runs/${state.run.id}/checkpoints`);
    $('checkpoint-content').textContent = JSON.stringify(r, null, 2);
    showDialog('checkpoint-dialog');
  } catch (e) {
    fail(e);
  }
});
$('export-workspace').addEventListener('click', () => {
  if (state.workspace) download(`/workspaces/${state.workspace.id}/export`, 'workspace.zip').catch(fail);
});
$('remove-workspace').addEventListener('click', async () => {
  if (!state.workspace || !confirm('移除这个工作区及其版本、任务记录？此操作无法撤销。')) return;
  try {
    await api('/workspaces/' + state.workspace.id, {
      method: 'DELETE'
    });
    state.workspace = null;
    localStorage.removeItem('studio-workspace');
    await refreshWorkspaces();
  } catch (e) {
    fail(e);
  }
});
$('index-build').addEventListener('click', async () => {
  if (!state.workspace) return;
  if (!confirm('将当前版本的源码片段发送给配置的 Embedding 服务建立向量索引，可能消耗额度。继续？')) return;
  try {
    $('index-build').disabled = true;
    const job = await api('/workspaces/' + state.workspace.id + '/index', {
      method: 'POST'
    });
    await consumeEvents(job.id, async (name, data) => {
      if (name === 'progress') $('workspace-meta').textContent = `语义索引 ${data.current}/${data.total}`;
      if (name === 'error') toast(data.message, true);
      if (name === 'done') toast('语义索引完成');
    });
    await selectWorkspace(state.workspace.id, false);
  } catch (e) {
    fail(e);
  } finally {
    $('index-build').disabled = false;
  }
});
$('history-query').addEventListener('input', () => {
  clearTimeout(searchHistory.timer);
  searchHistory.timer = setTimeout(() => searchHistory(false).catch(fail), 250);
});
$('history-more').addEventListener('click', () => searchHistory(true).catch(fail));
$('conversation').addEventListener('scroll', () => {
  const c = $('conversation');
  state.follow = c.scrollHeight - c.scrollTop - c.clientHeight < 100;
  $('follow-output').classList.toggle('hidden', state.follow || !state.run);
});
$('follow-output').addEventListener('click', () => {
  state.follow = true;
  scrollAnswer();
});
const closeEvidence = button('收起', () => closePanels(), 'small');
document.querySelector('.evidence-panel header').append(closeEvidence);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closePanels();
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'h') {
    e.preventDefault();
    openHistory().catch(fail);
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') {
    e.preventDefault();
    newQuestion();
  }
});
async function boot() {
  syncPanels();
  $('new-shortcut').textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ N' : 'Ctrl N';
  const previous = localStorage.getItem('studio-active-run');
  await refreshSettings();
  await refreshWorkspaces();
  const health = await api('/health');
  if (health.checkpoint_notice) $('mode-badge').title = health.checkpoint_notice;
  if (previous) {
    try {
      await loadRun(previous);
    } catch {
      localStorage.removeItem('studio-active-run');
    }
  }
}
boot().catch(fail);

function formatBytes(n) {
  return n >= 1000000 ? (n / 1000000).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1000)) + ' KB';
}

function syncWorkspaceControls() {
  const available = Boolean(state.workspace);
  for (const id of ['export-workspace', 'remove-workspace', 'new-file']) $(id).disabled = !available;
  const vector = Boolean(state.settings?.embedding_model && state.settings?.allow_source_upload);
  $('index-build').disabled = !available || !vector;
  $('index-build').title = vector ? '为当前版本建立语义索引' : '先在设置中配置 Embedding 模型，并允许发送源码';
  updateTestProfile();
}

function updateComposer() {
  const ai = state.settings?.mode === 'openai';
  const ready = !ai || Boolean(state.settings?.base_url && state.settings?.model && state.settings?.allow_source_upload);
  for (const option of $('intent').options) option.disabled = !ai && option.value !== 'investigate';
  if (!ai) $('intent').value = 'investigate';
  $('intent').options[0].textContent = ai ? '代码问答' : '查找源码';
  const mode = $('intent').value;
  $('send-question').replaceChildren(document.createTextNode(!ai ? '查找源码' : mode === 'change' ? '生成提案' : mode === 'review' ? '开始审查' : '发送问题'), icon('arrow'));
  $('send-question').disabled = state.submitting || !state.workspace || !ready;
  $('question-input').placeholder = !state.workspace ? '先导入一个仓库…' : !ai ? '输入函数名、文件路径或关键词…' :
    mode === 'change' ? '描述要修改的行为、预期结果和涉及的文件…' : mode === 'review' ? '指定要审查的文件或代码，说明关注点…' : '询问实现、调用关系或具体问题…';
  $('composer-mode').textContent = !state.workspace ? '导入仓库后开始' : !ai ? '本地关键词检索 · 连接模型后可问答和生成提案' : !ready ? '请补全模型配置，并允许发送相关源码' : '使用 ' + state.settings.model;
}

function renderStarters() {
  const ai = state.settings?.mode === 'openai';
  const list = $('starter-buttons');
  list.replaceChildren();
  $('starter-note').textContent = ai ? '可以从这些问题开始' : '输入源码关键词，或直接打开文件';
  $('starter-settings').classList.toggle('hidden', ai);
  let prompts = ai ? ['梳理项目入口和主要模块', '追踪一个函数的调用关系', '审查代码中的边界条件'] :
    (state.snapshot?.files || []).slice(0, 3).map(f => f.path);
  for (const prompt of prompts) list.append(button(prompt, () => {
    $('question-input').value = prompt;
    $('question-input').focus();
  }, 'starter-chip'));
}

function syncPanels() {
  const sidebar = $('workspace-sidebar');
  const evidence = document.querySelector('.evidence-panel');
  const sidebarOverlay = innerWidth <= 640 && sidebar.classList.contains('open');
  const evidenceOverlay = innerWidth <= 960 && evidence.classList.contains('open');
  $('panel-backdrop').classList.toggle('hidden', !sidebarOverlay && !evidenceOverlay);
  document.querySelector('.main').inert = sidebarOverlay || evidenceOverlay;
  sidebar.inert = innerWidth <= 640 && !sidebarOverlay;
  evidence.inert = innerWidth <= 960 && !evidenceOverlay;
  $('mobile-files').setAttribute('aria-expanded', String(sidebarOverlay));
}

function openPanel(kind) {
  $('workspace-sidebar').classList.toggle('open', kind === 'sidebar' && innerWidth <= 640);
  document.querySelector('.evidence-panel').classList.toggle('open', kind === 'evidence' && innerWidth <= 960);
  syncPanels();
  if (kind === 'sidebar') $('workspace-picker').focus();
  else if (innerWidth <= 960) closeEvidence.focus();
}

function closePanels() {
  const wasSidebar = $('workspace-sidebar').classList.contains('open');
  const wasEvidence = document.querySelector('.evidence-panel').classList.contains('open');
  $('workspace-sidebar').classList.remove('open');
  document.querySelector('.evidence-panel').classList.remove('open');
  syncPanels();
  if (wasSidebar && innerWidth <= 640) $('mobile-files').focus();
  else if (wasEvidence && innerWidth <= 960) $('view-evidence').focus();
}
$('mobile-files').addEventListener('click', () => openPanel('sidebar'));
$('close-sidebar').addEventListener('click', closePanels);
$('panel-backdrop').addEventListener('click', closePanels);
$('view-evidence').addEventListener('click', () => openPanel('evidence'));
$('intent').addEventListener('change', updateComposer);
$('configure-tests').addEventListener('click', async () => {
  try {
    await openSettings();
    $('docker-settings').open = true;
    $('docker-settings').scrollIntoView({
      block: 'center'
    });
  } catch (e) {
    fail(e);
  }
});
window.addEventListener('resize', syncPanels);
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab' || !$('panel-backdrop') || $('panel-backdrop').classList.contains('hidden') || document.querySelector('dialog[open]')) return;
  const panel = $('workspace-sidebar').classList.contains('open') ? $('workspace-sidebar') : document.querySelector('.evidence-panel');
  const focusable = [...panel.querySelectorAll('button:not(:disabled),select,input,a[href]')].filter(n => n.getClientRects().length);
  const first = focusable[0],
    last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});
