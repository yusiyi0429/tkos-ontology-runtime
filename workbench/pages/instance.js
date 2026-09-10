// 页 02 实例详情：页首为对象标题/类型/生命周期 + 回执入口；「概览 / 版本与内容 / 来源关系」
// 本页页签。三状态带只在所选对象==演练 WI 锚点时显示（Outcome 只读 outcome_achievement）。
// 完整 ID/hash/assignment/baseline 与版本指针收进默认收起的 details；异步重绘保持展开状态。
import { esc, panel, head, foot, tag, loading, empty, errorBox, idLine } from '../lib/html.js';
import { PagedLoader, ValueLoader } from '../lib/loaders.js';
import { buildQuery, casePathToRoute } from '../lib/url.js';
import { describeError } from '../lib/api.js';
import { payloadRows } from '../lib/payloadview.js';
import { buildDeliveryTimeline } from '../lib/timeline.js';
import {
  lifecycleLabel, lifecycleKind, verificationLabel, verificationKind,
  criterionLabel, criterionKind, outcomeAchievementLabel, outcomeAchievementKind,
  fmtTime, shortId,
} from '../lib/format.js';

const TEXT_PREVIEW_LIMIT = 64 * 1024;
const ANCHOR_TITLES = { 交付: '交付验收', Outcome: 'Outcome 达成', MF: 'MF 关闭' };
const TABS = [['overview', '概览'], ['version', '版本与内容'], ['relations', '来源关系']];

function anchorId(config, key) {
  return casePathToRoute(config?.state_paths?.[key]).objectId || null;
}

// 初始页签：显式 ?rev 深链接直接落在版本页；WorkItem 默认概览，其它类型默认对象内容。
export function initialInstanceTab({ objectType, explicitRevision }) {
  if (explicitRevision) return 'version';
  return objectType === 'WorkItem' ? 'overview' : 'version';
}

// 三项独立状态：分别读各自对象当前属性；Outcome 读 outcome_achievement，缺失即未提供。
function anchorValue(key, data) {
  if (key === 'Outcome') {
    const raw = data?.outcome_achievement;
    if (raw === null || raw === undefined) return { label: '未提供', raw: '—', kind: '' };
    const t = outcomeAchievementLabel(raw);
    return { label: t.label, raw, kind: outcomeAchievementKind(raw) };
  }
  const raw = data?.lifecycle_status;
  const t = lifecycleLabel(raw);
  return { label: t.label, raw, kind: lifecycleKind(raw) };
}

function statusCell(key, slot) {
  const title = ANCHOR_TITLES[key] || key;
  if (slot?.error) {
    const info = describeError(slot.error);
    return `<div class="tk-status"><span class="tk-small tk-muted">${esc(title)}</span><strong>不可读</strong><span class="tk-tiny tk-muted">${esc(info.title)}</span></div>`;
  }
  if (!slot?.data) {
    return `<div class="tk-status"><span class="tk-small tk-muted">${esc(title)}</span><strong>—</strong><span class="tk-tiny tk-muted">读取中 …</span></div>`;
  }
  const value = anchorValue(key, slot.data);
  return `<div class="tk-status"><span class="tk-small tk-muted">${esc(title)}</span>` +
    `<strong>${tag(value.label, value.kind)}</strong>` +
    `<span class="tk-tiny tk-muted">原始值：${esc(value.raw)}</span>` +
    `<button type="button" class="tk-link" data-action="goto-object" data-value="${esc(slot.data.object_id)}">查看对象 →</button></div>`;
}

