// 布局重构的行为回归：实例页签默认值（?rev 深链接落版本页、非 WI 直接看内容）
// 与回执详情空态（无回执时清晰空态，不无限 loading）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialInstanceTab } from '../../workbench/pages/instance.js';
import { receiptDetailState } from '../../workbench/pages/receipts.js';

test('实例页签：WorkItem 默认概览，非 WI 默认对象内容', () => {
  assert.equal(initialInstanceTab({ objectType: 'WorkItem', explicitRevision: false }), 'overview');
  assert.equal(initialInstanceTab({ objectType: 'CompanyOutcome', explicitRevision: false }), 'version');
  assert.equal(initialInstanceTab({ objectType: 'EvidenceAsset', explicitRevision: false }), 'version');
});

test('实例页签：显式 ?rev 深链接默认落在版本与内容页', () => {
  assert.equal(initialInstanceTab({ objectType: 'WorkItem', explicitRevision: true }), 'version');
  assert.equal(initialInstanceTab({ objectType: 'Deliverable', explicitRevision: true }), 'version');
});

test('回执详情：列表为空时给空态而不是无限 loading', () => {
  assert.equal(receiptDetailState({
    detailError: false, detailLoading: false, hasDetail: false, listEmpty: true, listSettled: true,
  }), 'empty');
  assert.equal(receiptDetailState({
    detailError: false, detailLoading: false, hasDetail: false, listEmpty: false, listSettled: true,
  }), 'pick');
  assert.equal(receiptDetailState({
    detailError: false, detailLoading: false, hasDetail: false, listEmpty: false, listSettled: false,
  }), 'loading');
  assert.equal(receiptDetailState({
    detailError: false, detailLoading: true, hasDetail: false, listEmpty: false, listSettled: true,
  }), 'loading');
  assert.equal(receiptDetailState({
    detailError: false, detailLoading: false, hasDetail: true, listEmpty: false, listSettled: true,
  }), 'detail');
  assert.equal(receiptDetailState({
    detailError: true, detailLoading: false, hasDetail: false, listEmpty: false, listSettled: true,
  }), 'error');
});
