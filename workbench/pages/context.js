// 页 04 Context Pack：显式 POST 生成审计快照 + 快照回读。
// 页面载入/角色切换/时间编辑绝不自动 POST；保存快照不改变业务状态。
import { esc, panel, head, foot, tag, loading, empty, errorBox, idLine } from '../lib/html.js';
import { buildQuery, casePathToRoute, isUuid } from '../lib/url.js';
import { describeError } from '../lib/api.js';
import { buildContextPackBody, toLocalInputValue, normalizeTimeInput } from '../lib/contextbody.js';
import { currentConditions, snapshotConditions, conditionsMatch } from '../lib/snapshot.js';
import { verificationLabel, verificationKind, shortId } from '../lib/format.js';

const MAX_PAGES = 10;

function anchorEntries(config) {
  return Object.entries(config?.state_paths || {})
    .map(([label, path]) => ({ label, objectId: casePathToRoute(path).objectId }))
    .filter((entry) => entry.objectId);
}

function selectedItemHtml(item) {
  const refs = (item.source_refs || []).map((ref) =>
    `<div class="tk-checkline"><span class="tk-checkmark">→</span><span class="tk-mono">${esc(shortId(ref.object_id))}… @ ${esc(shortId(ref.revision_id))}… · hash ${esc(shortId(ref.payload_hash))}… <button type="button" class="tk-copy" data-copy="${esc(ref.payload_hash)}">复制 hash</button></span></div>`,
  ).join('');
  const review = item.delivery_review
    ? `<div class="tk-divider"></div><h3>时间截面内的交付评审投影</h3>
       <p class="tk-tiny tk-muted" style="margin:6px 0">按 valid/known 双时间过滤，不代表当前业务状态。</p>
       <dl class="tk-kv">
         <dt>评审结果</dt><dd>${tag(verificationLabel(item.delivery_review.verification_result).label, verificationKind(item.delivery_review.verification_result))} <span class="tk-mono tk-muted">${esc(item.delivery_review.verification_result)}</span></dd>
         <dt>提交序号</dt><dd>v${esc(item.delivery_review.submission_seq)}</dd>
         ${item.delivery_review.review_note ? `<dt>评审意见</dt><dd>${esc(item.delivery_review.review_note)}</dd>` : ''}
       </dl>`
    : '';
  return `<div class="tk-packitem">
    <strong>${esc(item.payload?.title || item.object_id)} <span class="tk-mono tk-muted">${esc(item.object_type)}</span></strong>
    <div class="tk-packmeta">${item.delivery_status ? tag(`delivery: ${item.delivery_status}`, 'info') : ''}</div>
    <dl class="tk-kv" style="margin-top:8px">
      <dt>对象</dt><dd>${idLine(item.object_id)}</dd>
      <dt>revision</dt><dd>${idLine(item.revision_id)}</dd>
      <dt>payload hash</dt><dd>${idLine(item.payload_hash)}</dd>
    </dl>
    ${refs ? `<div style="margin-top:8px"><h3>source refs（冻结 revision 的内容来源）</h3>${refs}</div>` : ''}
    ${review}
    <details style="margin-top:10px"><summary class="tk-small tk-muted">payload 原文</summary><pre class="tk-pre" style="margin-top:8px">${esc(JSON.stringify(item.payload, null, 2))}</pre></details>
  </div>`;
}

function conditionNoteHtml(match) {
  if (!match) return '<div class="tk-note tk-warning" style="margin-bottom:12px">当前时间条件尚未填写完整。以下快照仍使用其已保存的条件，请填写完整后再生成新快照。</div>';
  if (match.same) return '';
  const parts = [];
  if (match.timeDiffers) parts.push('时间条件不同');
  if (match.objectsDiffer) parts.push('对象集合不同');
  return `<div class="tk-note tk-warning" style="margin-bottom:12px">当前输入与该快照${esc(parts.join('、'))}。以下快照按其自身标注的条件生成，不是当前条件的结果；需要新结果请重新点击「生成审计快照」。</div>`;
}

