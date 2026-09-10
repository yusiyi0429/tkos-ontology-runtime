// 当前对象导航状态（lib/focus.js）的回归用例：跨页连续性、换对象清空、
// 授权读取回填防迟到、切身份清空、目录选择记忆。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createFocus, clearFocus, openObject, recordRead, selectRevision, navRoute, rememberCatalog,
} from '../../workbench/lib/focus.js';

const WI = 'c8df3507-d1e3-4701-956e-45c6e9f77f6f';
const OC = 'dd84d8dc-69a3-4b22-9741-d0183e2f32f9';
const R1 = '1206a8a1-d722-4f47-852e-2782767ddfde';
const R2 = '53685780-9e1b-4405-b9a7-d5449c0bb733';

test('打开对象后导航保持同一对象与已选 revision', () => {
  const focus = createFocus();
  // 无对象时回退页面默认（演练锚点由页面 fallback）。
  assert.deepEqual(navRoute(focus, 'instance'), { page: 'instance' });
  assert.deepEqual(navRoute(focus, 'receipts'), { page: 'receipts' });
  openObject(focus, WI);
  selectRevision(focus, R1);
  // 实例 → 回执 → 实例：同一对象，实例带回用户选过的 revision，回执不带 revision。
  assert.deepEqual(navRoute(focus, 'receipts'), { page: 'receipts', objectId: WI });
  assert.deepEqual(navRoute(focus, 'instance'), { page: 'instance', objectId: WI, revisionId: R1 });
  // catalog / context 不携带对象状态。
  assert.deepEqual(navRoute(focus, 'catalog'), { page: 'catalog' });
});

test('换对象立即清掉旧标题与 revision', () => {
  const focus = createFocus();
  openObject(focus, WI, R1);
  recordRead(focus, WI, { title: '交付工作项', objectType: 'WorkItem' });
  openObject(focus, OC);
  assert.equal(focus.objectId, OC);
  assert.equal(focus.title, null);
  assert.equal(focus.objectType, null);
  assert.equal(focus.revisionId, null);
});

test('同对象重复打开保留标题，显式 revision 覆盖选择', () => {
  const focus = createFocus();
  openObject(focus, WI, R1);
  recordRead(focus, WI, { title: '交付工作项', objectType: 'WorkItem' });
  openObject(focus, WI);
  assert.equal(focus.title, '交付工作项');
  assert.equal(focus.revisionId, R1);
  openObject(focus, WI, R2);
  assert.equal(focus.revisionId, R2);
});

test('授权读取成功才回填标题；迟到/异对象的读取不回填', () => {
  const focus = createFocus();
  openObject(focus, WI);
  assert.equal(recordRead(focus, OC, { title: '别的对象' }), false);
  assert.equal(focus.title, null);
  assert.equal(recordRead(focus, WI, { title: '交付工作项', objectType: 'WorkItem', revisionId: R1 }), true);
  assert.equal(focus.title, '交付工作项');
  assert.equal(focus.revisionId, R1);
});

test('切换身份清空对象、标题与目录记忆，不残留先前身份数据', () => {
  const focus = createFocus();
  openObject(focus, WI, R1);
  recordRead(focus, WI, { title: '交付工作项', objectType: 'WorkItem' });
  rememberCatalog(focus, { type: 'WorkItem', domainId: OC });
  clearFocus(focus);
  assert.deepEqual(focus, createFocus());
});

test('目录类型/域选择的小范围记忆', () => {
  const focus = createFocus();
  rememberCatalog(focus, { type: 'WorkItem' });
  rememberCatalog(focus, { domainId: OC });
  assert.equal(focus.catalogType, 'WorkItem');
  assert.equal(focus.catalogDomainId, OC);
});
