// 当前对象导航状态（仅内存，不落地 localStorage）：跨页保持同一对象与用户选过的
// 精确 revision。可显示的标题/类型只在授权读取成功后写入；切身份/换对象立即清空，
// 任何 401/403/404 都不会残留先前身份的数据。

export function createFocus() {
  return {
    objectId: null,
    revisionId: null,
    title: null,
    objectType: null,
    catalogType: null,
    catalogDomainId: null,
  };
}

// 切换演练身份：连同目录记忆一并清空。
export function clearFocus(focus) {
  Object.assign(focus, createFocus());
}

// 打开对象：换对象时清掉旧标题与 revision；同对象保留标题，显式 revision 覆盖当前选择。
export function openObject(focus, objectId, revisionId = null) {
  if (!objectId) return;
  if (focus.objectId !== objectId) {
    focus.objectId = objectId;
    focus.title = null;
    focus.objectType = null;
    focus.revisionId = null;
  }
  if (revisionId) focus.revisionId = revisionId;
}

// 授权读取成功后回填可显示标题/类型；只接受当前对象的读取结果（防迟到响应回填）。
export function recordRead(focus, objectId, meta = {}) {
  if (!objectId || focus.objectId !== objectId) return false;
  focus.title = meta.title || null;
  focus.objectType = meta.objectType || null;
  if (meta.revisionId) focus.revisionId = meta.revisionId;
  return true;
}

// 用户在实例页显式选择 revision 后记录，供回执页返回与主导航复用。
export function selectRevision(focus, revisionId) {
  if (focus.objectId && revisionId) focus.revisionId = revisionId;
}

// 主导航路由：有当前对象就带着对象（实例页连带已选 revision）；无则回退页面默认锚点。
export function navRoute(focus, page) {
  if (page === 'instance' && focus.objectId) {
    return { page, objectId: focus.objectId, revisionId: focus.revisionId };
  }
  if (page === 'receipts' && focus.objectId) {
    return { page, objectId: focus.objectId };
  }
  return { page };
}

// 目录页类型/域选择的小范围记忆（仅内存，同次会话内返回目录时保留）。
export function rememberCatalog(focus, { type, domainId } = {}) {
  if (type !== undefined) focus.catalogType = type;
  if (domainId !== undefined) focus.catalogDomainId = domainId;
}
