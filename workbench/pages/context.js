// 页 04 记忆快照（Context Pack）：左侧输入栏（新建快照 / 回读快照 两模式，不同时混排），
// 右侧常驻结果栏（来源/时间/纳入排除在前，完整 ID/hash 收进「技术溯源」details）。
// 页面载入/角色切换/模式切换/时间编辑绝不自动 POST；保存快照不改变业务状态。
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

function selectedItemHtml(item, detailsAttr) {
  const techKey = `item-tech-${item.object_id}-${item.revision_id}`;
  const payloadKey = `item-payload-${item.object_id}-${item.revision_id}`;
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
    ${review}
    <details class="tk-details" data-detail-key="${esc(techKey)}"${detailsAttr(techKey)} style="margin-top:8px">
      <summary>技术溯源</summary>
      <div class="tk-detailsbody"><dl class="tk-kv">
        <dt>对象</dt><dd>${idLine(item.object_id)}</dd>
        <dt>revision</dt><dd>${idLine(item.revision_id)}</dd>
        <dt>payload hash</dt><dd>${idLine(item.payload_hash)}</dd>
      </dl>
      ${refs ? `<div style="margin-top:8px"><h3>source refs（冻结 revision 的内容来源）</h3>${refs}</div>` : ''}</div>
    </details>
    <details class="tk-details" data-detail-key="${esc(payloadKey)}"${detailsAttr(payloadKey)} style="margin-top:8px">
      <summary>payload 原文</summary>
      <div class="tk-detailsbody"><pre class="tk-pre">${esc(JSON.stringify(item.payload, null, 2))}</pre></div>
    </details>
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

export async function renderContext(main, ctx, route) {
  const anchors = anchorEntries(ctx.config);
  const local = {
    mode: route.snapshotId ? 'read' : 'create',
    selected: new Map(anchors.map((a) => [a.objectId, a.label])),
    candidates: [],
    candidatesError: null,
    candidatesTruncated: false,
    busy: false,
    postError: null,
    snapshots: [],
    activeSnapshot: 0,
    readbackId: route.snapshotId || '',
    readbackError: null,
    addError: '',
    addId: '',
    validAt: toLocalInputValue(),
    knownAt: toLocalInputValue(),
    openDetails: new Set(),
  };

  function detailsAttr(key) {
    return local.openDetails.has(key) ? ' open' : '';
  }

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
      if (local.mode === 'read') {
        el.innerHTML = '<p class="tk-tiny tk-muted" style="margin-bottom:12px">以下内容按这份已保存快照的时间与对象范围呈现。</p>';
        return;
      }
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

  // 新建模式：选对象 → 双时间 → 生成按钮；手动 UUID 默认折叠。
  function createForm() {
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
    return `
      <h3>1 · 选择对象</h3>
      <div class="tk-chiplist" style="margin-top:8px">${chips || '<span class="tk-small tk-muted">尚未选择对象。</span>'}</div>
      <div style="margin-top:10px;max-height:240px;overflow:auto">${candidateBody}</div>
      <details class="tk-details" data-detail-key="manual-add"${detailsAttr('manual-add')} style="margin-top:10px">
        <summary>手动输入对象 UUID</summary>
        <div class="tk-detailsbody">
          <label class="tk-field" for="tk-add-id" style="width:100%">对象 UUID
            <span class="tk-flex" style="flex:1"><input type="text" class="tk-input" id="tk-add-id" value="${esc(local.addId)}" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" spellcheck="false">
            <button type="button" class="tk-button" data-action="add-object">添加</button></span>
          </label>
          ${local.addError ? `<p class="tk-tiny" style="color:var(--tk-red);margin-top:6px">${esc(local.addError)}</p>` : ''}
          <p class="tk-tiny tk-muted" style="margin-top:8px">默认锚点来自演练配置的三项独立状态对象；添加的对象按当前身份逐个授权。</p>
        </div>
      </details>
      <div class="tk-divider"></div>
      <h3>2 · 双时间截面</h3>
      <div class="tk-formrow" style="margin-top:6px"><label for="tk-valid-at">业务有效时间 <span class="tk-tiny tk-mono">valid_at</span></label><input type="datetime-local" step="0.001" class="tk-input" id="tk-valid-at" value="${esc(local.validAt)}"></div>
      <div class="tk-formrow"><label for="tk-known-at">系统知悉时间 <span class="tk-tiny tk-mono">known_at</span></label><input type="datetime-local" step="0.001" class="tk-input" id="tk-known-at" value="${esc(local.knownAt)}"></div>
      <div class="tk-note" style="margin-top:12px">保存一份可追溯的审计快照，不改变交付、Outcome 或 MF 的状态。</div>
      <div class="tk-flex" style="margin-top:12px">
        <button type="button" class="tk-button tk-primary" data-action="generate" ${local.busy ? 'disabled' : ''}>${local.busy ? '正在生成 …' : '生成审计快照'}</button>
        ${local.busy ? '<span class="tk-spin" aria-hidden="true"></span>' : ''}
      </div>
      ${local.postError ? `<div style="margin-top:12px">${errorBox(describeError(local.postError).title, describeError(local.postError).detail)}</div>` : ''}`;
  }

  // 回读模式：快照 ID + 回读按钮（GET 只读，按当前身份重新授权）。
  function readForm() {
    return `
      <h3>回读已保存快照</h3>
      <label class="tk-field" for="tk-snap-id" style="width:100%;margin-top:10px">快照 ID
        <span class="tk-flex" style="flex:1"><input type="text" class="tk-input" id="tk-snap-id" value="${esc(local.readbackId)}" placeholder="context_snapshot_id" spellcheck="false">
        <button type="button" class="tk-button" data-action="readback" ${local.busy ? 'disabled' : ''}>回读</button></span>
      </label>
      <p class="tk-tiny tk-muted" style="margin-top:8px">回读为 GET 只读；快照内容按当前身份重新授权，不触发任何写入。</p>
      ${local.readbackError ? `<div style="margin-top:12px">${errorBox(describeError(local.readbackError).title, describeError(local.readbackError).detail)}</div>` : ''}`;
  }

  function inputPanel() {
    const tabs = [['create', '新建快照'], ['read', '回读快照']].map(([key, label]) =>
      `<button type="button" class="tk-tabbutton" data-action="mode" data-value="${key}" aria-pressed="${local.mode === key}">${label}</button>`,
    ).join('');
    return panel('快照操作', `<div class="tk-pad">
      <div class="tk-tabbar" aria-label="快照操作模式" style="margin-bottom:14px">${tabs}</div>
      ${local.mode === 'create' ? createForm() : readForm()}
    </div>`, local.mode === 'create' ? tag(`已选 ${local.selected.size} 个`, 'info') : '');
  }

  function snapshotCard(snap, index) {
    const data = snap.data;
    const selected = (data.selected || []).map((item) => selectedItemHtml(item, detailsAttr)).join('');
    const excluded = (data.excluded || []).map((item) =>
      `<div class="tk-disabledrow"><span class="tk-mono">${esc(item.object_id || '')}</span>${tag(item.reason || '未选入', 'wait')}</div>`,
    ).join('');
    const techKey = `snap-tech-${data.context_snapshot_id || index}`;
    return `<div class="tk-section">${panel(
      `快照 · 纳入 ${(data.selected || []).length} · 排除 ${(data.excluded || []).length}`,
      `<div class="tk-pad">
        <div class="tk-condnote" data-snap-index="${index}"></div>
        <dl class="tk-snapmeta">
          <dt>来源</dt><dd>${snap.source === 'generated' ? '本次生成 · POST 已持久化' : 'GET 回读 · 按当前权限重新授权'}</dd>
          <dt>生成/记录时间</dt><dd class="tk-mono">${esc(data.recorded_at || '—')}</dd>
          <dt>业务有效时间</dt><dd class="tk-mono">${esc(data.valid_at || '—')}</dd>
          <dt>系统知悉时间</dt><dd class="tk-mono">${esc(data.known_at || '—')}</dd>
        </dl>
        <details class="tk-details" data-detail-key="${esc(techKey)}"${detailsAttr(techKey)} style="margin-top:12px">
          <summary>技术溯源</summary>
          <div class="tk-detailsbody"><dl class="tk-kv">
            <dt>快照 ID</dt><dd>${idLine(data.context_snapshot_id)}</dd>
          </dl></div>
        </details>
        <div class="tk-divider"></div>
        <h3>选中对象（${(data.selected || []).length}）</h3>
        <div class="tk-packlist" style="margin-top:8px">${selected || '<p class="tk-small tk-muted" style="padding:12px 0">无选中对象。</p>'}</div>
        <div class="tk-divider"></div>
        <h3>未选入（${(data.excluded || []).length}）<span class="tk-tiny tk-muted"> · 排除依据见右侧标注</span></h3>
        ${excluded || '<p class="tk-small tk-muted" style="margin-top:8px">无排除对象。</p>'}
        <p class="tk-tiny tk-muted" style="margin-top:10px">响应不含聚合 snapshot hash；逐 revision 的 payload_hash 见各对象「技术溯源」。时间显示为服务端原始 ISO 值。快照内容为历史选择结果，回读时按当前权限重新授权。</p>
      </div>`,
    )}</div>`;
  }

  function resultsColumn() {
    if (!local.snapshots.length) {
      return panel('快照结果', empty('尚未生成或回读快照。「生成审计快照」是唯一写入口。'));
    }
    const selector = local.snapshots.length > 1
      ? `<label class="tk-field tk-snapshot-picker" for="tk-snapshot-choice">本次浏览的快照<select class="tk-select" id="tk-snapshot-choice" data-control="snapshot">${local.snapshots.map((snap, i) => `<option value="${i}" ${i === local.activeSnapshot ? 'selected' : ''}>${snap.source === 'generated' ? '新建' : '回读'} · ${esc(snap.data.recorded_at || shortId(snap.data.context_snapshot_id))}</option>`).join('')}</select></label>` : '';
    return selector + snapshotCard(local.snapshots[local.activeSnapshot], local.activeSnapshot);
  }

  function draw() {
    main.innerHTML = head('记忆快照（Context Pack）',
      '保存指定对象在两个时间条件下的可追溯快照，也可回看已保存结果。') +
      `<div class="tk-contextgrid"><div>${inputPanel()}</div><div>${resultsColumn()}</div></div>` +
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
      local.activeSnapshot = 0;
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
      local.activeSnapshot = 0;
      ctx.announce('快照回读完成，引用已按当前权限重新授权');
    } catch (error) {
      if (!ctx.isCurrent()) return;
      local.readbackError = error;
    }
    local.busy = false;
    if (ctx.isCurrent()) draw();
  }

  main.innerHTML = head('记忆快照（Context Pack）', '正在读取当前身份的可读对象 …') + loading();
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
    if (action === 'mode') {
      // 模式切换只是展示层变化，绝不触发 POST/GET。
      local.mode = button.dataset.value;
      draw();
    } else if (action === 'generate' && !local.busy) {
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
        local.selected.set(value, local.candidates.find((o) => o.object_id === value)?.title || null);
        local.addError = '';
        local.addId = '';
      }
      draw();
    }
  }, { signal: ctx.signal });
  main.addEventListener('change', (event) => {
    if (event.target.dataset.control === 'pick') {
      const id = event.target.value;
      if (event.target.checked) local.selected.set(id, local.candidates.find((o) => o.object_id === id)?.title || null);
      else local.selected.delete(id);
      draw();
    } else if (event.target.dataset.control === 'snapshot') {
      const index = Number(event.target.value);
      if (Number.isInteger(index) && local.snapshots[index]) {
        local.activeSnapshot = index;
        draw();
      }
    }
  }, { signal: ctx.signal });
  // details 展开状态在异步重绘间保持（capture：toggle 不冒泡）。
  main.addEventListener('toggle', (event) => {
    const detail = event.target.closest?.('details[data-detail-key]');
    if (!detail || !main.contains(detail)) return;
    if (detail.open) local.openDetails.add(detail.dataset.detailKey);
    else local.openDetails.delete(detail.dataset.detailKey);
  }, { capture: true, signal: ctx.signal });
  // 时间编辑只更新本地值与条件提示：不重绘（保留焦点），绝不触发 POST。
  main.addEventListener('input', (event) => {
    if (event.target.id === 'tk-valid-at') local.validAt = event.target.value;
    else if (event.target.id === 'tk-known-at') local.knownAt = event.target.value;
    else if (event.target.id === 'tk-snap-id') local.readbackId = event.target.value;
    else if (event.target.id === 'tk-add-id') local.addId = event.target.value;
    else return;
    updateConditionHints();
  }, { signal: ctx.signal });
}
