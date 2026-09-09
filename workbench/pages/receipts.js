// 页 03 动作与回执：对象关联 committed receipt 分页列表 + 单条回执详情。
// 只展示真实回执：授权拒绝不产生持久化 receipt，页面不虚构任何拒绝示例。
import { esc, panel, head, foot, tag, loading, empty, errorBox, idLine } from '../lib/html.js';
import { PagedLoader, ValueLoader } from '../lib/loaders.js';
import { buildQuery, casePathToRoute, isUuid } from '../lib/url.js';
import { describeError } from '../lib/api.js';
import { actionLabel, verificationLabel, verificationKind, fmtTime, shortId } from '../lib/format.js';

const PAGE_SIZE = 20;

// result 字段的通用展示：已知字段给中文标签，其余按原始 JSON 展示。
const RESULT_LABELS = {
  domain_id: '业务域',
  object_id: '目标对象',
  payload_hash: 'payload hash',
  acceptance_id: '评审记录',
  submission_seq: '提交序号',
  verification_result: '评审结果',
  deliverable_object_id: '交付物对象',
  deliverable_revision_id: '交付物 revision',
  referenced_object_ids: '关联对象',
  required_assignment_ids: '所需 assignment',
  revision_id: '产生的 revision',
  outcome_assessment_id: '成果评估',
};

function resultRows(result) {
  if (!result || typeof result !== 'object') return '<p class="tk-small tk-muted">无 result 内容。</p>';
  const rows = [];
  for (const [key, value] of Object.entries(result)) {
    const label = RESULT_LABELS[key] || key;
    if (value === null || value === undefined) {
      rows.push(`<dt>${esc(label)}</dt><dd>—</dd>`);
    } else if (key === 'verification_result') {
      rows.push(`<dt>${esc(label)}</dt><dd>${tag(verificationLabel(value).label, verificationKind(value))} <span class="tk-mono tk-muted">${esc(value)}</span></dd>`);
    } else if (key === 'object_id' || key === 'deliverable_object_id') {
      rows.push(`<dt>${esc(label)}</dt><dd><button type="button" class="tk-link" data-action="goto-object" data-value="${esc(value)}">${esc(value)} ↗</button></dd>`);
    } else if (key === 'referenced_object_ids' && Array.isArray(value)) {
      const links = value.map((id) => `<button type="button" class="tk-link" data-action="goto-object" data-value="${esc(id)}">${esc(shortId(id))}… ↗</button>`).join('<br>');
      rows.push(`<dt>${esc(label)}</dt><dd>${links}</dd>`);
    } else if (Array.isArray(value) || typeof value === 'object') {
      rows.push(`<dt>${esc(label)}</dt><dd><pre class="tk-pre">${esc(JSON.stringify(value, null, 2))}</pre></dd>`);
    } else if (typeof value === 'string' && (isUuid(value) || /^[0-9a-f]{64}$/.test(value))) {
      rows.push(`<dt>${esc(label)}</dt><dd>${idLine(value)}</dd>`);
    } else {
      rows.push(`<dt>${esc(label)}</dt><dd>${esc(String(value))}</dd>`);
    }
  }
  return `<dl class="tk-kv">${rows.join('')}</dl>`;
}

