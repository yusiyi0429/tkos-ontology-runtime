// 页 01 对象与关系：真实类型目录（10 种）+ 可读域选择 + 按类型分页实例列表。
// 类型目录是静态说明，不代表当前身份拥有任何执行权限。
import { esc, panel, head, foot, tag, loading, empty, errorBox } from '../lib/html.js';
import { PagedLoader } from '../lib/loaders.js';
import { buildQuery } from '../lib/url.js';
import { describeError } from '../lib/api.js';
import { creationModeLabel, lifecycleLabel, lifecycleKind, fmtTime } from '../lib/format.js';

const PAGE_SIZE = 20;
const MAX_DOMAIN_PAGES = 20;

function schemaTable(schema) {
  if (!schema) return '<p class="tk-small tk-muted">该类型经专用动作进入，没有公开创建 payload schema。</p>';
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

function typeDetail(item) {
  const refs = (item.reference_fields || []).map((r) => `<div class="tk-checkline"><span class="tk-checkmark">→</span><span>${esc(r)}</span></div>`).join('');
  return panel(item.label || item.object_type, `<div class="tk-pad">
    <div class="tk-flex"><span class="tk-mono tk-muted">${esc(item.object_type)}</span>${tag(creationModeLabel(item.creation_mode).label, 'info')}</div>
    <p class="tk-small" style="margin-top:8px">${esc(item.description || '')}</p>
    <div class="tk-divider"></div>
    <h3>typed 引用字段</h3>
    ${refs || '<p class="tk-small tk-muted">无声明的 typed 引用字段。</p>'}
    <div class="tk-divider"></div>
    <h3>payload schema</h3>
    ${schemaTable(item.payload_schema)}
    <p class="tk-tiny tk-muted" style="margin-top:10px">${esc(item.versioning || '')}</p>
  </div>`, tag('静态目录说明', 'info'));
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
    type: route.type,
    domainId: null,
    types: null,
    domains: null,
    domainsTruncated: false,
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
    listLoader.reset();
    refreshList();
  }

  function listPanel() {
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
    return panel('实例列表', body + more, count);
  }

  function draw() {
    const types = local.types?.items || [];
    if (!local.type && types.length) local.type = types[0].object_type;
    const current = types.find((t) => t.object_type === local.type) || types[0];
    const typeButtons = types.map((t) =>
      `<button type="button" class="tk-typebutton" data-action="type-select" data-value="${esc(t.object_type)}" aria-pressed="${current && t.object_type === current.object_type}"><strong>${esc(t.label || t.object_type)}</strong><small>${esc(t.object_type)}</small></button>`,
    ).join('');
    const domainOptions = (local.domains || []).map((d) =>
      `<option value="${esc(d.domain_id)}" ${d.domain_id === local.domainId ? 'selected' : ''}>${esc(d.name || d.domain_id)}</option>`,
    ).join('');
    const truncated = local.domainsTruncated
      ? '<p class="tk-tiny tk-muted" style="margin-top:6px">域数量超过加载上限，仅列出前若干页。</p>' : '';
    main.innerHTML = head('MODEL CATALOG', '对象与关系',
      '真实类型目录与可读实例。类型说明是静态元数据，不代表当前身份的执行权限；实例按当前身份授权过滤。') +
      `<div class="tk-catalog"><div class="tk-panel"><div class="tk-panelhead"><h3>对象目录</h3><div class="tk-tiny tk-muted">${types.length} 个类型</div></div>` +
      `<div class="tk-typelist">${typeButtons}</div>` +
      `<div class="tk-pad tk-tiny tk-muted">目录为静态说明，不含当前权限结论。<br><br>Mission / Risk / Lesson 未建模，不在目录中。</div></div>` +
      `<div>${current ? typeDetail(current) : ''}` +
      `<div class="tk-section">${panel('可读业务域', `<div class="tk-pad"><label class="tk-field" for="tk-domain">业务域<select class="tk-select" id="tk-domain" data-control="domain">${domainOptions}</select></label>` +
      `<p class="tk-tiny tk-muted" style="margin-top:10px">只列出当前演练身份被允许 read 的域；其余域整条隐藏。</p>${truncated}</div>`)}</div>` +
      `<div class="tk-section">${listPanel()}</div></div></div>${foot(ctx.config?.commit)}`;
  }

  main.innerHTML = head('MODEL CATALOG', '对象与关系', '正在读取类型目录与可读业务域 …') + loading();
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
    local.domainId = domains[0]?.domain_id || null;
    if (local.type && !types.items.some((t) => t.object_type === local.type)) local.type = null;
  } catch (error) {
    if (!ctx.isCurrent()) return;
    const info = describeError(error);
    main.innerHTML = head('MODEL CATALOG', '对象与关系', '类型目录或业务域读取失败。') + errorBox(info.title, info.detail, info.retryable ? 'reload-page' : '');
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

  if (!local.domainId) {
    // 当前身份没有任何可读域：真实空态，不请求对象列表。
    listLoader.started = true;
    listLoader.done = true;
    draw();
    return;
  }
  refreshList();
}
