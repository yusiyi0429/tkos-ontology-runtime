// Context Pack 请求体：双时间截面、UUID 集合校验、毫秒精度。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildContextPackBody, normalizeTimeInput, toLocalInputValue } from '../../workbench/lib/contextbody.js';

const A = 'c8df3507-d1e3-4701-956e-45c6e9f77f6f';
const B = 'dd84d8dc-69a3-4b22-9741-d0183e2f32f9';

test('双时间分别归一化为 ISO，互不影响', () => {
  const body = buildContextPackBody({
    objectIds: [A, B],
    validAt: '2026-09-08T17:00:00.000',
    knownAt: '2026-09-09T09:08:21.233',
  });
  assert.deepEqual(body.object_ids, [A, B]);
  assert.ok(body.valid_at.endsWith('Z'));
  assert.ok(body.known_at.endsWith('Z'));
  assert.notEqual(body.valid_at, body.known_at);
  assert.ok(body.known_at.includes('.233'));
});

test('校验：空集合 / 非 UUID / 重复 / 超 100', () => {
  assert.throws(() => buildContextPackBody({ objectIds: [], validAt: '2026-09-09T00:00', knownAt: '2026-09-09T00:00' }), /至少选择一个/);
  assert.throws(() => buildContextPackBody({ objectIds: ['bogus'], validAt: '2026-09-09T00:00', knownAt: '2026-09-09T00:00' }), /UUID/);
  assert.throws(() => buildContextPackBody({ objectIds: [A, A.toUpperCase()], validAt: '2026-09-09T00:00', knownAt: '2026-09-09T00:00' }), /重复/);
  const many = Array.from({ length: 101 }, (_, i) => `00000000-0000-0000-0000-${String(i).padStart(12, '0')}`);
  assert.throws(() => buildContextPackBody({ objectIds: many, validAt: '2026-09-09T00:00', knownAt: '2026-09-09T00:00' }), /100/);
});

test('缺时间或无法解析时抛错，不静默补值', () => {
  assert.throws(() => buildContextPackBody({ objectIds: [A], validAt: '', knownAt: '2026-09-09T00:00' }), /时间/);
  assert.throws(() => normalizeTimeInput('not-a-time'), /无法解析/);
});

test('toLocalInputValue 保留毫秒（step=0.001）', () => {
  const value = toLocalInputValue(new Date(2026, 8, 9, 17, 8, 21, 233));
  assert.match(value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$/);
  assert.ok(value.endsWith('.233'));
});
