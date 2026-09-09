// 通用 payload 拍平：引用字段识别、验收标准、JSON 回退。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { payloadRows, fieldLabel } from '../../workbench/lib/payloadview.js';

const OID = 'c8df3507-d1e3-4701-956e-45c6e9f77f6f';
const RID = '1206a8a1-d722-4f47-852e-2782767ddfde';

test('typed 引用与引用列表被识别为可跳转行', () => {
  const rows = payloadRows({
    title: 'x',
    execution_commitment_ref: { object_id: OID, revision_id: RID },
    upstream_refs: [{ object_id: OID, revision_id: RID }],
  });
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
  assert.equal(byKey.title.kind, 'text');
  assert.equal(byKey.execution_commitment_ref.kind, 'ref');
  assert.equal(byKey.upstream_refs.kind, 'reflist');
});

test('验收标准数组识别为 criteria', () => {
  const rows = payloadRows({ acceptance_criteria: [{ criterion_id: 'complete', description: 'd' }] });
  assert.equal(rows[0].kind, 'criteria');
  assert.equal(rows[0].criteria[0].criterion_id, 'complete');
});

test('嵌套对象回退 JSON 文本，null 显示占位', () => {
  const rows = payloadRows({ terms: { unit: 'deliveries', target: 80 }, feedback_ref: null });
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
  assert.equal(byKey.terms.kind, 'json');
  assert.ok(byKey.terms.text.includes('deliveries'));
  assert.equal(byKey.feedback_ref.text, '—');
});

test('已知键中文标签，未知键保留原名', () => {
  assert.equal(fieldLabel('submission_seq'), '提交序号');
  assert.equal(fieldLabel('custom_field'), 'custom_field');
});

test('非对象输入返回空', () => {
  assert.deepEqual(payloadRows(null), []);
  assert.deepEqual(payloadRows([1, 2]), []);
});