function payloadBlock(payload) {
  const rows = payloadRows(payload).map((row) => {
    if (row.kind === 'ref') {
      return `<dt>${esc(row.label)}</dt><dd><button type="button" class="tk-link" data-action="open-ref" data-object="${esc(row.ref.object_id)}" data-revision="${esc(row.ref.revision_id)}">${esc(shortId(row.ref.object_id))}… @ ${esc(shortId(row.ref.revision_id))}… ↗</button></dd>`;
    }
    if (row.kind === 'reflist') {
      const links = row.refs.map((ref) =>
        `<button type="button" class="tk-link" data-action="open-ref" data-object="${esc(ref.object_id)}" data-revision="${esc(ref.revision_id)}">${esc(shortId(ref.object_id))}… @ ${esc(shortId(ref.revision_id))}… ↗</button>`,
      ).join('<br>');
      return `<dt>${esc(row.label)}</dt><dd>${links}</dd>`;
    }
    if (row.kind === 'criteria') {
      const items = row.criteria.map((c) =>
        `<div class="tk-criterion"><div><span class="tk-criterionkey">${esc(c.criterion_id)}</span>${esc(c.description || '')}</div></div>`,
      ).join('');
      return `<dt>${esc(row.label)}</dt><dd>${items}</dd>`;
    }
    if (row.kind === 'json') {
      return `<dt>${esc(row.label)}</dt><dd><pre class="tk-pre">${esc(row.text)}</pre></dd>`;
    }
    return `<dt>${esc(row.label)}</dt><dd>${esc(row.text)}</dd>`;
  }).join('');
  return `<dl class="tk-kv">${rows}</dl>`;
}

// 评审块：实际比较 acceptance.payload_hash 与对应 submission payload_hash，不伪造校验结论。
function acceptanceBlock(acceptance, submission) {
  const vr = verificationLabel(acceptance.verification_result);
  const criteria = (acceptance.criterion_results || []).map((c) =>
    `<div class="tk-criterion"><div><span class="tk-criterionkey">${esc(c.criterion_id)}</span>${esc(c.note || '')}</div>${tag(criterionLabel(c.result).label, criterionKind(c.result))}</div>`,
  ).join('');
  let hashLine;
  if (acceptance.payload_hash && submission?.payload_hash) {
    const match = acceptance.payload_hash === submission.payload_hash;
    hashLine = `<p class="tk-tiny tk-muted" style="margin-top:8px">评审针对精确 Deliverable revision ${esc(shortId(acceptance.deliverable_revision_id))}… · 评审 payload hash 与提交 ${match ? '一致' : '不一致'} ${tag(match ? 'hash 匹配' : 'hash 不匹配', match ? 'good' : 'fail')}</p>`;
  } else {
    hashLine = `<p class="tk-tiny tk-muted" style="margin-top:8px">评审针对精确 Deliverable revision ${esc(shortId(acceptance.deliverable_revision_id))}…</p>${acceptance.payload_hash ? `<p class="tk-tiny tk-muted">评审 payload hash：${esc(acceptance.payload_hash)}</p>` : ''}`;
  }
  return `<div style="margin-top:12px">
    <div class="tk-between"><h3>评审结果 · 提交 v${esc(acceptance.submission_seq)}</h3>${tag(vr.label, verificationKind(acceptance.verification_result))}</div>
    <p class="tk-tiny tk-muted" style="margin-top:4px">原始结果：${esc(acceptance.verification_result)} · ${esc(fmtTime(acceptance.recorded_at))}</p>
    ${acceptance.review_note ? `<div class="tk-note" style="margin-top:10px">${esc(acceptance.review_note)}</div>` : ''}
    ${criteria ? `<div style="margin-top:6px">${criteria}</div>` : ''}
    ${hashLine}
  </div>`;
}

