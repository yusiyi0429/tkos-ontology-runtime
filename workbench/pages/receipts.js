// 页 03 动作与回执：页首当前对象标题 + 返回实例入口；左侧动作历史、右侧详情。
// 详情先突出动作/时间/业务评审结果/提交序号；完整 receiptID/actorID/auth_epoch/result
// 原文与对象版本收进「审计字段」details。只展示真实回执：授权拒绝不产生持久化 receipt。
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

// 详情区状态：列表已确定无回执时给清晰空态，绝不无限 loading。
export function receiptDetailState({ detailError, detailLoading, hasDetail, listEmpty, listSettled }) {
  if (detailError) return 'error';
  if (hasDetail) return 'detail';
  if (detailLoading) return 'loading';
  if (listEmpty) return 'empty';
  if (listSettled) return 'pick';
  return 'loading';
}

export async function renderReceipts(main, ctx, route) {
  const objectId = route.objectId || casePathToRoute(ctx.config?.state_paths?.['交付']).objectId;
  const local = {
    object: null,
    objectError: null,
    openDetails: new Set(),
  };
  const listLoader = new PagedLoader({
    pageSize: PAGE_SIZE,
    fetchPage: (params, signal) => ctx.client.getJson(`/v1/objects/${objectId}/action-receipts${buildQuery(params)}`, { signal }),
  });
  // 回执详情独立 epoch：连续点两条回执，旧响应不会覆盖新选中项。
  const detailLoader = new ValueLoader((receiptId, signal) =>
    ctx.client.getJson(`/v1/action-receipts/${receiptId}`, { signal }));

  if (!objectId) {
    main.innerHTML = head('动作与回执', '缺少目标对象。') + errorBox('没有可展示的对象', 'case.json 的 state_paths 未提供交付锚点。');
    return;
  }

  async function loadObject() {
    try {
      local.object = await ctx.client.getJson(`/v1/objects/${objectId}`, { signal: ctx.signal });
      // 授权读取成功后回填上下文条标题/类型。
      ctx.reportObject?.(objectId, {
        title: local.object.latest_revision?.payload?.title || null,
        objectType: local.object.object_type,
      });
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
    return panel('动作历史', body + more, count);
  }

  function detailPanel() {
    if (listLoader.error && !detailLoader.key) {
      return panel('回执详情', empty('动作历史未能载入，请先处理左侧提示或重试。'));
    }
    const state = receiptDetailState({
      detailError: Boolean(detailLoader.error),
      detailLoading: detailLoader.loading,
      hasDetail: Boolean(detailLoader.value),
      listEmpty: listLoader.isEmpty,
      listSettled: listLoader.started && !listLoader.loading && !listLoader.error,
    });
    if (state === 'error') {
      const info = describeError(detailLoader.error);
      return panel('回执详情', `<div class="tk-pad">${errorBox(info.title, info.detail, info.retryable ? 'retry-detail' : '')}</div>`);
    }
    if (state === 'loading') return panel('回执详情', loading());
    if (state === 'empty') return panel('回执详情', empty('该对象没有可读回执。授权拒绝不产生持久化回执，页面不虚构拒绝示例。'));
    if (state === 'pick') return panel('回执详情', empty('从左侧动作历史选择一条回执查看详情。'));
    const r = detailLoader.value.receipt;
    const al = actionLabel(r.action_type);
    const vr = r.result?.verification_result;
    const versions = (r.object_versions || []).map((v) =>
      `<tr><td><button type="button" class="tk-link" data-action="goto-object" data-value="${esc(v.object_id)}">${esc(shortId(v.object_id))}… ↗</button></td><td class="tk-mono">${esc(v.object_version)}</td></tr>`,
    ).join('');
    // 退回补充是业务评审结果，不是权限拒绝；短提示即可。
    const changesNote = vr === 'changes_requested'
      ? '<p class="tk-tiny tk-muted" style="margin-top:8px">退回补充（changes_requested）是成功提交的业务评审结果，不是授权拒绝；授权拒绝不会产生持久化回执。</p>'
      : '';
    return panel('回执详情', `<div class="tk-pad">
      <div class="tk-between"><h2>${esc(al.label)}</h2>${vr ? tag(verificationLabel(vr).label, verificationKind(vr)) : ''}</div>
      <p class="tk-tiny tk-muted" style="margin-top:4px">${esc(fmtTime(r.recorded_at))} · <span class="tk-mono">${esc(r.action_type)}</span></p>
      <dl class="tk-kv" style="margin-top:12px">
        ${r.result?.submission_seq !== null && r.result?.submission_seq !== undefined ? `<dt>提交序号</dt><dd>v${esc(r.result.submission_seq)}</dd>` : ''}
        ${vr ? `<dt>业务评审结果</dt><dd>${tag(verificationLabel(vr).label, verificationKind(vr))} <span class="tk-mono tk-muted">${esc(vr)}</span></dd>` : ''}
        <dt>状态</dt><dd>${tag(r.status === 'committed' ? '已提交 committed' : r.status, 'good')}</dd>
      </dl>
      ${changesNote}
      <details class="tk-details" data-detail-key="audit"${local.openDetails.has('audit') ? ' open' : ''} style="margin-top:12px">
        <summary>审计字段</summary>
        <div class="tk-detailsbody"><dl class="tk-kv">
          <dt>回执 ID</dt><dd>${idLine(r.receipt_id)}</dd>
          <dt>执行 actor</dt><dd>${idLine(r.actor_id)}</dd>
          <dt>auth epoch</dt><dd>${esc(r.auth_epoch ?? '—')}</dd>
        </dl>
        <div class="tk-divider"></div>
        <h3>result 原文</h3>
        <div style="margin-top:10px">${resultRows(r.result)}</div>
        ${versions ? `<div class="tk-divider"></div><h3>涉及对象版本</h3><table class="tk-table" style="margin-top:8px"><thead><tr><th>对象</th><th style="width:30%">写入后对象版本</th></tr></thead><tbody>${versions}</tbody></table>` : ''}
        </div>
      </details>
    </div>`);
  }

  function draw() {
    const title = local.object?.latest_revision?.payload?.title || `对象 ${shortId(objectId)}…`;
    const objectNote = local.objectError
      ? `<p class="tk-small tk-muted">对象详情读取失败（${esc(describeError(local.objectError).title)}），回执列表不受影响。</p>`
      : '';
    main.innerHTML = head('动作与回执',
      `当前对象：${title} · 谁在什么时间，对哪一次提交作了什么判断。`,
      `<button type="button" class="tk-button" data-action="back-instance">← 返回实例</button>`) +
      objectNote +
      `<div class="tk-receiptgrid"><div>${listPanel()}</div><div>${detailPanel()}</div></div>` +
      `<p class="tk-tiny tk-muted" style="margin-top:14px">仅展示服务端持久化的 committed 回执；每条回执按当前身份重新授权，含不可读引用的回执整条隐藏。</p>` +
      foot(ctx.config?.commit);
  }

  main.innerHTML = head('动作与回执', '正在读取回执 …') + loading();
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
    } else if (action === 'back-instance') {
      ctx.navigate({ page: 'instance', objectId, revisionId: ctx.focus?.objectId === objectId ? ctx.focus.revisionId : null });
    }
  }, { signal: ctx.signal });
  // details 展开状态在异步重绘间保持（capture：toggle 不冒泡）。
  main.addEventListener('toggle', (event) => {
    const detail = event.target.closest?.('details[data-detail-key]');
    if (!detail || !main.contains(detail)) return;
    if (detail.open) local.openDetails.add(detail.dataset.detailKey);
    else local.openDetails.delete(detail.dataset.detailKey);
  }, { capture: true, signal: ctx.signal });

  if (route.receiptId) {
    selectReceipt(route.receiptId);
    refreshList();
  } else {
    await refreshList();
  }
}
