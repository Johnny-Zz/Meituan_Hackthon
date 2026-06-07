let STATE = null;
let CHART_HITS = {bar: [], heat: [], map: []};
let ACTIVE_CASE = null;
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

function esc(s){
  return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function tab(id){
  $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === id));
  $$('.tab').forEach(t => t.classList.remove('active'));
  const target = $('#tab-' + id);
  if(target) target.classList.add('active');
  setTimeout(drawAllCharts, 30);
}
async function api(path, opts={}){
  const r = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  return await r.json();
}
async function loadState(){
  STATE = await api('/api/state');
  render();
}
async function loadSolver(){
  const r = await fetch('/api/solver');
  const box = $('#solverPreview');
  if(box) box.textContent = (await r.text()).slice(0, 20000);
}
function render(){
  if(!STATE) return;
  const ch = STATE.champion || {}, cand = STATE.candidate || {}, audit = STATE.audit || {};
  $('#championScore').textContent = ch.score ?? '--';
  $('#cChampion').textContent = ch.score ?? '--';
  $('#cCandidate').textContent = cand.score ?? cand.status ?? 'idle';
  const fb = STATE.score_feedback || {};
  if($('#cFeedback')) $('#cFeedback').textContent = fb.average_score ? Number(fb.average_score).toFixed(2) : '等待';
  if($('#cFeedbackHint')) $('#cFeedbackHint').textContent = fb.analysis?.avg_delta !== undefined && fb.analysis?.avg_delta !== null ? `较上一轮 Δ${Number(fb.analysis.avg_delta).toFixed(2)}` : '上传截图后自动对比上一轮';
  const qwenBox = $('#qwenStatus');
  if(qwenBox){
    const q = STATE.qwen || {};
    const d = STATE.deepseek || {};
    const qText = q.configured ? `Qwen OCR=${q.ocr_model || '--'}` : 'Qwen OCR 未配置，可粘贴文本回退';
    const dText = d.configured ? `DeepSeek LLM=${d.model || 'DeepSeek-V4-pro'}` : 'DeepSeek LLM 未配置，本地归因回退';
    qwenBox.textContent = `${qText} · ${dText}`;
    qwenBox.className = (q.configured && d.configured) ? 'hint ok' : 'hint warn';
  }
  $('#solverSize').textContent = (audit.size_kb ?? '--') + ' KB';
  $('#cSize').textContent = (audit.size_kb ?? '--') + 'KB';
  $('#heroStatus').textContent = String(cand.status || '').includes('patch') ? (cand.status === 'patch-accepted' ? 'PATCHED' : 'GUARDED') : (cand.status === 'dry-run-logged' ? 'LOGGED' : 'READY');
  renderEvents(); renderMiniLog(); renderOffice(); renderFlow(); renderDataLab(); renderForensics(); renderAudit(audit); renderLogs(); renderPatch(); renderFeedback(); renderBackups(); drawAllCharts();
}
function renderEvents(){
  const box = $('#eventStream'); if(!box) return;
  box.innerHTML = '';
  (STATE.events || []).slice().reverse().slice(0, 55).forEach(e => {
    const div = document.createElement('div'); div.className = 'event';
    div.innerHTML = `<span>${esc(e.time)}</span><b>${esc(e.agent)}</b> · <em>${esc(e.type)}</em><p>${esc(e.message)}</p>`;
    box.appendChild(div);
  });
}
function renderMiniLog(){
  const box = $('#miniLog'); if(!box) return;
  box.innerHTML = (STATE.events || []).slice(-7).map(e => `<div>${esc(e.time)} ${esc(e.agent)}: ${esc(String(e.message).slice(0,38))}</div>`).join('');
}
function renderOffice(){
  const box = $('#agentOffice'); if(!box) return;
  box.querySelectorAll('.desk,.agent,.agent-name').forEach(n => n.remove());
  (STATE.agents || []).forEach(a => {
    const desk = document.createElement('button');
    desk.className = 'desk'; desk.style.left = a.desk[0] + '%'; desk.style.top = a.desk[1] + '%';
    desk.title = '点击查看 ' + a.name; desk.onclick = () => showAgent(a.id); box.appendChild(desk);
    const ag = document.createElement('button');
    ag.className = 'agent'; ag.style.left = a.desk[0] + '%'; ag.style.top = (a.desk[1]-4) + '%'; ag.style.setProperty('--c', a.color);
    ag.onclick = () => showAgent(a.id); box.appendChild(ag);
    const nm = document.createElement('button'); nm.className = 'agent-name'; nm.style.left = a.desk[0] + '%'; nm.style.top = (a.desk[1]+9) + '%';
    nm.innerHTML = `${esc(a.name)}<div class="agent-status">${esc(a.role)} · ${esc(a.status)} · ${esc(a.energy)}%</div>`;
    nm.onclick = () => showAgent(a.id); box.appendChild(nm);
  });
}
function showAgent(id){
  const a = (STATE.agents || []).find(x => x.id === id); if(!a) return;
  $('#agentDrawer').classList.add('open');
  $('#agentDetailName').textContent = a.name;
  const actions = (a.actions || []).map(x => `<li>${esc(x)}</li>`).join('');
  const data = Object.entries(a.key_data || {}).map(([k,v]) => `<div><b>${esc(k)}</b><span>${esc(v)}</span></div>`).join('');
  const logs = (STATE.logs_preview?.agent_logs || []).filter(x => String(x.agent).includes(a.name.split(' ')[0]) || String(x.agent).includes(a.role.split(' ')[0]) || String(x.agent).toLowerCase().includes(id)).slice(-8).reverse();
  $('#agentDetail').innerHTML = `
    <div class="agent-hero" style="--c:${esc(a.color)}"><b>${esc(a.role)}</b><span>${esc(a.status)} · energy ${esc(a.energy)}%</span></div>
    <h3>本轮关键操作</h3><p>${esc(a.last_action || '暂无')}</p>
    <h3>关键数据</h3><div class="kv">${data || '<span>暂无</span>'}</div>
    <h3>职责链</h3><ul class="checklist compact">${actions}</ul>
    <h3>自主迭代日志</h3>${logs.map(l => `<div class="event mini"><span>${esc(l.time)}</span><b>${esc(l.agent)}</b><p>${esc(l.message)}</p></div>`).join('') || '<p class="hint">暂无该 Agent 日志。</p>'}`;
}
function renderFlow(){
  const box = $('#flowNodes'); if(!box) return;
  box.innerHTML = '';
  (STATE.flow_nodes || []).forEach((n, i, arr) => {
    const btn = document.createElement('button');
    btn.className = 'node ' + (n.state || ''); btn.textContent = n.id;
    btn.onclick = () => showFlowNode(n.id);
    box.appendChild(btn);
    if(i < arr.length - 1){ const edge = document.createElement('div'); edge.className = 'edge'; box.appendChild(edge); }
  });
}
function showFlowNode(id){
  const n = (STATE.flow_nodes || []).find(x => x.id === id); if(!n) return;
  $('#flowDetail').innerHTML = `<b>${esc(n.id)}</b><p>${esc(n.detail)}</p><p class="hint">状态：${esc(n.state)}</p>`;
  $('#flowCode').textContent = n.code || '';
}
function renderDataLab(){
  const box = $('#scenarioParams'); if(!box) return;
  const scenes = STATE.training_config?.scenes || {};
  const generated = STATE.generated_cases || {};
  box.innerHTML = Object.entries(scenes).map(([scene, cfg]) => {
    const params = Object.entries(cfg).filter(([k,v]) => typeof v === 'number');
    const sliders = params.map(([k,v]) => {
      const max = v > 10 ? Math.max(1000, Math.ceil(v*2)) : 1;
      const step = v > 10 ? 10 : 0.01;
      return `<label class="slider-row"><span>${esc(k)}</span><input type="range" min="0" max="${max}" step="${step}" value="${esc(v)}" data-scene="${esc(scene)}" data-param="${esc(k)}"><b>${esc(v)}</b></label>`;
    }).join('');
    const g = (generated.items || []).find(x => x.scenario === scene);
    const gline = g ? `<div class="seed-chip">样本：${esc(g.path)} · rows=${esc(g.stats?.rows)} · tasks=${esc(g.stats?.tasks)} · couriers=${esc(g.stats?.couriers)}</div>` : '<div class="seed-chip muted">尚未生成训练样本</div>';
    return `<div class="scenario-card" data-scene-card="${esc(scene)}"><div class="scenario-head"><b>${esc(scene)}</b><button class="ghost save-scene" data-scene="${esc(scene)}">保存参数</button></div><p>${esc(cfg.risk || '')}</p>${sliders}${gline}<button class="ghost generate-one" data-target="${esc(scene)}">仅生成该场景</button></div>`;
  }).join('');
  $$('.slider-row input').forEach(input => {
    input.oninput = () => input.parentElement.querySelector('b').textContent = input.value;
  });
  $$('.save-scene').forEach(btn => btn.onclick = () => saveSceneParams(btn.dataset.scene));
  $$('.generate-one').forEach(btn => btn.onclick = () => doAction('auto_seed_config', {target: btn.dataset.target}));
  const preview = $('#seedConfigPreview');
  if(preview){
    preview.textContent = generated && generated.items ? JSON.stringify(generated, null, 2) : '点击上方按钮生成 generated_cases/* 与 seed_config_large_seed301.json';
  }
}
async function saveSceneParams(scene){
  const params = {};
  $$(`input[data-scene="${CSS.escape(scene)}"]`).forEach(i => params[i.dataset.param] = Number(i.value));
  const res = await api('/api/action', {method:'POST', body:JSON.stringify({action:'save_params', scene, params})});
  $('#actionResult') && ($('#actionResult').textContent = JSON.stringify(res, null, 2));
  await loadState();
}
function renderForensics(){
  const box = $('#forensicsList'); if(!box) return;
  box.innerHTML = '';
  const fromState = STATE.forensics || [];
  const fromFeedback = (STATE.score_feedback?.forensics || []);
  const seen = new Set();
  const items = [...fromFeedback, ...fromState].filter(f => {
    const key = `${f.scene}|${f.finding}|${f.updated_at || ''}`;
    if(seen.has(key)) return false; seen.add(key); return true;
  });
  if(!items.length){ box.innerHTML = '<p class="hint">暂无错误归因。上传分数截图或运行一键训练后实时更新。</p>'; return; }
  items.forEach(f => {
    const div = document.createElement('div'); div.className = 'forensic ' + (f.severity || '');
    div.dataset.case = f.scene || '';
    if(ACTIVE_CASE && f.scene === ACTIVE_CASE) div.classList.add('active-forensic');
    div.innerHTML = `<div class="forensic-head"><b>${esc(f.scene)}</b><span>${esc(f.severity || 'info')}</span></div>
      <p><b>结论：</b>${esc(f.finding)}</p><p><b>具体原因：</b>${esc(f.reason)}</p>
      ${f.evidence ? `<p class="hint"><b>证据：</b>${esc(f.evidence)}</p>` : ''}
      <p class="hint">来源：${esc(f.source || 'state')} · 更新时间：${esc(f.updated_at || '')}</p>
      <div class="grid two"><div><h3>本轮改写中的错误代码</h3><pre class="codebox small">${esc(f.bad_code)}</pre></div><div><h3>修正方向</h3><pre class="codebox small">${esc(f.patch)}</pre></div></div>`;
    box.appendChild(div);
  });
}

