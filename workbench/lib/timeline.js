// 交付轨迹推导：从真实 delivery 投影（state / submissions / acceptances）生成时序节点。
// 节点的 receiptId 即动作回执 ID（action_id 与 receipt_id 同源），可跳转动作与回执页。

const KIND_LABELS = {
  accept: '承接工作项',
  submission: '提交交付物',
  review_accepted: '验收通过',
  review_changes_requested: '退回补充',
  review_other: '评审',
};

export function buildDeliveryTimeline(delivery) {
  if (!delivery || typeof delivery !== 'object') return [];
  const nodes = [];
  const state = delivery.state;
  if (state?.accepted_at) {
    nodes.push({
      key: `accept:${state.accepted_at}`,
      kind: 'accept',
      label: KIND_LABELS.accept,
      at: state.accepted_at,
      receiptId: null,
      seq: null,
    });
  }
  for (const sub of delivery.submissions || []) {
    const seq = sub?.payload?.submission_seq ?? null;
    nodes.push({
      key: `sub:${sub.revision_id}`,
      kind: 'submission',
      label: seq !== null ? `提交 v${seq}` : KIND_LABELS.submission,
      at: sub.recorded_at,
      receiptId: sub.action_id || null,
      seq,
    });
  }
  for (const acc of delivery.acceptances || []) {
    const seq = acc?.submission_seq ?? null;
    const result = acc?.verification_result;
    const kind = result === 'accepted' ? 'review_accepted'
      : result === 'changes_requested' ? 'review_changes_requested' : 'review_other';
    const base = KIND_LABELS[kind] || KIND_LABELS.review_other;
    nodes.push({
      key: `acc:${acc.acceptance_id}`,
      kind,
      label: seq !== null ? `${base} v${seq}` : base,
      at: acc.recorded_at,
      receiptId: acc.action_id || null,
      seq,
      result: result || null,
    });
  }
  const timeOf = (node) => {
    const ms = Date.parse(node.at || '');
    return Number.isNaN(ms) ? Number.MAX_SAFE_INTEGER : ms;
  };
  return nodes
    .filter((node) => node.at)
    .sort((a, b) => timeOf(a) - timeOf(b));
}
