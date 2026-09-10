// HTML 字符串拼装的安全基础：所有服务端文本一律经 esc() 或 textContent 进入页面。
const ESCAPE_MAP = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (ch) => ESCAPE_MAP[ch]);
}

// 通用标签/面板小构件，只接受已经 esc 过或纯静态的文本参数。
export function tag(text, kind = '') {
  return `<span class="tk-tag${kind ? ` tk-${kind}` : ''}">${esc(text)}</span>`;
}

export function panel(title, body, extra = '') {
  return `<section class="tk-panel"><div class="tk-panelhead tk-between"><h3>${esc(title)}</h3>${extra}</div>${body}</section>`;
}

export function head(title, subtitle, action = '') {
  return `<div class="tk-breadcrumb">工作台 / ${esc(title)}</div>` +
    `<div class="tk-pagehead">` +
    `<div class="tk-between"><h1>${esc(title)}</h1>${action}</div>` +
    (subtitle ? `<p class="tk-subtitle">${esc(subtitle)}</p>` : '') + `</div>`;
}

export function foot(commit) {
  const left = commit ? `Runtime 基线 ${esc(commit)} · 本地合成演练` : '本地合成演练';
  return `<div class="tk-footnote"><span>${left} · 数据来自本地只读代理</span><span>本体与记忆工作台 / 只读展示 + 显式审计快照</span></div>`;
}

export function loading(text = '加载中 …') {
  return `<div class="tk-loading" role="status"><span class="tk-spin" aria-hidden="true"></span><span>${esc(text)}</span></div>`;
}

export function empty(text) {
  return `<div class="tk-empty">${esc(text)}</div>`;
}

export function errorBox(title, detail, retryAction = '') {
  const retry = retryAction
    ? `<div style="margin-top:10px"><button type="button" class="tk-button" data-action="${esc(retryAction)}">重试</button></div>`
    : '';
  return `<div class="tk-errorbox" role="alert"><strong>${esc(title)}</strong><span>${esc(detail)}</span>${retry}</div>`;
}

// UUID/hash 展示：长 ID 可换行，附带复制按钮（复制由 app.js 委托处理）。
export function idLine(value, { copy = true } = {}) {
  const btn = copy ? `<button type="button" class="tk-copy" data-copy="${esc(value)}">复制</button>` : '';
  return `<span class="tk-idline"><span class="tk-mono">${esc(value)}</span>${btn}</span>`;
}