function openCase(caseName){
  ACTIVE_CASE = caseName;
  tab('forensics');
  renderForensics();
  setTimeout(() => {
    const el = document.querySelector(`[data-case="${CSS.escape(caseName)}"]`);
    if(el) el.scrollIntoView({behavior:'smooth', block:'center'});
  }, 80);
}
function renderAudit(a){
  const box = $('#auditBox'); if(!box) return;
  box.innerHTML = `<div class="big">${esc(a.size_kb ?? 0)} KB</div>
    <div class="hint">risk: ${esc(a.risk)} · functions: ${esc(a.functions ?? 0)} · lines: ${esc(a.lines ?? 0)}</div>
    <p>dangerous: ${esc((a.dangerous || []).join(', ') || 'none')}</p>
    <p>sha256: <code>${esc(a.sha256 || '--')}</code></p>`;
}
function renderLogs(){
  const logs = STATE.logs_preview || {};
  $('#notesLog') && ($('#notesLog').textContent = logs.notes || 'Notes.md 暂无内容');
  $('#handoverLog') && ($('#handoverLog').textContent = logs.handover || 'Handover.md 暂无内容');
  const agentBox = $('#agentLogs');
  if(agentBox){ agentBox.innerHTML = (logs.agent_logs || []).slice().reverse().slice(0,80).map(e => `<div class="event"><span>${esc(e.time || e.iso)}</span><b>${esc(e.agent)}</b> · <em>${esc(e.type)}</em><p>${esc(e.message)}</p></div>`).join('') || '<p class="hint">暂无 Agent 日志。</p>'; }
  const trainBox = $('#trainingLogs');
  if(trainBox){
    const rounds = (logs.training_rounds || []).slice().reverse().map(r => `<div class="event"><span>Round ${esc(r.round)}</span><b>${esc(r.source)}</b><p>${esc(r.change)}</p><small>${esc(r.eval?.reason || '')}</small></div>`).join('');
    const chats = (logs.chat || []).slice(-12).reverse().map(c => `<div class="event"><span>${esc(c.time)}</span><b>${esc(c.role)}</b><p>${esc(String(c.message).slice(0,240))}</p></div>`).join('');
    trainBox.innerHTML = rounds + chats || '<p class="hint">暂无训练或对话日志。</p>';
  }
}

