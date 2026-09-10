// URL hash 路由与 case 路径解析。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isUuid, buildQuery, buildHash, parseHash, casePathToRoute } from '../../workbench/lib/url.js';

const OID = 'c8df3507-d1e3-4701-956e-45c6e9f77f6f';
const RID = '1206a8a1-d722-4f47-852e-2782767ddfde';

test('isUuid 严格校验', () => {
  assert.ok(isUuid(OID));
  assert.ok(isUuid(OID.toUpperCase()));
  assert.ok(!isUuid('not-a-uuid'));
  assert.ok(!isUuid(`${OID}<script>`));
  assert.ok(!isUuid(''));
  assert.ok(!isUuid(null));
});

test('buildQuery 编码并跳过空值', () => {
  assert.equal(buildQuery({ domain_id: OID, cursor: 'a+b=c', limit: 20 }), `?domain_id=${OID}&cursor=a%2Bb%3Dc&limit=20`);
  assert.equal(buildQuery({ a: null, b: '', c: undefined }), '');
});

test('hash 往返：instance + revision', () => {
  const hash = buildHash({ page: 'instance', objectId: OID, revisionId: RID });
  const route = parseHash(hash);
  assert.equal(route.page, 'instance');
  assert.equal(route.objectId, OID);
  assert.equal(route.revisionId, RID);
});

test('hash 往返：receipts/context/catalog', () => {
  assert.deepEqual(
    parseHash(buildHash({ page: 'receipts', objectId: OID, receiptId: RID })),
    { page: 'receipts', objectId: OID, revisionId: null, receiptId: RID, snapshotId: null, type: null },
  );
  assert.equal(parseHash(buildHash({ page: 'context', snapshotId: RID })).snapshotId, RID);
  assert.equal(parseHash(buildHash({ page: 'catalog', type: 'WorkItem' })).type, 'WorkItem');
});

test('parseHash 拒绝非法 ID 与未知类型', () => {
  const route = parseHash(`#/instance/${OID}<script>?rev=bogus&type=Mission`);
  assert.equal(route.objectId, null);
  assert.equal(route.revisionId, null);
  assert.equal(route.type, null);
  assert.equal(parseHash('').page, 'catalog');
  assert.equal(parseHash('#/unknown').page, 'catalog');
});

test('catalog 深链接：ProtocolSentinel 保留、未知类型丢弃', () => {
  // 明确的 hash 深链接必须解析出 ProtocolSentinel。
  const direct = parseHash('#/catalog?type=ProtocolSentinel');
  assert.equal(direct.page, 'catalog');
  assert.equal(direct.type, 'ProtocolSentinel');
  // buildHash 编码后往返一致。
  const hash = buildHash({ page: 'catalog', type: 'ProtocolSentinel' });
  assert.equal(hash, '#/catalog?type=ProtocolSentinel');
  assert.equal(parseHash(hash).type, 'ProtocolSentinel');
  // 未知类型在解析与构造两侧都被丢弃。
  assert.equal(parseHash('#/catalog?type=NoSuchType').type, null);
  assert.equal(buildHash({ page: 'catalog', type: 'NoSuchType' }), '#/catalog');
});

test('casePathToRoute 映射演练路径', () => {
  assert.deepEqual(casePathToRoute(`/v1/objects/${OID}`), { page: 'instance', objectId: OID });
  assert.deepEqual(casePathToRoute(`/v1/objects/${OID}/action-receipts`), { page: 'receipts', objectId: OID });
  assert.deepEqual(casePathToRoute(`/v1/context-packs/${RID}`), { page: 'context', snapshotId: RID });
  assert.deepEqual(casePathToRoute(`/v1/evidence-assets/${OID}/revisions/${RID}`), { page: 'instance', objectId: OID });
  assert.deepEqual(casePathToRoute('/v1/object-types'), { page: 'catalog' });
  assert.deepEqual(casePathToRoute(`/v1/objects/${OID}/relations?revision_id=${RID}`), { page: 'instance', objectId: OID });
});
