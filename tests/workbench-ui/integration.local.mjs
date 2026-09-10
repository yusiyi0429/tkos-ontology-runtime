// A1 本地联调：驱动 workbench lib 客户端走四页真实 GET 流程。
import { createHash } from 'node:crypto';
import { createClient, cachedJson } from '../../workbench/lib/api.js';
import { casePathToRoute, buildQuery } from '../../workbench/lib/url.js';
import { buildDeliveryTimeline } from '../../workbench/lib/timeline.js';
import { snapshotObjectIds, currentConditions, snapshotConditions, conditionsMatch } from '../../workbench/lib/snapshot.js';

const endpoint = new URL(process.env.TKOS_WORKBENCH_URL || 'http://127.0.0.1:8032/');
if (endpoint.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(endpoint.hostname)
    || !endpoint.port || endpoint.username || endpoint.password || endpoint.pathname !== '/'
    || endpoint.search || endpoint.hash) throw new Error('必须指定本机回环工作台根地址');
const ROOT = endpoint.href;
const results = [];
const ok = (name, cond, extra = '') => results.push([name, cond ? 'PASS' : 'FAIL', extra]);
const configResponse = await fetch(new URL('case.json', ROOT));
if (!configResponse.ok) throw new Error('演练配置读取失败');
const config = await configResponse.json();
const hidden = (actor, error) => actor === 'outsider' && [403, 404].includes(error.status);

for (const actor of ['ceo', 'mission_dri', 'verifier', 'outsider']) {
  const client = createClient({ apiRoot: ROOT, actor });
  const cache = new Map();
  const cached = (key, path) => cachedJson(cache, key, () => client.getJson(path));

  try {
    // 页 01：类型目录 + 域（分页）+ 对象列表
    const types = await cached('object-types', '/v1/object-types');
    ok(`${actor} object-types 10 种业务类型及治理登记哨兵`, types.items?.length === 11
      && types.items.find((item) => item.object_type === 'ProtocolSentinel')?.creation_mode === 'control_plane_only',
      `got ${types.items?.length}`);
    const domains = [];
    let cursor = null;
    do {
      const page = await client.getJson(`/v1/domains${buildQuery({ limit: 100, cursor })}`);
      domains.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    ok(`${actor} domains 分页读取`, domains.length >= 1, `${domains.length} 域`);
    if (domains.length) {
      const objects = await client.getJson(`/v1/objects${buildQuery({ domain_id: domains[0].domain_id, limit: 20 })}`);
      ok(`${actor} objects 列表`, Array.isArray(objects.items), `${objects.items?.length} 条`);
    }
  } catch (error) {
    ok(`${actor} 页01`, false, `${error.status} ${error.code}`);
  }

  // 页 02：锚点对象 + revisions + relations + responsibility + 轨迹
  const wiId = casePathToRoute(config.state_paths['交付']).objectId;
  const ocId = casePathToRoute(config.state_paths['Outcome']).objectId;
  try {
    const wi = await client.getJson(`/v1/objects/${wiId}`);
    const revs = await client.getJson(`/v1/objects/${wiId}/revisions${buildQuery({ limit: 20 })}`);
    const rid = wi.latest_revision_id;
    const rev = await client.getJson(`/v1/objects/${wiId}/revisions/${rid}`);
    const rels = await client.getJson(`/v1/objects/${wiId}/relations${buildQuery({ revision_id: rid })}`);
    const resp = await client.getJson(`/v1/objects/${wiId}/responsibility`);
    const timeline = buildDeliveryTimeline(wi.delivery);
    ok(`${actor} WorkItem 详情链`, actor !== 'outsider' && revs.items?.length >= 1
      && rev.payload_hash?.length === 64 && resp.dri?.role === 'MISSION_DRI'
      && resp.protocol?.interpretation_status === 'legacy_v0_2');
    ok(`${actor} 交付轨迹`, timeline.length === 5, timeline.map((n) => n.label).join(' → '));
    ok(`${actor} relations`, Array.isArray(rels.items));
    // Outcome 卡语义
    const oc = await client.getJson(`/v1/objects/${ocId}`);
    ok(`${actor} outcome_achievement`, oc.outcome_achievement === 'not_assessed', String(oc.outcome_achievement));
  } catch (error) {
    ok(`${actor} 页02 权限`, hidden(actor, error), `${error.status} ${error.code}`);
  }

  // 页 03：回执列表 + 单条
  try {
    const receipts = await client.getJson(`/v1/objects/${wiId}/action-receipts${buildQuery({ limit: 20 })}`);
    const first = receipts.items?.[receipts.items.length - 1];
    const detail = first ? await client.getJson(`/v1/action-receipts/${first.receipt_id}`) : null;
    ok(`${actor} 回执`, actor !== 'outsider' && receipts.items?.length === 6 && detail?.receipt?.status === 'committed', `${receipts.items?.length} 条`);
  } catch (error) {
    ok(`${actor} 页03 权限`, hidden(actor, error), `${error.status}`);
  }

  // 页 04：快照回读 + 条件比较
  const snapId = casePathToRoute(config.cases.find(([l]) => l.includes('Context Pack'))?.[1] || '').snapshotId;
  if (snapId) {
    try {
      const snap = await client.getJson(`/v1/context-packs/${snapId}`);
      const ids = snapshotObjectIds(snap);
      const match = conditionsMatch(
        currentConditions({ validAt: snap.valid_at, knownAt: snap.known_at, objectIds: ids }),
        snapshotConditions(snap),
      );
      ok(`${actor} 快照回读`, actor !== 'outsider' && snap.selected?.length >= 1 && match.same, `对象 ${ids.length} 个, same=${match.same}`);
    } catch (error) {
      ok(`${actor} 页04 权限`, hidden(actor, error), `${error.status}`);
    }
  } else {
    ok(`${actor} 快照配置完整`, false);
  }
}

// 证据字节（ceo）
try {
  const client = createClient({ apiRoot: ROOT, actor: 'ceo' });
  const reference = config.evidence?.at(-1);
  if (!reference?.object_id || !reference?.revision_id || !reference?.sha256) throw new Error('演练配置缺少证据引用与 hash');
  const ev = await client.getBytes(`/v1/evidence-assets/${reference.object_id}/revisions/${reference.revision_id}`);
  const digest = createHash('sha256').update(ev.bytes).digest('hex');
  ok('证据字节', /^text\//.test(ev.contentType) && digest === reference.sha256 && ev.etag === digest,
    `${ev.bytes.length}B sha256=${digest.slice(0, 12)}…`);
} catch (error) {
  ok('证据字节', false, `${error.status} ${error.code}`);
}

let failed = 0;
for (const [name, status, extra] of results) {
  if (status === 'FAIL') failed += 1;
  console.log(`${status} ${name}${extra ? ` — ${extra}` : ''}`);
}
console.log(`\n${results.length - failed}/${results.length} PASS`);
process.exit(failed ? 1 : 0);