function renderPatch(){
  const reports = (STATE.patch_reports || STATE.logs_preview?.patch_reports || []).slice().reverse();
  const box = $('#patchReports');
  if(box){
    box.innerHTML = reports.slice(0,40).map(r => {
      const plan = r.plan || {}; const gate = r.gate || {};
      const checks = (gate.checks || []).map(c => `${c.case}: ${c.ok ? 'PASS' : 'FAIL'} ${c.delta !== undefined ? 'Δ'+c.delta : ''}`).join(' | ');
      return `<div class="event ${r.accepted ? 'pass' : 'fail'}"><span>Round ${esc(r.round)} · ${esc(r.time || '')}</span><b>${esc(plan.title || 'patch')}</b><p>${esc(r.decision_reason || '')}</p><small>accepted=${esc(r.accepted)} · gate=${esc(gate.ok)} · ${esc(checks)}</small></div>`;
    }).join('') || '<p class="hint">暂无 patch report。点击“自主改写 solver.py”生成。</p>';
  }
  const diff = $('#patchDiff');
  if(diff) diff.textContent = STATE.latest_patch_diff || '暂无 diff。';
}

function renderFeedback(){
  const fb = STATE.score_feedback || {};
  const analysis = fb.analysis || {};
  const cases = fb.cases || [];
  const summaryBox = $('#feedbackSummary');
  if(summaryBox){
    if(fb.average_score){
      const delta = analysis.avg_delta;
      const cls = delta < 0 ? 'good' : delta > 0 ? 'bad' : 'flat';
      summaryBox.innerHTML = `<div class="feedback-kpis"><div><span>最新平均分</span><b>${Number(fb.average_score).toFixed(2)}</b></div><div><span>上一轮</span><b>${analysis.previous_average_score !== undefined && analysis.previous_average_score !== null ? Number(analysis.previous_average_score).toFixed(2) : '--'}</b></div><div><span>Δ</span><b class="delta ${cls}">${delta !== undefined && delta !== null ? Number(delta).toFixed(2) : '--'}</b></div><div><span>完成算例</span><b>${esc(fb.completed_cases || '--')}</b></div></div><p>${esc(analysis.summary || '')}</p>`;
    } else {
      summaryBox.textContent = '等待上传线上提交截图。';
    }
  }
  const raw = $('#feedbackRaw'); if(raw) raw.textContent = fb.raw_text || '等待上传。';
  const ana = $('#feedbackAnalysis');
  if(ana){
    const focus = (analysis.next_focus || []).map(x => `<li>${esc(x)}</li>`).join('');
    const plan = (analysis.next_round_plan || []).map(p => `<div class="plan-card"><b>${esc(p.target)} / ${esc(p.patch_surface)}</b><p>${esc(p.action)}</p><small>风险：${esc(p.risk || '--')}</small></div>`).join('');
    const llm = analysis.llm_summary ? `<h3>DeepSeek LLM 补充</h3><p>${esc(analysis.llm_summary)}</p>` : '';
    ana.innerHTML = fb.average_score ? `<p>${esc(analysis.summary || '')}</p>${llm}<h3>下一轮 Focus</h3><ul class="checklist compact">${focus || '<li>暂无</li>'}</ul><h3>Patch Plan</h3>${plan || '<p class="hint">暂无计划。</p>'}` : '<p class="hint">上传截图后生成。</p>';
  }
  const table = $('#feedbackTable');
  if(table){
    table.innerHTML = cases.length ? `<div class="score-row head"><b>case</b><b>上一轮</b><b>本轮</b><b>Δ</b><b>覆盖/耗时</b><b>状态</b></div>` + cases.map(c => {
      const d = c.delta;
      const cls = d < 0 ? 'good' : d > 0 ? 'bad' : 'flat';
      return `<div class="score-row six"><span>${esc(c.case)}</span><span>${c.previous_score !== undefined && c.previous_score !== null ? Number(c.previous_score).toFixed(2) : '--'}</span><b>${Number(c.score).toFixed(2)}</b><b class="delta ${cls}">${d !== undefined && d !== null ? Number(d).toFixed(2) : '--'}</b><span>${esc(c.assigned || '--')} / ${esc(c.time_ms || '--')}ms</span><span class="tag ${cls}">${esc(c.trend || '--')}</span></div>`;
    }).join('') : '<p class="hint">暂无 case 反馈。</p>';
  }
  const hist = $('#feedbackHistory');
  if(hist){
    const items = STATE.score_feedback_history || [];
    hist.innerHTML = items.slice().reverse().slice(0,30).map((r,i) => {
      const rec = r.record || r;
      const avg = rec.average_score ?? rec.record?.average_score;
      const delta = rec.avg_delta ?? rec.analysis?.avg_delta;
      return `<div class="event"><span>${esc(rec.time || rec.record?.time || '')}</span><b>${avg !== undefined && avg !== null ? Number(avg).toFixed(2) : '--'}</b><p>Δ=${delta !== undefined && delta !== null ? Number(delta).toFixed(2) : '--'} · ${esc(rec.completed_cases || '')}</p></div>`;
    }).join('') || '<p class="hint">暂无历史。</p>';
  }
  const next = $('#nextPlanPreview'); if(next) next.textContent = JSON.stringify(STATE.next_round_plan || {}, null, 2);
}