export async function renderInstance(main, ctx, route) {
  const objectId = route.objectId || anchorId(ctx.config, '交付');
  // 三状态带仅在所选对象==演练 WI 锚点时显示；其它对象不出现无关演练状态。
  const isAnchorWi = objectId && objectId === anchorId(ctx.config, '交付');
  const local = {
    object: null,
    selectedRevisionId: route.revisionId || null,
    selectedRevision: null,
    responsibility: null,
    responsibilityError: null,
    submissionSeq: null,
    anchors: {},
    tab: 'overview',
    openDetails: new Set(),
  };
  const revisionLoader = new PagedLoader({
    pageSize: 20,
    fetchPage: (params, signal) => ctx.client.getJson(`/v1/objects/${objectId}/revisions${buildQuery(params)}`, { signal }),
  });
  const relationLoader = new PagedLoader({
    pageSize: 50,
    fetchPage: (params, signal) => ctx.client.getJson(`/v1/objects/${objectId}/relations${buildQuery(params)}`, { signal }),
  });
  const evidenceLoader = new ValueLoader((key, signal) => {
    const [evObjectId, evRevisionId] = key.split(':');
    return ctx.client.getBytes(`/v1/evidence-assets/${evObjectId}/revisions/${evRevisionId}`, { signal });
  });

  if (!objectId) {
    main.innerHTML = head('实例详情', '演练配置缺少默认锚点。') + errorBox('没有可展示的默认对象', 'case.json 的 state_paths 未提供交付锚点。');
    return;
  }

  async function fetchAnchors() {
    await Promise.all(Object.entries(ctx.config?.state_paths || {}).map(async ([key, path]) => {
      const r = casePathToRoute(path);
      if (!r.objectId) { local.anchors[key] = { error: new Error('路径无法解析') }; return; }
      try {
        local.anchors[key] = { data: await ctx.client.getJson(`/v1/objects/${r.objectId}`, { signal: ctx.signal }) };
      } catch (error) {
        local.anchors[key] = { error };
      }
    }));
  }

  async function fetchSelectedRevision() {
    const rid = local.selectedRevisionId;
    if (!rid) { local.selectedRevision = null; return; }
    if (local.object.latest_revision?.revision_id === rid) { local.selectedRevision = local.object.latest_revision; return; }
    if (local.object.effective_revision?.revision_id === rid) { local.selectedRevision = local.object.effective_revision; return; }
    local.selectedRevision = await ctx.client.getJson(`/v1/objects/${objectId}/revisions/${rid}`, { signal: ctx.signal });
  }

  function detailsAttr(key) {
    return local.openDetails.has(key) ? ' open' : '';
  }

  function statusBand() {
    const entries = Object.entries(ctx.config?.state_paths || {});
    if (!entries.length) return '';
    const cells = entries.map(([key]) => statusCell(key, local.anchors[key])).join('');
    return `<div class="tk-statusgrid">${cells}</div>` +
      `<p class="tk-tiny tk-muted" style="margin:-8px 0 16px">本演练闭环当前状态：三项分别读取锚点对象的当前属性（Outcome 读 outcome_achievement），互不推断联动。</p>`;
  }

  function tabBar() {
    const buttons = TABS.map(([key, label]) =>
      `<button type="button" class="tk-tabbutton" data-action="tab" data-value="${key}" aria-pressed="${local.tab === key}">${label}</button>`,
    ).join('');
    return `<div class="tk-tabbar tk-pagetabs" aria-label="实例信息页签">${buttons}</div>`;
  }

  function timelinePanel() {
    const nodes = buildDeliveryTimeline(local.object.delivery);
    if (!nodes.length) return '';
    const items = nodes.map((node) => {
      const inner = node.receiptId
        ? `<button type="button" data-action="goto-receipt" data-value="${esc(node.receiptId)}">${esc(node.label)} ↗</button>`
        : `<button type="button" data-action="goto-receipts">${esc(node.label)} ↗</button>`;
      return `<li><span class="tk-point"></span>${inner}<small>${esc(fmtTime(node.at))}</small></li>`;
    }).join('');
    return panel('交付轨迹', `<ol class="tk-timeline">${items}</ol>`, tag('节点跳回执', 'info'));
  }

  function revisionSelector() {
    const items = revisionLoader.items;
    const known = new Set(items.map((r) => r.revision_id));
    const fallback = local.selectedRevisionId && !known.has(local.selectedRevisionId)
      ? `<option value="${esc(local.selectedRevisionId)}" selected>hash 指定 revision · ${esc(shortId(local.selectedRevisionId))}…</option>`
      : '';
    const options = fallback + items.map((rev) => {
      const marks = [rev.is_latest ? '最新' : '', rev.is_effective ? '生效' : ''].filter(Boolean).join(' · ');
      return `<option value="${esc(rev.revision_id)}" ${rev.revision_id === local.selectedRevisionId ? 'selected' : ''}>${esc(fmtTime(rev.recorded_at))}${marks ? ` · ${marks}` : ''} · ${esc(shortId(rev.revision_id))}…</option>`;
    }).join('');
    const more = revisionLoader.hasMore
      ? `<button type="button" class="tk-button" data-action="more-revisions" ${revisionLoader.loading ? 'disabled' : ''}>${revisionLoader.loading ? '载入中 …' : '载入更早 revision'}</button>` : '';
    return `<div class="tk-flex"><label class="tk-field" for="tk-revision">revision<select class="tk-select" id="tk-revision" data-control="revision">${options}</select></label>${more}</div>`;
  }

  // 版本指针：latest/effective/object_version 折叠进 details。
  function pointersDetails() {
    const o = local.object;
    return `<details class="tk-details" data-detail-key="pointers"${detailsAttr('pointers')}>
      <summary>版本指针</summary>
      <div class="tk-detailsbody"><dl class="tk-kv">
        <dt>对象版本</dt><dd>${esc(o.object_version)}</dd>
        <dt>最新候选</dt><dd>${idLine(o.latest_revision_id)}</dd>
        <dt>当前生效</dt><dd>${idLine(o.effective_revision_id)}</dd>
        <dt>创建时间</dt><dd>${esc(fmtTime(o.created_at))}</dd>
        <dt>更新时间</dt><dd>${esc(fmtTime(o.updated_at))}</dd>
      </dl>
      <p class="tk-tiny tk-muted" style="margin-top:10px">latest 与 effective 可能指向不同 revision；候选出现不代表生效。</p></div>
    </details>`;
  }

  // 当前 revision 的完整 ID/hash/时间区间，折叠进 details。
  function revisionProvenanceDetails(rev) {
    return `<details class="tk-details" data-detail-key="prov-revision"${detailsAttr('prov-revision')}>
      <summary>溯源信息 · 当前 revision</summary>
      <div class="tk-detailsbody"><dl class="tk-kv">
        <dt>对象 ID</dt><dd>${idLine(objectId)}</dd>
        <dt>revision</dt><dd>${idLine(rev.revision_id)}</dd>
        <dt>payload hash</dt><dd>${idLine(rev.payload_hash)}</dd>
        <dt>记录时间</dt><dd>${esc(fmtTime(rev.recorded_at))}</dd>
        <dt>有效区间</dt><dd>${esc(fmtTime(rev.valid_from))} → ${rev.valid_to ? esc(fmtTime(rev.valid_to)) : '至今'}</dd>
      </dl></div>
    </details>`;
  }

  function contentPanel() {
    const rev = local.selectedRevision;
    if (!rev) return panel('版本与内容', empty('未选择 revision。'));
    const isLatest = rev.revision_id === local.object.latest_revision_id;
    const isEffective = rev.revision_id === local.object.effective_revision_id;
    const historyNote = !isLatest
      ? '<div class="tk-note tk-warning" style="margin-top:12px">正在查看历史 revision 内容；概览页的交付轨迹与提交验收仍是当前业务投影，不代表该历史时点的状态。</div>'
      : '';
    return panel('版本与内容', `<div class="tk-pad">
      ${revisionSelector()}
      <div class="tk-flex" style="margin-top:12px">${isLatest ? tag('最新候选 latest', 'info') : ''}${isEffective ? tag('当前生效 effective', 'good') : ''}${!isLatest && !isEffective ? tag('历史 revision') : ''}${tag(`精确 revision ${shortId(rev.revision_id)}…`, 'info')}</div>
      <div class="tk-divider"></div>
      ${payloadBlock(rev.payload)}
      ${local.object.object_type === 'EvidenceAsset' ? `<div class="tk-divider"></div><button type="button" class="tk-button tk-primary" data-action="evidence" data-object="${esc(objectId)}" data-revision="${esc(rev.revision_id)}">读取原始证据</button>${evidenceInspect()}` : ''}
      ${historyNote}
      <div class="tk-section">${revisionProvenanceDetails(rev)}</div>
      <div class="tk-section">${pointersDetails()}</div>
    </div>`);
  }

  // 责任人摘要：姓名/角色/assignment 是否当前有效；完整 assignment/baseline ID 收进溯源 details。
  function responsibilityPanel() {
    if (local.responsibilityError) {
      const info = describeError(local.responsibilityError);
      return panel('责任人摘要', `<div class="tk-pad">${errorBox(info.title, info.detail)}</div>`);
    }
    const r = local.responsibility;
    if (!r) return panel('责任人摘要', loading());
    const person = (title, p) => p ? `<dt>${esc(title)}</dt><dd>${esc(p.display_name || '—')} <span class="tk-muted">· ${esc(p.role)}</span><br>` +
      `<span class="tk-tiny tk-muted">${p.current_assignment_active ? 'assignment 当前有效' : 'assignment 当前无效'}</span></dd>` : '';
    const provenance = `<details class="tk-details" data-detail-key="prov-object"${detailsAttr('prov-object')}>
      <summary>溯源信息 · 责任与基线</summary>
      <div class="tk-detailsbody"><dl class="tk-kv">
        <dt>对象 ID</dt><dd>${idLine(objectId)}</dd>
        ${r.dri ? `<dt>DRI assignment</dt><dd>${idLine(r.dri.assignment_id)}</dd>` : ''}
        ${r.acceptor ? `<dt>验收人 assignment</dt><dd>${idLine(r.acceptor.assignment_id)}</dd>` : ''}
        <dt>冻结基线</dt><dd>${idLine(r.baseline_revision_id)}<br><span class="tk-tiny tk-muted">baseline 不可改写，与当前对象版本分开</span></dd>
      </dl></div>
    </details>`;
    return panel('责任人摘要', `<div class="tk-pad"><dl class="tk-kv">
      ${person('交付 DRI', r.dri)}
      ${person('指定验收人', r.acceptor)}
    </dl><div class="tk-divider"></div>
    <div class="tk-note">指定/assignment active 不代表拥有 Action 执行权限；能读取、负责交付、能验收分别判断。</div>
    <div class="tk-section">${provenance}</div></div>`);
  }

  function evidenceInspect() {
    const slot = evidenceLoader;
    if (!slot.key) return '';
    const [, evRevisionId] = slot.key.split(':');
    if (slot.loading) return `<section class="tk-inlineinspect">${loading('正在读取证据字节 …')}</section>`;
    if (slot.error) {
      const info = describeError(slot.error);
      return `<section class="tk-inlineinspect"><div class="tk-pad">${errorBox(info.title, info.detail)}</div></section>`;
    }
    if (!slot.value) return '';
    const data = slot.value;
    const isText = /^text\//.test(data.contentType) || data.contentType.includes('json') || data.contentType.includes('csv');
    const preview = isText && data.bytes.length <= TEXT_PREVIEW_LIMIT
      ? '<pre class="tk-pre" id="tk-evidence-text"></pre>'
      : `<p class="tk-small tk-muted">${isText ? '内容超过预览上限' : '二进制内容不提供内联预览'}，请下载后查看。</p>`;
    return `<section class="tk-inlineinspect" aria-label="证据详情">
      <div class="tk-panelhead tk-between"><h3>证据内容 · ${esc(shortId(evRevisionId))}…</h3>
      <button type="button" class="tk-link" data-action="close-evidence">收起</button></div>
      <div class="tk-pad"><dl class="tk-kv">
        <dt>证据对象</dt><dd>${idLine(slot.key.split(':')[0])}</dd>
        <dt>revision</dt><dd>${idLine(evRevisionId)}</dd>
        <dt>服务端 hash</dt><dd>${idLine(data.etag || '—')}</dd>
        <dt>类型</dt><dd>${esc(data.contentType)} · ${esc(data.length)} 字节</dd>
      </dl>
      <div class="tk-divider"></div>${preview}
      <div class="tk-flex" style="margin-top:12px"><button type="button" class="tk-button" data-action="download-evidence">下载原始字节</button>
      <span class="tk-tiny tk-muted">服务端逐字节校验 hash；hash 仅证明内容未被替换。</span></div></div></section>`;
  }

  function evidenceBlock(submission) {
    const ids = submission?.payload?.evidence_revision_ids || [];
    if (!ids.length) return '<p class="tk-small tk-muted">该提交未引用证据 revision。</p>';
    const upstream = submission?.payload?.upstream_refs || [];
    return ids.map((rid) => {
      const ref = upstream.find((u) => u.revision_id === rid);
      const key = `${ref?.object_id || ''}:${rid}`;
      const active = evidenceLoader.key === key;
      const suffix = active && evidenceLoader.loading ? '读取中 …' : active && evidenceLoader.value ? '已读取' : active && evidenceLoader.error ? '读取失败' : '点击查看';
      return `<button type="button" class="tk-evidence" data-action="evidence" data-object="${esc(ref?.object_id || '')}" data-revision="${esc(rid)}" ${ref ? '' : 'disabled'}>
        <span class="tk-file">EV</span>
        <span style="flex:1;min-width:0"><strong>证据 revision ${esc(shortId(rid))}… ↗</strong>
        <small>${ref ? `对象 ${esc(shortId(ref.object_id))}…` : '未能从 upstream_refs 解析证据对象'} · ${esc(suffix)}</small></span>
      </button>`;
    }).join('') + evidenceInspect();
  }

  function deliveryPanels() {
    const delivery = local.object.delivery;
    if (!delivery) return '';
    const submissions = [...(delivery.submissions || [])].sort((a, b) => (a.payload?.submission_seq || 0) - (b.payload?.submission_seq || 0));
    if (!submissions.length) return panel('提交与验收', empty('尚无交付提交。'));
    if (!local.submissionSeq || !submissions.some((s) => s.payload?.submission_seq === local.submissionSeq)) {
      local.submissionSeq = submissions[submissions.length - 1].payload?.submission_seq;
    }
    const current = submissions.find((s) => s.payload?.submission_seq === local.submissionSeq);
    const acceptances = (delivery.acceptances || []).filter((a) => a.submission_seq === local.submissionSeq);
    const tabs = submissions.map((s) => {
      const seq = s.payload?.submission_seq;
      const latestMark = s.revision_id === delivery.state?.latest_submission_revision_id ? ' · 最新' : '';
      return `<button type="button" class="tk-tabbutton" data-action="submission" data-value="${esc(seq)}" aria-pressed="${seq === local.submissionSeq}">v${esc(seq)}${latestMark}</button>`;
    }).join('');
    const acceptanceHtml = acceptances.length
      ? acceptances.map((a) => acceptanceBlock(a, current)).join('<div class="tk-divider"></div>')
      : '<p class="tk-small tk-muted" style="margin-top:10px">该提交尚无评审记录。</p>';
    const lastResult = acceptances.length ? acceptances[acceptances.length - 1].verification_result : null;
    return panel('提交与验收', `<div class="tk-pad">
      <div class="tk-between"><div><span class="tk-mono tk-muted">DELIVERABLE / v${esc(local.submissionSeq)}</span>
      <h2 style="margin-top:6px">${esc(current.payload?.title || '（无标题）')}</h2></div>
      ${lastResult ? tag(verificationLabel(lastResult).label, verificationKind(lastResult)) : tag('待评审', 'wait')}</div>
      ${current.payload?.summary ? `<p class="tk-small tk-muted" style="margin-top:9px">${esc(current.payload.summary)}</p>` : ''}
      <p class="tk-tiny tk-muted" style="margin-top:8px">v1 / v2 为提交序号，各自对应精确的 Deliverable revision，不与最新内容混用。</p>
      <div class="tk-divider"></div>
      ${acceptanceHtml}
      <details class="tk-details" data-detail-key="prov-submission"${detailsAttr('prov-submission')} style="margin-top:12px">
        <summary>溯源信息 · 提交 v${esc(local.submissionSeq)}</summary>
        <div class="tk-detailsbody"><dl class="tk-kv">
          <dt>提交 revision</dt><dd>${idLine(current.revision_id)}</dd>
          <dt>payload hash</dt><dd>${idLine(current.payload_hash)}</dd>
          <dt>记录时间</dt><dd>${esc(fmtTime(current.recorded_at))}</dd>
        </dl></div>
      </details>
    </div>`, `<div class="tk-tabbar" aria-label="选择交付提交版本">${tabs}</div>`) +
    `<div class="tk-section">${panel('本次提交引用的证据', `<div class="tk-pad" style="padding-top:2px;padding-bottom:2px">${evidenceBlock(current)}</div>`)}</div>`;
  }

  // 非 WorkItem 的概览：对象基本信息 + 前往「版本与内容」的入口。
  function genericOverview() {
    const o = local.object;
    return panel('对象概览', `<div class="tk-pad"><dl class="tk-kv">
      <dt>类型</dt><dd>${esc(o.object_type)}</dd>
      <dt>lifecycle</dt><dd>${tag(lifecycleLabel(o.lifecycle_status).label, lifecycleKind(o.lifecycle_status))} <span class="tk-mono tk-muted">${esc(o.lifecycle_status)}</span></dd>
      <dt>创建时间</dt><dd>${esc(fmtTime(o.created_at))}</dd>
      <dt>更新时间</dt><dd>${esc(fmtTime(o.updated_at))}</dd>
    </dl>
    <p class="tk-small tk-muted" style="margin-top:12px">对象实际内容在「版本与内容」页签；证据对象可读取原始字节。</p>
    <div style="margin-top:10px"><button type="button" class="tk-button" data-action="tab" data-value="version">查看对象内容 →</button></div></div>`);
  }

  function overviewTab() {
    if (local.object.object_type !== 'WorkItem') return genericOverview();
    const timeline = timelinePanel();
    return `${timeline ? `<div>${timeline}</div>` : ''}
      <div class="tk-split tk-section"><div>${deliveryPanels()}</div><div>${responsibilityPanel()}</div></div>`;
  }

  function relationsPanel() {
    if (relationLoader.error) {
      const info = describeError(relationLoader.error);
      return panel('来源关系（出向一跳）', `<div class="tk-pad">${errorBox(info.title, info.detail, info.retryable ? 'retry-relations' : '')}</div>`);
    }
    if (!relationLoader.started) return panel('来源关系（出向一跳）', loading());
    const rows = relationLoader.items.length ? relationLoader.items.map((rel) =>
      `<div class="tk-relation"><div class="tk-node tk-active">${esc(local.object.object_type)}<small class="tk-mono">${esc(shortId(rel.source_ref.revision_id))}…</small></div>
      <span class="tk-edge">来源引用<br>→</span>
      <div class="tk-node">${esc(rel.target_type || '对象')}<small class="tk-mono">${esc(shortId(rel.target_ref.object_id))}… @ ${esc(shortId(rel.target_ref.revision_id))}…</small></div>
      <button type="button" class="tk-link" data-action="open-ref" data-object="${esc(rel.target_ref.object_id)}" data-revision="${esc(rel.target_ref.revision_id)}">按精确 revision 查看 →</button></div>`,
    ).join('') : empty('该 revision 没有可读的出向来源引用。');
    const more = relationLoader.hasMore
      ? `<div style="margin-top:10px"><button type="button" class="tk-button" data-action="more-relations" ${relationLoader.loading ? 'disabled' : ''}>${relationLoader.loading ? '加载中 …' : '加载更多关系'}</button></div>` : '';
    const sourceNote = relationLoader.meta?.source_ref
      ? `<p class="tk-tiny tk-muted" style="margin-top:4px">source：${esc(shortId(relationLoader.meta.source_ref.object_id))}… @ revision ${esc(shortId(relationLoader.meta.source_ref.revision_id))}…</p>`
      : '';
    return panel('来源关系（出向一跳）', `<div class="tk-pad">
      <p class="tk-tiny tk-muted">范围：指定 source revision 的出向一跳来源引用；无权 target 的边整条隐藏。不提供入向全图。</p>
      ${sourceNote}
      ${rows}${more}</div>`);
  }

  function draw() {
    const o = local.object;
    const revTitle = local.selectedRevision?.payload?.title;
    const title = revTitle || o.latest_revision?.payload?.title || o.object_id;
    const lc = lifecycleLabel(o.lifecycle_status);
    const header = head(title, `${o.object_type} · ${lc.label} · 真实接口读取`,
      `<button type="button" class="tk-button" data-action="goto-receipts">动作与回执 →</button>`);
    const tabContent = local.tab === 'overview' ? overviewTab()
      : local.tab === 'relations' ? relationsPanel()
      : contentPanel();
    main.innerHTML = header + tabBar() + (isAnchorWi && local.tab === 'overview' ? statusBand() : '') + tabContent + foot(ctx.config?.commit);
    fillEvidenceText();
  }

  // 证据文本经 textContent 注入，绝不走 innerHTML。
  function fillEvidenceText() {
    const pre = main.querySelector('#tk-evidence-text');
    if (pre && evidenceLoader.value) {
      pre.textContent = new TextDecoder().decode(evidenceLoader.value.bytes);
    }
  }

  function openEvidence(evObjectId, evRevisionId) {
    const pending = evidenceLoader.load(`${evObjectId}:${evRevisionId}`, ctx.signal);
    draw(); // load 已同步置 loading，立即显示读取中；details 展开状态由 openDetails 保持
    pending.then((result) => {
      if (result.status === 'stale' || !ctx.isCurrent()) return;
      draw();
      main.querySelector('.tk-inlineinspect')?.scrollIntoView({ block: 'nearest' });
    });
  }

  // 初始加载
  main.innerHTML = head('实例详情', '正在读取对象 …') + loading();
  try {
    const [object] = await Promise.all([
      ctx.client.getJson(`/v1/objects/${objectId}`, { signal: ctx.signal }),
      isAnchorWi ? fetchAnchors() : Promise.resolve(),
    ]);
    if (!ctx.isCurrent()) return;
    local.object = object;
    local.tab = initialInstanceTab({ objectType: object.object_type, explicitRevision: Boolean(route.revisionId) });
    if (!local.selectedRevisionId) local.selectedRevisionId = object.latest_revision_id;
    await revisionLoader.loadMore({}, ctx.signal);
    if (!ctx.isCurrent()) return;
    const extra = [fetchSelectedRevision()];
    if (object.object_type === 'WorkItem') {
      extra.push((async () => {
        try {
          local.responsibility = await ctx.client.getJson(`/v1/objects/${objectId}/responsibility`, { signal: ctx.signal });
        } catch (error) {
          if (ctx.isCurrent()) local.responsibilityError = error;
        }
      })());
    }
    relationLoader.reset();
    extra.push(relationLoader.loadMore(
      local.selectedRevisionId ? { revision_id: local.selectedRevisionId } : {},
      ctx.signal,
    ));
    await Promise.all(extra);
    if (!ctx.isCurrent()) return;
    ctx.reportObject?.(objectId, {
      title: local.selectedRevision?.payload?.title || object.latest_revision?.payload?.title || null,
      objectType: object.object_type,
    });
  } catch (error) {
    if (!ctx.isCurrent()) return;
    const info = describeError(error);
    main.innerHTML = head('实例详情', '对象读取失败。') + errorBox(info.title, info.detail, info.retryable ? 'reload-page' : '');
    return;
  }
  draw();

  // 监听随 ctx.signal 卸载。
  main.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || !main.contains(button)) return;
    const action = button.dataset.action;
    if (action === 'tab') {
      // 本页页签：只切换展示层，不重新请求业务数据。
      local.tab = button.dataset.value;
      draw();
    } else if (action === 'open-ref') {
      ctx.navigate({ page: 'instance', objectId: button.dataset.object, revisionId: button.dataset.revision });
    } else if (action === 'goto-object') {
      ctx.navigate({ page: 'instance', objectId: button.dataset.value });
    } else if (action === 'goto-receipts') {
      ctx.navigate({ page: 'receipts', objectId });
    } else if (action === 'goto-receipt') {
      ctx.navigate({ page: 'receipts', objectId, receiptId: button.dataset.value });
    } else if (action === 'submission') {
      local.submissionSeq = Number(button.dataset.value);
      evidenceLoader.clear();
      draw();
    } else if (action === 'evidence') {
      if (button.dataset.object) openEvidence(button.dataset.object, button.dataset.revision);
    } else if (action === 'close-evidence') {
      evidenceLoader.clear();
      draw();
    } else if (action === 'download-evidence') {
      if (evidenceLoader.value) {
        const blob = new Blob([evidenceLoader.value.bytes], { type: evidenceLoader.value.contentType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `evidence-${evidenceLoader.key.split(':')[1]}`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } else if (action === 'more-revisions') {
      (async () => { await revisionLoader.loadMore({}, ctx.signal); if (ctx.isCurrent()) draw(); })();
    } else if (action === 'more-relations') {
      (async () => {
        const params = local.selectedRevisionId ? { revision_id: local.selectedRevisionId } : {};
        await relationLoader.loadMore(params, ctx.signal);
        if (ctx.isCurrent()) draw();
      })();
    } else if (action === 'retry-relations') {
      relationLoader.reset();
      (async () => {
        const params = local.selectedRevisionId ? { revision_id: local.selectedRevisionId } : {};
        await relationLoader.loadMore(params, ctx.signal);
        if (ctx.isCurrent()) draw();
      })();
    }
  }, { signal: ctx.signal });
  main.addEventListener('change', (event) => {
    if (event.target.dataset.control === 'revision') {
      ctx.navigate({ page: 'instance', objectId, revisionId: event.target.value });
    }
  }, { signal: ctx.signal });
  // details 展开状态在异步重绘间保持（capture：toggle 不冒泡）；证据加载不收起已展开区块。
  main.addEventListener('toggle', (event) => {
    const detail = event.target.closest?.('details[data-detail-key]');
    if (!detail || !main.contains(detail)) return;
    if (detail.open) local.openDetails.add(detail.dataset.detailKey);
    else local.openDetails.delete(detail.dataset.detailKey);
  }, { capture: true, signal: ctx.signal });
}
