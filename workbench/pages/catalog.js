// 页 01 对象与关系：左侧类型列表，右侧「业务域过滤（首屏）→ 类型摘要 + 实例列表 →
// 默认收起的类型定义与字段」。类型目录是静态说明，不代表当前身份拥有任何执行权限。
import { esc, panel, head, foot, tag, loading, empty, errorBox } from '../lib/html.js';
import { PagedLoader } from '../lib/loaders.js';
import { buildQuery } from '../lib/url.js';
import { describeError } from '../lib/api.js';
import { rememberCatalog } from '../lib/focus.js';
import { creationModeLabel, lifecycleLabel, lifecycleKind, fmtTime } from '../lib/format.js';

const PAGE_SIZE = 20;
const MAX_DOMAIN_PAGES = 20;

function schemaTable(schema) {
  if (!schema) return '<p class="tk-small tk-muted">该类型未公开创建字段，请以类型说明为准。</p>';
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  const names = Object.keys(properties);
  if (!names.length) return '<p class="tk-small tk-muted">schema 未声明字段。</p>';
  const rows = names.map((name) => {
    const prop = properties[name] || {};
    const type = prop.type || (prop.anyOf ? 'anyOf' : prop.$ref ? 'ref' : '—');
    const desc = prop.description || prop.title || '';
    return `<tr><td class="tk-mono">${esc(name)}</td><td>${esc(type)}${required.has(name) ? ' · 必填' : ''}</td><td>${esc(desc)}</td></tr>`;
  }).join('');
  return `<table class="tk-table"><thead><tr><th style="width:26%">字段</th><th style="width:22%">类型</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// 类型定义/schema/typed 字段：默认收起的 details，放在实例列表之后。
function typeDefinitionDetails(item, openAttr) {
  if (!item) return '';
  const refs = (item.reference_fields || []).map((r) => `<div class="tk-checkline"><span class="tk-checkmark">→</span><span>${esc(r)}</span></div>`).join('');
  return `<details class="tk-details tk-section" data-detail-key="type-def"${openAttr}>
    <summary>类型定义与字段 · ${esc(item.label || item.object_type)}</summary>
    <div class="tk-detailsbody">
      <div class="tk-flex"><span class="tk-mono tk-muted">${esc(item.object_type)}</span>${tag(creationModeLabel(item.creation_mode).label, 'info')}${tag('静态目录说明', 'info')}</div>
      <p class="tk-small" style="margin-top:8px">${esc(item.description || '')}</p>
      <div class="tk-divider"></div>
      <h3>typed 引用字段</h3>
      ${refs || '<p class="tk-small tk-muted">无声明的 typed 引用字段。</p>'}
      <div class="tk-divider"></div>
      <h3>payload schema</h3>
      ${schemaTable(item.payload_schema)}
      <p class="tk-tiny tk-muted" style="margin-top:10px">${esc(item.versioning || '')}</p>
    </div>
  </details>`;
}

function objectRow(obj) {
  const lc = lifecycleLabel(obj.lifecycle_status);
  const samePointer = obj.latest_revision_id && obj.latest_revision_id === obj.effective_revision_id;
  const pointer = obj.latest_revision_id
    ? (samePointer ? tag('最新 = 生效', 'good') : `${tag('最新候选', 'wait')}${tag('生效另指', 'info')}`)
    : '';
  return `<button type="button" class="tk-objrow" data-action="open-object" data-value="${esc(obj.object_id)}">
    <span><strong>${esc(obj.title || '（无标题）')}</strong>
    <small class="tk-mono">${esc(obj.object_id)}</small></span>
    <span style="text-align:right">${tag(lc.label, lifecycleKind(obj.lifecycle_status))}${pointer}
    <small class="tk-mono" style="display:block;margin-top:3px">对象版本 ${esc(obj.object_version)} · ${esc(fmtTime(obj.created_at))}</small></span>
  </button>`;
}

export async function renderCatalog(main, ctx, route) {
  const local = {
    type: route.type || ctx.focus?.catalogType || null,
    domainId: ctx.focus?.catalogDomainId || null,
    types: null,
    domains: null,
    domainsTruncated: false,
    openDetails: new Set(),
  };
  // 实例列表加载器：filter（domain/type）变化即 reset，乱序旧响应不会写入。
  const listLoader = new PagedLoader({
    pageSize: PAGE_SIZE,
    fetchPage: (params, signal) => ctx.client.getJson(`/v1/objects${buildQuery(params)}`, { signal }),
  });

  function currentFilters() {
    const params = { domain_id: local.domainId };
    if (local.type) params.object_type = local.type;
    return params;
  }

  async function refreshList() {
    if (!local.domainId) {
      listLoader.started = true; listLoader.done = true; draw(); return;
    }
    draw();
    const result = await listLoader.loadMore(currentFilters(), ctx.signal);
    if (result.status === 'stale') return;
    if (ctx.isCurrent()) draw();
  }

  function changeFilter(mutate) {
    mutate();
    rememberCatalog(ctx.focus, { type: local.type, domainId: local.domainId });
    listLoader.reset();
    refreshList();
  }

  function detailsAttr(key) {
    return local.openDetails.has(key) ? ' open' : '';
  }

  // 业务域过滤：主内容首屏的过滤条。
  function domainBar() {
    const domainOptions = (local.domains || []).map((d) =>
      `<option value="${esc(d.domain_id)}" ${d.domain_id === local.domainId ? 'selected' : ''}>${esc(d.name || d.domain_id)}</option>`,
    ).join('');
    const truncated = local.domainsTruncated
      ? '<span class="tk-tiny tk-muted">域数量超过加载上限，仅列出前若干页。</span>' : '';
    return `<div class="tk-lens"><label class="tk-field" for="tk-domain">业务域<select class="tk-select" id="tk-domain" data-control="domain">${domainOptions}</select></label>` +
      `<span class="tk-tiny tk-muted">只列出当前演练身份被允许 read 的域；其余域整条隐藏。</span>${truncated}</div>`;
  }

  function listPanel(current) {
    let body;
    if (listLoader.error) {
      const info = describeError(listLoader.error);
      body = `<div class="tk-pad">${errorBox(info.title, info.detail, info.retryable ? 'retry-list' : '')}</div>`;
    } else if (!listLoader.started || (listLoader.loading && !listLoader.items.length)) {
      body = loading();
    } else if (listLoader.isEmpty) {
      body = empty('当前域与类型组合下没有可读实例。');
    } else {
      body = listLoader.items.map(objectRow).join('');
    }
    const more = listLoader.hasMore
      ? `<div class="tk-pad" style="padding-top:10px"><button type="button" class="tk-button" data-action="load-more" ${listLoader.loading ? 'disabled' : ''}>${listLoader.loading ? '加载中 …' : '加载更多'}</button></div>`
      : '';
    const count = listLoader.started && !listLoader.error ? tag(`已读 ${listLoader.items.length} 条`, 'info') : '';
    return panel(`实例列表 · ${current?.label || current?.object_type || ''}`, body + more, count);
  }

  function draw() {
    const types = local.types?.items || [];
    if (!local.type && types.length) local.type = types[0].object_type;
    const current = types.find((t) => t.object_type === local.type) || types[0];
    const typeButtons = types.map((t) =>
      `<button type="button" class="tk-typebutton" data-action="type-select" data-value="${esc(t.object_type)}" aria-pressed="${current && t.object_type === current.object_type}"><strong>${esc(t.label || t.object_type)}</strong><small>${esc(t.object_type)}</small></button>`,
    ).join('');
    main.innerHTML = head('对象与关系',
      '按类型浏览当前身份可读的业务对象；类型说明是静态元数据，不代表当前身份的执行权限。') +
      `<div class="tk-catalog"><div class="tk-panel"><div class="tk-panelhead"><h3>对象目录</h3><div class="tk-tiny tk-muted">${types.length} 个类型</div></div>` +
      `<div class="tk-typelist">${typeButtons}</div>` +
      `<div class="tk-pad tk-tiny tk-muted">目录为静态说明，不含当前权限结论。<br><br>Mission / Risk / Lesson 未建模，不在目录中。</div></div>` +
      `<div>${domainBar()}` +
      `<div class="tk-section">${listPanel(current)}</div>` +
      `${typeDefinitionDetails(current, detailsAttr('type-def'))}</div></div>${foot(ctx.config?.commit)}`;
  }

  main.innerHTML = head('对象与关系', '正在读取类型目录与可读业务域 …') + loading();
  try {
    const types = await ctx.cachedJson('object-types', '/v1/object-types');
    if (!ctx.isCurrent()) return;
    // 域列表消费全部 next_cursor 页（有界）。
    const domains = [];
    let cursor = null;
    for (let i = 0; i < MAX_DOMAIN_PAGES; i += 1) {
      const page = await ctx.client.getJson(`/v1/domains${buildQuery({ limit: 100, cursor })}`, { signal: ctx.signal });
      if (!ctx.isCurrent()) return;
      domains.push(...(page.items || []));
      cursor = page.next_cursor;
      if (!cursor) break;
      if (i === MAX_DOMAIN_PAGES - 1) local.domainsTruncated = true;
    }
    if (!ctx.isCurrent()) return;
    local.types = types;
    local.domains = domains;
    if (local.domainId && !domains.some((d) => d.domain_id === local.domainId)) local.domainId = null;
    if (!local.domainId) local.domainId = domains[0]?.domain_id || null;
    if (local.type && !types.items.some((t) => t.object_type === local.type)) local.type = null;
    if (!local.type) local.type = types.items[0]?.object_type || null;
    rememberCatalog(ctx.focus, { type: local.type, domainId: local.domainId });
  } catch (error) {
    if (!ctx.isCurrent()) return;
    const info = describeError(error);
    main.innerHTML = head('对象与关系', '类型目录或业务域读取失败。') + errorBox(info.title, info.detail, info.retryable ? 'reload-page' : '');
    return;
  }

  // 监听随 ctx.signal 卸载：切页/切身份后旧闭包全部移除。
  main.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || !main.contains(button)) return;
    const action = button.dataset.action;
    if (action === 'type-select') {
      changeFilter(() => { local.type = button.dataset.value; });
    } else if (action === 'open-object') {
      ctx.navigate({ page: 'instance', objectId: button.dataset.value });
    } else if (action === 'load-more') {
      refreshList();
    } else if (action === 'retry-list') {
      listLoader.reset();
      refreshList();
    }
  }, { signal: ctx.signal });
  main.addEventListener('change', (event) => {
    if (event.target.dataset.control === 'domain') {
      changeFilter(() => { local.domainId = event.target.value; });
    }
  }, { signal: ctx.signal });
  // details 展开状态在异步重绘间保持（capture：toggle 不冒泡）。
  main.addEventListener('toggle', (event) => {
    const detail = event.target.closest?.('details[data-detail-key]');
    if (!detail || !main.contains(detail)) return;
    if (detail.open) local.openDetails.add(detail.dataset.detailKey);
    else local.openDetails.delete(detail.dataset.detailKey);
  }, { capture: true, signal: ctx.signal });

  if (!local.domainId) {
    // 当前身份没有任何可读域：真实空态，不请求对象列表。
    listLoader.started = true;
    listLoader.done = true;
    draw();
    return;
  }
  refreshList();
}
