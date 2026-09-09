// 快照条件规范化与对象集合比较。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  timeMs, normalizeIds, snapshotObjectIds, currentConditions, snapshotConditions, conditionsMatch,
} from '../../workbench/lib/snapshot.js';

const A = 'c8df3507-d1e3-4701-956e-45c6e9f77f6f';
const B = 'dd84d8dc-69a3-4b22-9741-d0183e2f32f9';
const C = '57544e84-666a-46a6-b3dd-0c09a4a5c790';

test('Date 的毫秒读数不用于精确快照条件比较', () => {
  assert.equal(timeMs('2026-09-09T09:08:21.233Z'), timeMs('2026-09-09T09:08:21.233000+00:00'));
  assert.notEqual(timeMs('2026-09-09T09:08:21.233Z'), timeMs('2026-09-09T09:08:22.233Z'));
  assert.equal(timeMs('bogus'), null);
});

test('刚生成的同条件快照不误判为不同', () => {
  const current = currentConditions({
    validAt: '2026-09-09T09:08:21.233Z',
    knownAt: '2026-09-09T09:08:21.233Z',
    objectIds: [A, B],
  });
  const snap = snapshotConditions({
    valid_at: '2026-09-09T09:08:21.233000+00:00',
    known_at: '2026-09-09T09:08:21.233000+00:00',
    selected: [{ object_id: A }, { object_id: B }],
    excluded: [],
  });
  assert.deepEqual(conditionsMatch(current, snap), { same: true, timeDiffers: false, objectsDiffer: false });
});

test('时间变化标记 timeDiffers', () => {
  const current = currentConditions({ validAt: '2026-09-08T17:00:00Z', knownAt: '2026-09-08T17:00:00Z', objectIds: [A] });
  const snap = snapshotConditions({ valid_at: '2026-09-09T09:08:21.233000+00:00', known_at: '2026-09-09T09:08:21.233000+00:00', selected: [{ object_id: A }], excluded: [] });
  const match = conditionsMatch(current, snap);
  assert.equal(match.same, false);
  assert.equal(match.timeDiffers, true);
  assert.equal(match.objectsDiffer, false);
});

test('对象集合变化标记 objectsDiffer（顺序与大小写无关）', () => {
  const current = currentConditions({ validAt: '2026-09-09T09:08:21.233Z', knownAt: '2026-09-09T09:08:21.233Z', objectIds: [B.toUpperCase(), A] });
  const same = snapshotConditions({ valid_at: '2026-09-09T09:08:21.233000+00:00', known_at: '2026-09-09T09:08:21.233000+00:00', selected: [{ object_id: A }, { object_id: B }], excluded: [] });
  assert.equal(conditionsMatch(current, same).same, true);
  const different = snapshotConditions({ valid_at: '2026-09-09T09:08:21.233000+00:00', known_at: '2026-09-09T09:08:21.233000+00:00', selected: [{ object_id: A }, { object_id: C }], excluded: [] });
  const match = conditionsMatch(current, different);
  assert.equal(match.same, false);
  assert.equal(match.objectsDiffer, true);
});

test('GET 回读快照从 selected+excluded 还原对象集合', () => {
  const ids = snapshotObjectIds({
    selected: [{ object_id: B }],
    excluded: [{ object_id: A, reason: 'no_valid_revision' }, { object_id: C }],
  });
  assert.deepEqual(ids, normalizeIds([A, B, C]));
});

test('POST 时优先使用请求集合而非响应还原', () => {
  const data = { valid_at: '2026-09-09T09:08:21Z', known_at: '2026-09-09T09:08:21Z', selected: [{ object_id: A }], excluded: [] };
  const withRequest = snapshotConditions(data, [A, B]);
  assert.deepEqual(withRequest.ids, normalizeIds([A, B]));
});
