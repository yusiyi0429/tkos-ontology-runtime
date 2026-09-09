// URL hash 路由与参数校验。所有进入路径段的值先校验再 encodeURIComponent。

const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const KNOWN_TYPES = new Set([
  'CompanyOutcome', 'BusinessCommitment', 'ExecutionCommitment', 'FeedbackThread',
  'ManagementAdjustment', 'Decision', 'MetricObservation', 'WorkItem',
  'EvidenceAsset', 'Deliverable',
]);

export function isUuid(value) {
  return typeof value === 'string' && UUID_RE.test(value);
}

export function isKnownType(value) {
  return KNOWN_TYPES.has(value);
}

// 路径段只允许 UUID；其他字符拒绝，返回 null。
export function uuidSeg(value) {
  return isUuid(value) ? value.toLowerCase() : null;
}

// 构造查询串：跳过 null/undefined/''，键值均编码。
export function buildQuery(params) {
  const parts = [];
  for (const [key, value] of Object.entries(params || {})) {
    if (value === null || value === undefined || value === '') continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

// route: {page, objectId?, revisionId?, receiptId?, snapshotId?, type?}
export function buildHash(route) {
  const page = route.page || 'catalog';
  let path = `#/${page}`;
  const query = {};
  if (page === 'instance' && isUuid(route.objectId)) {
    path += `/${route.objectId}`;
    if (isUuid(route.revisionId)) query.rev = route.revisionId;
  } else if (page === 'receipts') {
    if (isUuid(route.objectId)) path += `/${route.objectId}`;
    if (isUuid(route.receiptId)) query.r = route.receiptId;
  } else if (page === 'context') {
    if (isUuid(route.snapshotId)) query.snap = route.snapshotId;
  } else if (page === 'catalog') {
    if (isKnownType(route.type)) query.type = route.type;
  }
  const qs = buildQuery(query);
  return qs ? `${path}${qs}` : path;
}

// 把 case.json 里的 '/v1/...' 演练路径映射到内部路由；无法识别时回退到目录页。
export function casePathToRoute(path) {
  const text = String(path || '');
  const uuid = /([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/;
  if (text.startsWith('/v1/context-packs/')) {
    const m = text.match(uuid);
    return { page: 'context', snapshotId: m ? m[1].toLowerCase() : null };
  }
  if (text.includes('/action-receipts')) {
    const m = text.match(uuid);
    return { page: 'receipts', objectId: m ? m[1].toLowerCase() : null };
  }
  if (text.startsWith('/v1/objects/') || text.startsWith('/v1/evidence-assets/')) {
    const m = text.match(uuid);
    return { page: 'instance', objectId: m ? m[1].toLowerCase() : null };
  }
  return { page: 'catalog' };
}

export function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '');
  const [pathPart, queryPart] = raw.split('?');
  const segments = pathPart.split('/').filter(Boolean);
  const query = new URLSearchParams(queryPart || '');
  const route = { page: 'catalog', objectId: null, revisionId: null, receiptId: null, snapshotId: null, type: null };
  const page = segments[0];
  if (page === 'instance' || page === 'receipts' || page === 'context' || page === 'catalog') {
    route.page = page;
  }
  if ((page === 'instance' || page === 'receipts') && isUuid(segments[1] || '')) {
    route.objectId = segments[1].toLowerCase();
  }
  const rev = query.get('rev');
  if (isUuid(rev || '')) route.revisionId = rev.toLowerCase();
  const receipt = query.get('r');
  if (isUuid(receipt || '')) route.receiptId = receipt.toLowerCase();
  const snap = query.get('snap');
  if (isUuid(snap || '')) route.snapshotId = snap.toLowerCase();
  const type = query.get('type');
  if (isKnownType(type)) route.type = type;
  return route;
}
