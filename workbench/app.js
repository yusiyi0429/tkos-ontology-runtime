// TKOS 工作台外壳：演练配置加载、固定身份切换、hash 路由、全局复制与公告。
import { createClient, describeError, cachedJson } from './lib/api.js';
import { RequestGuard } from './lib/guard.js';
import { parseHash, buildHash, casePathToRoute } from './lib/url.js';
import { esc, loading, errorBox } from './lib/html.js';
import { renderCatalog } from './pages/catalog.js';
import { renderInstance } from './pages/instance.js';
import { renderReceipts } from './pages/receipts.js';
import { renderContext } from './pages/context.js';

const root = document.getElementById('tkos-workbench');
const main = root.querySelector('#tk-main');
const casePanel = root.querySelector('#tk-case');
const announceEl = root.querySelector('#tk-announcement');
const actorSelect = root.querySelector('#tk-actor');

// 部署入口为 /workbench/；config 在上一级 case.json，API 在同源 '/{actor}/v1/...'。
const workbenchBase = new URL('./', window.location.href);
const configUrl = new URL('../case.json', workbenchBase);
const apiRoot = new URL('../', workbenchBase).pathname;

const PAGE_TITLES = { catalog: '对象与关系', instance: '实例详情', receipts: '动作与回执', context: 'Context Pack' };
const PAGE_RENDERERS = { catalog: renderCatalog, instance: renderInstance, receipts: renderReceipts, context: renderContext };

const state = {
  actor: 'ceo',
  config: null,
  cache: new Map(),
  guard: new RequestGuard(),
};

function announce(text) {
  announceEl.textContent = text;
}

function navigate(route) {
  const next = buildHash(route);
  if (window.location.hash === next) renderRoute();
  else window.location.hash = next;
}

function makeCtx(token, signal) {
  const actor = state.actor;
  const cache = state.cache;
  const client = createClient({ apiRoot, actor });
  return {
    actor: state.actor,
    config: state.config,
    client,
    // 只在本次导航内去重；失败即淘汰，不跨页面复用授权读取。
    cachedJson: (key, path) => cachedJson(cache, `${actor}:${key}`, () => client.getJson(path, { signal })),
    cache: state.cache,
    token,
    signal,
    isCurrent: () => state.guard.isCurrent(token),
    navigate,
    announce,
  };
}

async function renderRoute() {
  if (!state.config) return;
  state.cache.clear();
  state.cache = new Map(); // Per navigation: never reuse a promise tied to an aborted route.
  const { token, signal } = state.guard.begin();
  const route = parseHash(window.location.hash);
  root.querySelectorAll('.tk-navbutton').forEach((btn) => {
    if (btn.dataset.nav === route.page) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
  main.innerHTML = loading();
  main.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "instant" });
  const renderer = PAGE_RENDERERS[route.page] || renderCatalog;
  const ctx = makeCtx(token, signal);
  try {
    await renderer(main, ctx, route);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    const info = describeError(error);
    main.innerHTML = errorBox(info.title, info.detail, info.retryable ? 'reload-page' : '');
  }
  if (ctx.isCurrent()) announce(`已更新${PAGE_TITLES[route.page] || '对象与关系'}视图`);
}

async function loadConfig() {
  casePanel.textContent = '正在读取演练配置 …';
  const response = await fetch(configUrl, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`case.json 读取失败（HTTP ${response.status}）`);
  state.config = await response.json();
  renderCasePanel();
}

function renderCasePanel() {
  const config = state.config;
  if (!config) return;
  const anchors = Object.entries(config.state_paths || {}).map(([label, path]) => {
    const route = casePathToRoute(path);
    return `<button type="button" class="tk-link" data-nav-route="${esc(buildHash(route))}">${esc(label)} ↗</button>`;
  }).join('');
  casePanel.innerHTML = `当前演练案例<strong>交付与反馈闭环</strong><div class="tk-caseanchors">${anchors}</div>` +
    `<div style="margin-top:14px" class="tk-mono">Runtime 基线 ${esc(config.commit || '—')}</div>` +
    `<a class="tk-link" style="display:block;margin-top:12px;text-decoration:none" href="/">API 验证入口 →</a>`;

}

async function boot() {
  main.innerHTML = loading('正在读取演练配置 …');
  try {
    await loadConfig();
  } catch (error) {
    main.innerHTML = errorBox('演练配置读取失败', error?.message || '无法读取 ../case.json。', 'retry-config');
    return;
  }
  await renderRoute();
}

root.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button || !root.contains(button)) return;
  if (button.dataset.nav) {
    navigate({ page: button.dataset.nav });
    return;
  }
  if (button.dataset.navRoute) {
    const target = button.dataset.navRoute;
    if (window.location.hash === target) renderRoute();
    else window.location.hash = target;
    return;
  }
  if (button.dataset.copy !== undefined) {
    const text = button.dataset.copy;
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => announce('已复制到剪贴板'), () => announce('复制失败'));
    } else {
      announce('当前浏览器不支持自动复制');
    }
    return;
  }
  if (button.dataset.action === 'retry-config') {
    boot();
    return;
  }
  if (button.dataset.action === 'reload-page') {
    renderRoute();
  }
});

actorSelect.addEventListener('change', () => {
  // 切换演练身份：先清掉所有已读对象/回执/快照缓存，再以新身份重新查询。
  state.actor = actorSelect.value;
  state.cache.clear();
  state.guard.cancel();
  announce(`已切换到 ${actorSelect.selectedOptions[0]?.textContent || state.actor} 演练身份，正在以新身份重新查询`);
  renderRoute();
});

window.addEventListener('hashchange', () => renderRoute());

boot();
