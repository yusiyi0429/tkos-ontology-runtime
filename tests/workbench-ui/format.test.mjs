// 展示枚举翻译：已知值给中文，未知值回退原始字符串，绝不臆造。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  lifecycleLabel, outcomeAchievementLabel, verificationLabel, actionLabel,
  criterionLabel, creationModeLabel, fmtTime,
} from '../../workbench/lib/format.js';

test('Outcome 达成读 outcome_achievement 语义', () => {
  assert.equal(outcomeAchievementLabel('not_assessed').label, '待评估');
  assert.equal(outcomeAchievementLabel('achieved').label, '已达成');
  assert.equal(outcomeAchievementLabel('something_new').label, 'something_new');
});

test('lifecycle 未知值回退原始 status', () => {
  assert.equal(lifecycleLabel('delivery_accepted').label, '交付验收通过');
  assert.equal(lifecycleLabel('brand_new_status').label, 'brand_new_status');
  assert.equal(lifecycleLabel('brand_new_status').raw, 'brand_new_status');
});

test('评审/动作/标准/创建方式翻译与回退', () => {
  assert.equal(verificationLabel('changes_requested').label, '退回补充');
  assert.equal(actionLabel('review_deliverable').label, '评审交付物');
  assert.equal(actionLabel('unknown_action').label, 'unknown_action');
  assert.equal(criterionLabel('failed').label, '未通过');
  assert.equal(creationModeLabel('dedicated_action').label, '专用动作进入');
});

test('fmtTime 可解析则格式化，否则原样返回', () => {
  assert.match(fmtTime('2026-09-09T08:46:46.531650+00:00'), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  assert.equal(fmtTime('not-a-date'), 'not-a-date');
  assert.equal(fmtTime(null), '—');
});
