// 交付轨迹推导：真实 delivery 投影 → 时序节点 + 回执跳转 ID。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildDeliveryTimeline } from '../../workbench/lib/timeline.js';

const DRILL_DELIVERY = {
  state: {
    accepted_at: '2026-09-09T08:46:46.789205+00:00',
    latest_submission_revision_id: 'rev-v2',
  },
  submissions: [
    { revision_id: 'rev-v2', action_id: 'act-sub-2', recorded_at: '2026-09-09T08:46:47.365440+00:00', payload: { submission_seq: 2 } },
    { revision_id: 'rev-v1', action_id: 'act-sub-1', recorded_at: '2026-09-09T08:46:46.956996+00:00', payload: { submission_seq: 1 } },
  ],
  acceptances: [
    { acceptance_id: 'acc-2', action_id: 'act-rev-2', verification_result: 'accepted', submission_seq: 2, recorded_at: '2026-09-09T08:46:47.580759+00:00' },
    { acceptance_id: 'acc-1', action_id: 'act-rev-1', verification_result: 'changes_requested', submission_seq: 1, recorded_at: '2026-09-09T08:46:47.120103+00:00' },
  ],
};

test('按真实时间排序：承接 → 提交 v1 → 退回 → 提交 v2 → 验收', () => {
  const nodes = buildDeliveryTimeline(DRILL_DELIVERY);
  assert.deepEqual(nodes.map((n) => n.label), ['承接工作项', '提交 v1', '退回补充 v1', '提交 v2', '验收通过 v2']);
  assert.deepEqual(nodes.map((n) => n.kind), ['accept', 'submission', 'review_changes_requested', 'submission', 'review_accepted']);
});

test('节点 receiptId 来自真实 action_id，承接节点无 action_id 时为 null', () => {
  const nodes = buildDeliveryTimeline(DRILL_DELIVERY);
  assert.equal(nodes[0].receiptId, null);
  assert.equal(nodes[1].receiptId, 'act-sub-1');
  assert.equal(nodes[2].receiptId, 'act-rev-1');
  assert.equal(nodes[4].receiptId, 'act-rev-2');
});

test('空/缺失 delivery 返回空数组，无时间的节点被过滤', () => {
  assert.deepEqual(buildDeliveryTimeline(null), []);
  assert.deepEqual(buildDeliveryTimeline({}), []);
  const nodes = buildDeliveryTimeline({ submissions: [{ revision_id: 'x', payload: { submission_seq: 1 } }] });
  assert.deepEqual(nodes, []);
});

test('未知评审结果不虚构文案', () => {
  const nodes = buildDeliveryTimeline({
    acceptances: [{ acceptance_id: 'a', action_id: 'x', verification_result: 'something_new', submission_seq: 3, recorded_at: '2026-09-09T00:00:00Z' }],
  });
  assert.equal(nodes[0].kind, 'review_other');
  assert.equal(nodes[0].label, '评审 v3');
});
