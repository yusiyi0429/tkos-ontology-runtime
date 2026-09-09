// 通用 payload 展示：把任意对象 payload 拍平成展示行，引用字段单独标出以便跳转。
// 纯函数，不接触 DOM；渲染层负责 esc()。

export const FIELD_LABELS = {
  title: '标题',
  summary: '摘要',
  upstream_refs: '上游引用',
  work_item_ref: '所属工作项',
  execution_commitment_ref: '执行承诺',
  feedback_ref: '来源反馈',
  evidence_revision_ids: '证据 revision',
  submission_seq: '提交序号',
  responds_to_acceptance_id: '回应的评审',
  acceptance_criteria: '冻结验收标准',
  dri_assignment_id: '责任 DRI assignment',
  acceptor_assignment_id: '验收人 assignment',
  terms: '指标口径',
  description: '说明',
  changes: '调整项',
  feedback_revision_id: '来源反馈 revision',
  decision_revision_id: '依据决策 revision',
};

export function fieldLabel(key) {
  return FIELD_LABELS[key] || key;
}

function isRef(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
    && typeof value.object_id === 'string' && typeof value.revision_id === 'string';
}

function isCriterionList(value) {
  return Array.isArray(value) && value.length > 0 && value.every(
    (item) => item && typeof item === 'object' && typeof item.criterion_id === 'string',
  );
}

// 返回 [{key, label, kind, text?, ref?, refs?, criteria?}]
export function payloadRows(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return [];
  const rows = [];
  for (const [key, value] of Object.entries(payload)) {
    const label = fieldLabel(key);
    if (isRef(value)) {
      rows.push({ key, label, kind: 'ref', ref: value });
    } else if (isCriterionList(value)) {
      rows.push({ key, label, kind: 'criteria', criteria: value });
    } else if (Array.isArray(value) && value.length > 0 && value.every(isRef)) {
      rows.push({ key, label, kind: 'reflist', refs: value });
    } else if (value === null || value === undefined) {
      rows.push({ key, label, kind: 'text', text: '—' });
    } else if (typeof value === 'object') {
      rows.push({ key, label, kind: 'json', text: JSON.stringify(value, null, 2) });
    } else {
      rows.push({ key, label, kind: 'text', text: String(value) });
    }
  }
  return rows;
}