export async function renderReceipts(main, ctx, route) {
  const objectId = route.objectId || casePathToRoute(ctx.config?.state_paths?.['交付']).objectId;
  const local = {
    object: null,
    objectError: null,
  };
  const listLoader = new PagedLoader({
    pageSize: PAGE_SIZE,
    fetchPage: (params, signal) => ctx.client.getJson(`/v1/objects/${objectId}/action-receipts${buildQuery(params)}`, { signal }),
  });
  // 回执详情独立 epoch：连续点两条回执，旧响应不会覆盖新选中项。
  const detailLoader = new ValueLoader((receiptId, signal) =>
    ctx.client.getJson(`/v1/action-receipts/${receiptId}`, { signal }));

  if (!objectId) {
    main.innerHTML = head('ACTION LEDGER', '动作与回执', '缺少目标对象。') + errorBox('没有可展示的对象', 'case.json 的 state_paths 未提供交付锚点。');
    return;
  }

  async function loadObject() {
    try {
      local.object = await ctx.client.getJson(`/v1/objects/${objectId}`, { signal: ctx.signal });
    } catch (error) {
      if (ctx.isCurrent()) local.objectError = error;
    }
  }

  async function refreshList() {
    draw();
    const result = await listLoader.loadMore({}, ctx.signal);
    if (result.status === 'stale') return;
    if (result.status === 'applied' && !detailLoader.key && listLoader.items.length) {
      selectReceipt(listLoader.items[listLoader.items.length - 1].receipt_id);
      return;
    }
    if (ctx.isCurrent()) draw();
  }

  function selectReceipt(receiptId) {
    const pending = detailLoader.load(receiptId, ctx.signal);
    draw();
    pending.then((result) => {
      if (result.status === 'stale' || !ctx.isCurrent()) return;
      draw();
    });
  }

  function listPanel() {
    let body;
    if (listLoader.error) {
      const info = describeError(listLoader.error);
      body = `<div class="tk-pad">${errorBox(info.title, info.detail, info.retryable ? 'retry-list' : '')}</div>`;
    } else if (!listLoader.started) {
      body = loading();
    } else if (listLoader.isEmpty) {
      body = empty('该对象当前没有可读的 committed 回执。');
    } else {
      const items = [...listLoader.items].reverse();
      body = `<div class="tk-receiptlist">${items.map((r) => {
        const al = actionLabel(r.action_type);
        return `<button type="button" class="tk-receiptbutton" data-action="receipt-select" data-value="${esc(r.receipt_id)}" aria-pressed="${detailLoader.key === r.receipt_id}">
          <span>${esc(al.label)}</span>${tag('committed', 'good')}
          <small>${esc(fmtTime(r.recorded_at))} · actor ${esc(shortId(r.actor_id))}…</small>
          <small class="tk-mono">${esc(shortId(r.receipt_id))}… · ${esc(r.action_type)}</small>
        </button>`;
      }).join('')}</div>`;
    }
    const more = listLoader.hasMore
      ? `<div class="tk-pad" style="padding-top:10px"><button type="button" class="tk-button" data-action="load-more" ${listLoader.loading ? 'disabled' : ''}>${listLoader.loading ? '加载中 …' : '加载更多'}</button></div>`
      : '';
    const count = listLoader.started && !listLoader.error ? tag(`已读 ${listLoader.items.length} 条`, 'info') : '';
    return panel('committed 回执', body + more, count);
  }

  function detailPanel() {
    if (detailLoader.error) {
      const info = describeError(detailLoader.error);
      return panel('回执详情', `<div class="tk-pad">${errorBox(info.title, info.detail, info.retryable ? 'retry-detail' : '')}</div>`);
    }
    if (detailLoader.loading || !detailLoader.value) return panel('回执详情', loading());
    const r = detailLoader.value.receipt;
    const al = actionLabel(r.action_type);
    const versions = (r.object_versions || []).map((v) =>
      `<tr><td><button type="button" class="tk-link" data-action="goto-object" data-value="${esc(v.object_id)}">${esc(shortId(v.object_id))}… ↗</button></td><td class="tk-mono">${esc(v.object_version)}</td></tr>`,
    ).join('');
    const changesNote = r.result?.verification_result === 'changes_requested'
      ? '<div class="tk-note" style="margin-top:12px">changes_requested 是成功提交的业务评审结果（退回补充），不是授权拒绝；授权拒绝不会产生持久化回执。</div>'
      : '';
    return panel('回执详情', `<div class="tk-pad">
      <div class="tk-mono tk-muted">${esc(fmtTime(r.recorded_at))}</div>
      <h2 style="margin-top:8px">${esc(al.label)}</h2>
      <p class="tk-small tk-muted" style="margin-top:4px">原始动作类型：<span class="tk-mono">${esc(r.action_type)}</span></p>
      <div class="tk-divider"></div>
      <dl class="tk-kv">
        <dt>回执 ID</dt><dd>${idLine(r.receipt_id)}</dd>
        <dt>执行 actor</dt><dd>${idLine(r.actor_id)}</dd>
        <dt>状态</dt><dd>${tag(r.status === 'committed' ? '已提交 committed' : r.status, 'good')}</dd>
        <dt>auth epoch</dt><dd>${esc(r.auth_epoch ?? '—')}</dd>
      </dl>
      <div class="tk-divider"></div>
      <h3>服务端结果</h3>
      <div style="margin-top:10px">${resultRows(r.result)}</div>
      ${versions ? `<div class="tk-divider"></div><h3>涉及对象版本</h3><table class="tk-table" style="margin-top:8px"><thead><tr><th>对象</th><th style="width:30%">写入后对象版本</th></tr></thead><tbody>${versions}</tbody></table>` : ''}
      ${changesNote}
    </div>`);
  }

  function draw() {
    const title = local.object?.latest_revision?.payload?.title || `对象 ${shortId(objectId)}…`;
    const objectNote = local.objectError
      ? `<p class="tk-small tk-muted">对象详情读取失败（${esc(describeError(local.objectError).title)}），回执列表不受影响。</p>`
      : '';
    main.innerHTML = head('ACTION LEDGER', '动作与回执',
      `对象「${title}」的 committed 回执。谁在什么时间，对哪一次提交作了什么判断。`) +
      objectNote +
      `<div class="tk-split"><section class="tk-panel">${listPanel()}</section><div>${detailPanel()}</div></div>` +
      `<div class="tk-section">${panel('审计展示约定', '<div class="tk-pad"><div class="tk-small tk-muted">仅展示服务端持久化的 committed 回执；被授权拒绝的请求不会留下回执，本页不虚构拒绝示例。每条回执按当前身份重新授权，含不可读引用的回执整条隐藏。</div></div>')}</div>` +
      foot(ctx.config?.commit);
  }

  main.innerHTML = head('ACTION LEDGER', '动作与回执', '正在读取回执 …') + loading();
  await loadObject();
  if (!ctx.isCurrent()) return;
  draw();

  main.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || !main.contains(button)) return;
    const action = button.dataset.action;
    if (action === 'receipt-select') {
      selectReceipt(button.dataset.value);
    } else if (action === 'load-more') {
      refreshList();
    } else if (action === 'retry-list') {
      listLoader.reset();
      refreshList();
    } else if (action === 'retry-detail' && detailLoader.key) {
      selectReceipt(detailLoader.key);
    } else if (action === 'goto-object') {
      ctx.navigate({ page: 'instance', objectId: button.dataset.value });
    }
  }, { signal: ctx.signal });

  if (route.receiptId) {
    selectReceipt(route.receiptId);
    refreshList();
  } else {
    await refreshList();
  }
}