function snapshotCard(snap, index) {
  const data = snap.data;
  const selected = (data.selected || []).map(selectedItemHtml).join('');
  const excluded = (data.excluded || []).map((item) =>
    `<div class="tk-disabledrow"><span class="tk-mono">${esc(item.object_id || '')}</span>${tag(item.reason || '未选入', 'wait')}</div>`,
  ).join('');
  return `<div class="tk-section">${panel(
    snap.source === 'generated' ? '本次生成的审计快照' : '快照回读',
    `<div class="tk-pad">
      <div class="tk-condnote" data-snap-index="${index}"></div>
      <dl class="tk-snapmeta">
        <dt>快照 ID</dt><dd>${idLine(data.context_snapshot_id)}</dd>
        <dt>记录时间（原始）</dt><dd class="tk-mono">${esc(data.recorded_at || '—')}</dd>
        <dt>valid_at（原始）</dt><dd class="tk-mono">${esc(data.valid_at || '—')}</dd>
        <dt>known_at（原始）</dt><dd class="tk-mono">${esc(data.known_at || '—')}</dd>
      </dl>
      <div class="tk-divider"></div>
      <h3>选中对象（${(data.selected || []).length}）</h3>
      <div class="tk-packlist" style="margin-top:8px">${selected || '<p class="tk-small tk-muted" style="padding:12px 0">无选中对象。</p>'}</div>
      <div class="tk-divider"></div>
      <h3>未选入（${(data.excluded || []).length}）</h3>
      ${excluded || '<p class="tk-small tk-muted" style="margin-top:8px">无排除对象。</p>'}
      <p class="tk-tiny tk-muted" style="margin-top:10px">响应不含聚合 snapshot hash；逐 revision 的 payload_hash 如上。时间显示为服务端原始 ISO 值。快照内容为历史选择结果，回读时按当前权限重新授权。</p>
    </div>`,
    tag(snap.source === 'generated' ? 'POST 已持久化' : 'GET 回读', 'info'),
  )}</div>`;
}