async function uploadScoreFile(file){
  if(!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const notes = prompt('这张截图对应哪一轮/哪次线上提交？可留空。', '线上提交反馈');
    const res = await api('/api/feedback/upload', {method:'POST', body:JSON.stringify({image_data: reader.result, filename: file.name, notes: notes || ''})});
    const out = $('#actionResult') || $('#feedbackRaw'); if(out) out.textContent = JSON.stringify(res.feedback || res, null, 2);
    STATE = res.state || await api('/api/state');
    render(); tab('feedback');
  };
  reader.readAsDataURL(file);
}

async function parseScoreText(){
  const text = $('#scoreTextInput')?.value || '';
  if(!text.trim()) return alert('请先粘贴分数文本。');
  const res = await api('/api/feedback/text', {method:'POST', body:JSON.stringify({text, notes:'manual pasted score feedback'})});
  const out = $('#feedbackRaw'); if(out) out.textContent = JSON.stringify(res.feedback || res, null, 2);
  STATE = res.state || await api('/api/state');
  render(); tab('feedback');
}

function renderBackups(){
  const items = STATE.backups || [];
  const table = $('#backupTable');
  const rb = $('#rollbackList');
  const rows = items.map(b => `<div class="backup-row"><div><b>${esc(b.archive)}</b><p>${esc(b.note || '')}</p></div><div><span>时间</span><b>${esc(b.created_at)}</b></div><div><span>轮次</span><b>${esc(b.round)}</b></div><div><span>代码</span><b>${esc(b.solver_name || 'submission/solver.py')}</b></div><div><span>Hash</span><code>${esc(b.solver_hash)}</code></div></div>`).join('');
  if(table) table.innerHTML = rows || '<p class="hint">暂无备份。点击“创建备份”或“一键训练”生成。</p>';
  if(rb) rb.innerHTML = items.map(b => `<div class="rollback-card"><div><b>${esc(b.archive)}</b><p>${esc(b.note || '无说明')}</p><div class="hash">${esc(b.created_at)} · Round ${esc(b.round)} · ${esc(b.solver_name || 'submission/solver.py')} · ${esc(b.solver_hash)}</div></div><button class="danger-btn" data-rollback="${esc(b.archive)}">回滚到此版本</button></div>`).join('') || '<p class="hint">暂无可回滚版本。先在配置台创建备份。</p>';
  $$('[data-rollback]').forEach(btn => btn.onclick = () => rollback(btn.dataset.rollback));
}
async function doAction(action, extra={}){
  if(action === 'one_click_train'){
    const change = prompt('请输入本轮训练改动说明（会写入 Notes.md / Handover.md / manifest）：', '基于 DataLab 当前参数执行一键训练');
    if(change === null) return;
    extra.change = change;
  }
  if(action === 'autonomous_patch'){
    const objective = prompt('请输入自主改写目标（会进入 Qwen/本地 patch 生成器，并写入 diff/gate/Notes）：', '基于 large_seed301 本地反馈，生成低风险 CONFIG patch，并通过 no-regression gate');
    if(objective === null) return;
    extra.objective = objective;
  }
  if(action === 'backup'){
    const note = prompt('备份说明：', 'manual UI backup before modification');
    if(note === null) return;
    extra.note = note;
  }
  const res = await api('/api/action', {method:'POST', body:JSON.stringify({action, ...extra})});
  const out = $('#actionResult'); if(out) out.textContent = JSON.stringify(res, null, 2);
  if(res.seed_config && $('#seedConfigPreview')) $('#seedConfigPreview').textContent = JSON.stringify(res.seed_config, null, 2);
  await loadState();
}
async function rollback(archive){
  const one = confirm(`准备回滚到：\n${archive}\n\n系统会先创建 pre_restore 保护备份。是否继续？`);
  if(!one) return;
  const text = prompt('二次确认：请输入 ROLLBACK 才会执行恢复。');
  if(text !== 'ROLLBACK') return alert('已取消：确认文本不正确。');
  const res = await api('/api/action', {method:'POST', body:JSON.stringify({action:'rollback', archive, confirm:'ROLLBACK'})});
  const out = $('#actionResult'); if(out) out.textContent = JSON.stringify(res, null, 2);
  await loadState(); await loadSolver();
  alert(res.ok ? '回滚完成。' : '回滚失败，请查看结果面板。');
}
async function sendChat(){
  const input = $('#chatInput'); const msg = input.value.trim(); if(!msg) return;
  const log = $('#chatLog'); log.innerHTML += `<div class="msg user">${esc(msg)}</div>`; input.value = '';
  const res = await api('/api/chat', {method:'POST', body:JSON.stringify({message:msg})});
  log.innerHTML += `<div class="msg llm">${esc(res.message)}</div>`; log.scrollTop = log.scrollHeight;
  await loadState();
}
function chartCtx(id){ const c = $('#' + id); return c ? c.getContext('2d') : null; }
function clear(ctx){ if(ctx) ctx.clearRect(0,0,ctx.canvas.width,ctx.canvas.height); }
function cssColor(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function drawAllCharts(){ if(!STATE) return; drawBar(); drawHeat(); drawRadar(); drawLine(); drawMap(); }
function currentScore(d){ return Number(d.official_score ?? d.candidate ?? d.champion ?? 0); }
function previousScore(d){ return Number(d.previous_score ?? d.champion ?? d.official_score ?? 0); }
function scoreDelta(d){ const v = d.delta_vs_previous; return v === undefined || v === null ? null : Number(v); }
function trendColor(d){ const delta = scoreDelta(d); if(delta !== null && delta < -0.25) return '#2be88a'; if(delta !== null && delta > 0.25) return '#ff6b7d'; if(d.status==='protected') return '#ff9d42'; if(d.status==='anchor') return '#58d6ff'; return '#a479ff'; }
function drawBar(){
  const ctx = chartCtx('barChart'); if(!ctx) return; clear(ctx); CHART_HITS.bar = [];
  const data = (STATE.case_results || []).slice(0,10); const w = ctx.canvas.width, h = ctx.canvas.height;
  ctx.fillStyle = '#98a2b3'; ctx.font = '12px Arial'; ctx.fillText('Official feedback score: current bar + previous marker · click case', 20, 20);
  const max = Math.max(...data.map(d => Math.max(currentScore(d), previousScore(d))), 1); const bw = (w-60)/Math.max(1,data.length);
  data.forEach((d,i) => {
    const cur = currentScore(d), prev = previousScore(d); const bh = (h-78)*(cur/max); const x=35+i*bw, y=h-38-bh;
    ctx.fillStyle = trendColor(d); ctx.fillRect(x,y,bw*0.58,bh);
    CHART_HITS.bar.push({caseName:d.case,x,y,w:bw*.62,h:Math.max(16,bh+26)});
    if(prev){ const py = h-38-(h-78)*(prev/max); ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(x-2,py); ctx.lineTo(x+bw*.62,py); ctx.stroke(); }
    const delta = scoreDelta(d); if(delta !== null){ ctx.fillStyle = delta < 0 ? '#2be88a' : delta > 0 ? '#ff6b7d' : '#98a2b3'; ctx.font='10px Arial'; ctx.fillText((delta>0?'+':'')+delta.toFixed(1), x, Math.max(34,y-4)); }
    ctx.save(); ctx.translate(x+2,h-25); ctx.rotate(-0.55); ctx.fillStyle='#c7cfdd'; ctx.font='11px Arial'; ctx.fillText(d.case.replace('_seed',''),0,0); ctx.restore();
  });
}
function drawHeat(){
  const ctx = chartCtx('heatChart'); if(!ctx) return; clear(ctx); CHART_HITS.heat = [];
  const data = (STATE.case_results || []).slice(0,10); const cols=5, cellW=86, cellH=48, x0=24,y0=34;
  ctx.fillStyle = '#98a2b3'; ctx.font='12px Arial'; ctx.fillText('Risk heatmap: click a cell to inspect live forensics', 20, 20);
  data.forEach((d,i)=>{
    const c=i%cols, r=Math.floor(i/cols); const delta = scoreDelta(d);
    let fill='rgb(70,78,105)';
    if(delta !== null && delta < -0.25) fill='rgb(38,150,95)';
    else if(delta !== null && delta > 0.25) fill='rgb(220,70,95)';
    else if(d.status==='protected') fill='rgb(150,92,52)';
    else if(d.trend==='stalled') fill='rgb(116,94,180)';
    const x=x0+c*cellW, y=y0+r*cellH;
    ctx.fillStyle=fill; ctx.fillRect(x,y,cellW-8,cellH-8); CHART_HITS.heat.push({caseName:d.case,x,y,w:cellW-8,h:cellH-8});
    ctx.fillStyle='#fff'; ctx.font='11px Arial'; ctx.fillText(d.case.split('_seed')[0],x+6,y+18);
    ctx.fillText(delta !== null ? 'Δ'+delta.toFixed(1) : (d.status||''),x+6,y+34);
  });
}
function drawRadar(){
  const ctx = chartCtx('radarChart'); if(!ctx) return; clear(ctx);
  const data = (STATE.case_results || []); const fb = STATE.score_feedback || {}; const analysis = fb.analysis || {};
  const w=ctx.canvas.width,h=ctx.canvas.height,cx=w/2,cy=h/2+8,R=86;
  const avgDelta = Number(analysis.avg_delta ?? 0);
  const covered = data.map(d => String(d.assigned||'').match(/(\d+)\/(\d+)/)).filter(Boolean).map(m => Number(m[1])/Math.max(1,Number(m[2])));
  const coverage = covered.length ? covered.reduce((a,b)=>a+b,0)/covered.length : .9;
  const improvement = Math.max(.1, Math.min(1, .55 + (-avgDelta)/8));
  const protectedStable = data.filter(d => ['small_seed100','tiny_seed42','scarce_couriers_seed401'].includes(d.case)).every(d => (scoreDelta(d) ?? 0) <= .25) ? .92 : .45;
  const runtimeSafe = data.filter(d => d.time_ms).length ? 1 - Math.min(.55, data.filter(d => d.time_ms >= 8900).length / 10) : .74;
  const labels=['coverage','improve','protected','runtime','backup','audit']; const vals=[coverage, improvement, protectedStable, runtimeSafe, .72, .96];
  ctx.strokeStyle='#30384d'; ctx.fillStyle='#98a2b3'; ctx.font='12px Arial';
  for(let k=1;k<=4;k++){ ctx.beginPath(); labels.forEach((_,i)=>{ const a=-Math.PI/2+i*2*Math.PI/labels.length; const x=cx+Math.cos(a)*R*k/4,y=cy+Math.sin(a)*R*k/4; i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.closePath(); ctx.stroke(); }
  ctx.beginPath(); vals.forEach((v,i)=>{ const a=-Math.PI/2+i*2*Math.PI/labels.length; const x=cx+Math.cos(a)*R*v,y=cy+Math.sin(a)*R*v; i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.closePath(); ctx.fillStyle='rgba(88,214,255,.22)'; ctx.fill(); ctx.strokeStyle='#58d6ff'; ctx.stroke();
  labels.forEach((lab,i)=>{ const a=-Math.PI/2+i*2*Math.PI/labels.length; ctx.fillStyle='#c7cfdd'; ctx.fillText(lab,cx+Math.cos(a)*(R+18)-22,cy+Math.sin(a)*(R+18)); });
}
function drawLine(){
  const ctx = chartCtx('lineChart'); if(!ctx) return; clear(ctx);
  const hist = (STATE.score_feedback_history || []).map(x => x.record || x).filter(x => x.average_score !== undefined || x.record?.average_score !== undefined);
  const w=ctx.canvas.width,h=ctx.canvas.height,x0=35,y0=30,gw=w-60,gh=h-70;
  ctx.strokeStyle='#30384d'; ctx.strokeRect(x0,y0,gw,gh); ctx.fillStyle='#98a2b3'; ctx.font='12px Arial'; ctx.fillText('Official feedback average score trend', 20,20);
  if(hist.length >= 2){
    const vals = hist.map(x => Number(x.average_score ?? x.record?.average_score)); const min=Math.min(...vals), max=Math.max(...vals); const span=Math.max(1,max-min);
    ctx.beginPath(); vals.forEach((v,i)=>{ const x=x0+i*gw/Math.max(1,vals.length-1), y=y0+gh-(v-min)/span*gh; i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.strokeStyle='#58d6ff'; ctx.lineWidth=2; ctx.stroke();
    vals.forEach((v,i)=>{ const x=x0+i*gw/Math.max(1,vals.length-1), y=y0+gh-(v-min)/span*gh; ctx.fillStyle=i===vals.length-1?'#2be88a':'#58d6ff'; ctx.beginPath(); ctx.arc(x,y,4,0,Math.PI*2); ctx.fill(); });
    ctx.fillStyle='#c7cfdd'; ctx.fillText('latest '+vals[vals.length-1].toFixed(2), x0+gw-110, y0+18);
    return;
  }
  const data = (STATE.case_results || []).slice(0,5);
  data.forEach((d,idx)=>{ const series=(d.score_history||[]).map(x=>x.score); const hist2=series.length?series:(d.delta_history||[0,0,0,0,0]); ctx.beginPath(); hist2.forEach((v,i)=>{ const x=x0+i*gw/Math.max(1,hist2.length-1), y=y0+gh/2+(Number(v)-Number(hist2[0]||0))*4; i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.strokeStyle=['#58d6ff','#ff6b7d','#a479ff','#ff9d42','#2be88a'][idx%5]; ctx.stroke(); ctx.fillStyle=ctx.strokeStyle; ctx.fillText(d.case.split('_seed')[0], x0+gw-112, y0+18+idx*16); });
}
function drawMap(){
  const ctx = chartCtx('mapChart'); if(!ctx) return; clear(ctx); CHART_HITS.map = [];
  const w=ctx.canvas.width,h=ctx.canvas.height; ctx.fillStyle='#111722'; ctx.fillRect(0,0,w,h); ctx.fillStyle='#98a2b3'; ctx.font='13px Arial'; ctx.fillText('Case map: radius = official score pressure, color = current-vs-previous trend · click bubble',20,24);
  (STATE.case_results||[]).forEach(d=>{ const x=(d.x||50)/100*w,y=(d.y||50)/100*h; const r=Math.max(10, Math.min(34, Math.sqrt(currentScore(d)||100))); ctx.beginPath(); ctx.arc(x,y,r,0,Math.PI*2); const delta=scoreDelta(d); ctx.fillStyle = delta !== null && delta < -0.25 ? 'rgba(43,232,138,.72)' : delta !== null && delta > 0.25 ? 'rgba(255,107,125,.78)' : d.status==='protected' ? 'rgba(255,157,66,.68)' : 'rgba(164,121,255,.58)'; ctx.fill(); ctx.strokeStyle='#fff3'; ctx.stroke(); CHART_HITS.map.push({caseName:d.case,x:x-r,y:y-r,w:r*2,h:r*2,cx:x,cy:y,r}); ctx.fillStyle='#fff'; ctx.font='12px Arial'; ctx.fillText(d.case.replace('_seed',''),x+r+4,y+4); if(delta!==null){ ctx.fillStyle=delta<0?'#2be88a':delta>0?'#ff6b7d':'#c7cfdd'; ctx.fillText('Δ'+delta.toFixed(1),x+r+4,y+18); } });
}

function chartClick(kind, ev){
  const rect = ev.currentTarget.getBoundingClientRect();
  const scaleX = ev.currentTarget.width / rect.width;
  const scaleY = ev.currentTarget.height / rect.height;
  const x = (ev.clientX - rect.left) * scaleX;
  const y = (ev.clientY - rect.top) * scaleY;
  const hit = (CHART_HITS[kind] || []).find(h => {
    if(h.r !== undefined){ const dx=x-h.cx, dy=y-h.cy; return dx*dx + dy*dy <= h.r*h.r; }
    return x>=h.x && x<=h.x+h.w && y>=h.y && y<=h.y+h.h;
  });
  if(hit) openCase(hit.caseName);
}

$$('.nav-btn').forEach(b => b.onclick = () => tab(b.dataset.tab));
$$('[data-tab-shortcut]').forEach(x => x.onclick = () => tab(x.dataset.tabShortcut));
$$('[data-jump]').forEach(x => x.onclick = () => tab(x.dataset.jump));
$$('[data-close]').forEach(x => x.onclick = () => $('#' + x.dataset.close).classList.remove('open'));
$$('.control-card[data-action]').forEach(b => b.onclick = () => doAction(b.dataset.action, {scene:b.dataset.scene}));
$('#panicBtn').onclick = () => doAction('pause');
$('#auditBtn').onclick = () => doAction('audit');
$('#oneTrainTop').onclick = () => doAction('one_click_train');
$('#oneTrainHero').onclick = () => doAction('one_click_train');
$('#autoPatchHero') && ($('#autoPatchHero').onclick = () => doAction('autonomous_patch'));
$('#autoPatchTop') && ($('#autoPatchTop').onclick = () => doAction('autonomous_patch'));
$('#autoSeedBtn').onclick = () => doAction('auto_seed_config', {target:'all'});
$('#sendChat').onclick = sendChat;
$('#runGeneratedTrainBtn') && ($('#runGeneratedTrainBtn').onclick = () => doAction('one_click_train', {change:'DataLab generated scenario cases ready; run one-key training with current parameters'}));
$('#refreshGeneratedBtn') && ($('#refreshGeneratedBtn').onclick = loadState);
['barChart','heatChart','mapChart'].forEach(id => { const c = $('#'+id); if(c){ c.style.cursor='pointer'; c.onclick = ev => chartClick(id==='barChart'?'bar':id==='heatChart'?'heat':'map', ev); } });
$('#uploadScoreBtn') && ($('#uploadScoreBtn').onclick = () => $('#scoreImageInput').click());
$('#scoreImageInput') && ($('#scoreImageInput').onchange = e => uploadScoreFile(e.target.files[0]));
$('#parseScoreTextBtn') && ($('#parseScoreTextBtn').onclick = parseScoreText);
$('#refreshFeedbackBtn') && ($('#refreshFeedbackBtn').onclick = loadState);
const dz = $('#scoreDropzone');
if(dz){
  dz.onclick = () => $('#scoreImageInput').click();
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('drag'); };
  dz.ondragleave = () => dz.classList.remove('drag');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('drag'); uploadScoreFile(e.dataTransfer.files[0]); };
}
window.addEventListener('resize', drawAllCharts);
loadState(); loadSolver(); setInterval(loadState, 3000);