export async function renderContext(main, ctx, route) {
  const anchors = anchorEntries(ctx.config);
  const local = {
    selected: new Map(anchors.map((a) => [a.objectId, a.label])),
    candidates: [],
    candidatesError: null,
    candidatesTruncated: false,
    busy: false,
    postError: null,
    snapshots: [],
    readbackId: route.snapshotId || '',
    readbackError: null,
    addError: '',
    validAt: toLocalInputValue(),
    knownAt: toLocalInputValue(),
  };

  function currentMatch() {
    let normalized;
    try {
      normalized = { validAt: normalizeTimeInput(local.validAt), knownAt: normalizeTimeInput(local.knownAt) };
    } catch {
      return null;
    }
    return currentConditions({ ...normalized, objectIds: [...local.selected.keys()] });
  }

  // 只更新条件提示，不重建 DOM：保留输入焦点，且不触发任何请求。
  function updateConditionHints() {
    const current = currentMatch();
    local.snapshots.forEach((snap, index) => {
      const el = main.querySelector(`[data-snap-index="${index}"]`);
      if (!el) return;
      const match = current ? conditionsMatch(current, snapshotConditions(snap.data, snap.requestIds)) : null;
      el.innerHTML = conditionNoteHtml(match);
    });
  }

  async function loadCandidates() {
    const domains = [];
    let cursor = null;
    for (let i = 0; i < MAX_PAGES; i += 1) {
      const page = await ctx.client.getJson(`/v1/domains${buildQuery({ limit: 100, cursor })}`, { signal: ctx.signal });
      if (!ctx.isCurrent()) return;
      domains.push(...(page.items || []));
      cursor = page.next_cursor;
      if (!cursor) break;
      if (i === MAX_PAGES - 1) local.candidatesTruncated = true;
    }
    for (const domain of domains) {
      let objectCursor = null;
      for (let i = 0; i < MAX_PAGES; i += 1) {
        const page = await ctx.client.getJson(
          `/v1/objects${buildQuery({ domain_id: domain.domain_id, limit: 100, cursor: objectCursor })}`,
          { signal: ctx.signal },
        );
        if (!ctx.isCurrent()) return;
        local.candidates.push(...(page.items || []));
        objectCursor = page.next_cursor;
        if (!objectCursor) break;
        if (i === MAX_PAGES - 1) local.candidatesTruncated = true;
      }
    }
  }

  function selectionPanel() {
    const chips = [...local.selected.entries()].map(([id, label]) =>
      `<span class="tk-chip">${esc(label || '对象')} <span class="tk-mono">${esc(shortId(id))}…</span><button type="button" data-action="remove-object" data-value="${esc(id)}" aria-label="移除 ${esc(id)}">×</button></span>`,
    ).join('');
    const rows = local.candidates.map((o) => {
      const checked = local.selected.has(o.object_id);
      return `<label class="tk-checkrow" for="tk-pick-${esc(o.object_id)}"><input type="checkbox" id="tk-pick-${esc(o.object_id)}" data-control="pick" value="${esc(o.object_id)}" ${checked ? 'checked' : ''}>
        <span>${esc(o.title || '（无标题）')} <span class="tk-mono tk-muted">${esc(o.object_type)}</span>
        <small class="tk-mono">${esc(o.object_id)}</small></span></label>`;
    }).join('');
    let candidateBody;
    if (local.candidatesError) {
      const info = describeError(local.candidatesError);
      candidateBody = errorBox(info.title, info.detail, info.retryable ? 'retry-candidates' : '');
    } else {
      candidateBody = (rows || '<p class="tk-small tk-muted">当前身份暂无可读对象。</p>') +
        (local.candidatesTruncated ? '<p class="tk-tiny tk-muted" style="margin-top:6px">可读对象超过加载上限，仅载入前若干页；可手动输入 UUID。</p>' : '');
    }
    return panel('选择对象', `<div class="tk-pad">
      <div class="tk-chiplist">${chips || '<span class="tk-small tk-muted">尚未选择对象。</span>'}</div>
      <div class="tk-divider"></div>
      <h3>从当前身份可读对象中选择</h3>
      <div style="margin-top:6px;max-height:260px;overflow:auto">${candidateBody}</div>
      <div class="tk-divider"></div>
      <label class="tk-field" for="tk-add-id" style="width:100%">手动添加对象 UUID
        <span class="tk-flex" style="flex:1"><input type="text" class="tk-input" id="tk-add-id" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" spellcheck="false">
        <button type="button" class="tk-button" data-action="add-object">添加</button></span>
      </label>
      ${local.addError ? `<p class="tk-tiny" style="color:var(--tk-red);margin-top:6px">${esc(local.addError)}</p>` : ''}
      <p class="tk-tiny tk-muted" style="margin-top:8px">默认锚点来自演练配置的三项独立状态对象；添加的对象按当前身份逐个授权。</p>
    </div>`, tag(`已选 ${local.selected.size} 个`, 'info'));
  }

  function conditionsPanel() {
    return panel('组装条件', `<div class="tk-pad">
      <div class="tk-formrow"><label for="tk-valid-at">valid_at 业务有效时间</label><input type="datetime-local" step="0.001" class="tk-input" id="tk-valid-at" value="${esc(local.validAt)}"></div>
      <div class="tk-formrow"><label for="tk-known-at">known_at 系统知悉时间</label><input type="datetime-local" step="0.001" class="tk-input" id="tk-known-at" value="${esc(local.knownAt)}"></div>
      <div class="tk-note" style="margin-top:12px">点击「生成审计快照」会向 Runtime 显式 POST，并持久化一条 append-only 审计快照；保存快照不改变任何业务对象状态。页面载入、角色切换与时间编辑都不会自动 POST。</div>
      <div class="tk-flex" style="margin-top:12px">
        <button type="button" class="tk-button tk-primary" data-action="generate" ${local.busy ? 'disabled' : ''}>${local.busy ? '正在生成 …' : '生成审计快照'}</button>
        ${local.busy ? '<span class="tk-spin" aria-hidden="true"></span>' : ''}
      </div>
      ${local.postError ? `<div style="margin-top:12px">${errorBox(describeError(local.postError).title, describeError(local.postError).detail)}</div>` : ''}
      <div class="tk-divider"></div>
      <h3>回读已保存快照</h3>
      <label class="tk-field" for="tk-snap-id" style="width:100%;margin-top:8px">快照 ID
        <span class="tk-flex" style="flex:1"><input type="text" class="tk-input" id="tk-snap-id" value="${esc(local.readbackId)}" placeholder="context_snapshot_id" spellcheck="false">
        <button type="button" class="tk-button" data-action="readback" ${local.busy ? 'disabled' : ''}>回读</button></span>
      </label>
      ${local.readbackError ? `<div style="margin-top:12px">${errorBox(describeError(local.readbackError).title, describeError(local.readbackError).detail)}</div>` : ''}
    </div>`);
  }

  function draw() {
    main.innerHTML = head('CONTEXT INSPECTOR', 'Context Pack',
      '选择对象与双时间截面，显式生成审计快照；或回读已保存快照。') +
      `<div class="tk-split"><div>${selectionPanel()}</div><div>${conditionsPanel()}</div></div>` +
      (local.snapshots.length
        ? local.snapshots.map((snap, i) => snapshotCard(snap, i)).join('')
        : `<div class="tk-section">${panel('快照结果', empty('尚未生成或回读快照。生成按钮是唯一写入口。'))}</div>`) +
      foot(ctx.config?.commit);
    updateConditionHints();
  }

  async function generate() {
    local.postError = null;
    let body;
    try {
      body = buildContextPackBody({
        objectIds: [...local.selected.keys()],
        validAt: local.validAt,
        knownAt: local.knownAt,
      });
    } catch (error) {
      local.postError = error;
      draw();
      return;
    }
    local.busy = true;
    draw();
    try {
      const data = await ctx.client.postJson('/v1/context-packs', body, { signal: ctx.signal });
      if (!ctx.isCurrent()) return;
      local.snapshots.unshift({ source: 'generated', data, requestIds: body.object_ids });
      ctx.announce('审计快照已生成并持久化');
    } catch (error) {
      if (!ctx.isCurrent()) return;
      local.postError = error;
    }
    local.busy = false;
    if (ctx.isCurrent()) draw();
  }

  async function readback() {
    local.readbackError = null;
    const id = (main.querySelector('#tk-snap-id')?.value || '').trim().toLowerCase();
    local.readbackId = id;
    if (!isUuid(id)) {
      local.readbackError = new Error('快照 ID 不是有效 UUID。');
      draw();
      return;
    }
    local.busy = true;
    draw();
    try {
      const data = await ctx.client.getJson(`/v1/context-packs/${id}`, { signal: ctx.signal });
      if (!ctx.isCurrent()) return;
      local.snapshots.unshift({ source: 'readback', data, requestIds: null });
      ctx.announce('快照回读完成，引用已按当前权限重新授权');
    } catch (error) {
      if (!ctx.isCurrent()) return;
      local.readbackError = error;
    }
    local.busy = false;
    if (ctx.isCurrent()) draw();
  }

  main.innerHTML = head('CONTEXT INSPECTOR', 'Context Pack', '正在读取当前身份的可读对象 …') + loading();
  try {
    await loadCandidates();
  } catch (error) {
    if (!ctx.isCurrent()) return;
    local.candidatesError = error;
  }
  if (!ctx.isCurrent()) return;
  draw();

  // 回读 hash 中携带的快照（GET 只读，允许自动执行）。
  if (route.snapshotId) {
    await readback();
    if (!ctx.isCurrent()) return;
  }

  main.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || !main.contains(button)) return;
    const action = button.dataset.action;
    if (action === 'generate' && !local.busy) {
      generate();
    } else if (action === 'readback' && !local.busy) {
      readback();
    } else if (action === 'remove-object') {
      local.selected.delete(button.dataset.value);
      draw();
    } else if (action === 'retry-candidates') {
      local.candidates = [];
      local.candidatesError = null;
      local.candidatesTruncated = false;
      draw();
      loadCandidates().then(() => {
        if (ctx.isCurrent()) draw();
      }).catch((error) => {
        if (!ctx.isCurrent()) return;
        local.candidatesError = error;
        draw();
      });
    } else if (action === 'add-object') {
      const value = (main.querySelector('#tk-add-id')?.value || '').trim().toLowerCase();
      if (!isUuid(value)) {
        local.addError = '输入不是有效 UUID。';
      } else if (local.selected.has(value)) {
        local.addError = '该对象已在选择列表中。';
      } else if (local.selected.size >= 100) {
        local.addError = '一次最多选择 100 个对象。';
      } else {
        local.selected.set(value, null);
        local.addError = '';
      }
      draw();
    }
  }, { signal: ctx.signal });
  main.addEventListener('change', (event) => {
    if (event.target.dataset.control === 'pick') {
      const id = event.target.value;
      if (event.target.checked) local.selected.set(id, null);
      else local.selected.delete(id);
      draw();
    }
  }, { signal: ctx.signal });
  // 时间编辑只更新本地值与条件提示：不重绘（保留焦点），绝不触发 POST。
  main.addEventListener('input', (event) => {
    if (event.target.id === 'tk-valid-at') local.validAt = event.target.value;
    else if (event.target.id === 'tk-known-at') local.knownAt = event.target.value;
    else if (event.target.id === 'tk-snap-id') local.readbackId = event.target.value;
    else return;
    updateConditionHints();
  }, { signal: ctx.signal });
}
