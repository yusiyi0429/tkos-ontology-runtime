// 展示层枚举翻译：仅做界面文案映射，未知取值一律回退到原始字符串，绝不臆造业务结论。

export const LIFECYCLE_LABELS = {
  draft: '草稿',
  confirmed: '已确认',
  active: '生效中',
  open: '待路由',
  routed: '已路由',
  accepted: '已受理',
  investigating: '跟进中',
  closed: '已关闭',
  delivery_in_progress: '交付进行中',
  delivery_submitted: '交付待验收',
  delivery_changes_requested: '交付退回补充',
  delivery_accepted: '交付验收通过',
};

export const ACTION_LABELS = {
  create_object: '创建对象',
  accept_work_item: '承接工作项',
  submit_deliverable: '提交交付物',
  review_deliverable: '评审交付物',
  confirm_outcome: '确认目标版本',
  record_outcome_assessment: '记录成果评估',
  activate_commitment: '生效承诺',
  confirm_decision: '确认决策',
  upload_evidence: '登记证据',
};

export const VERIFICATION_LABELS = {
  accepted: '验收通过',
  changes_requested: '退回补充',
};

export const CRITERION_LABELS = {
  passed: '通过',
  failed: '未通过',
};

export const CREATION_MODE_LABELS = {
  generic_action: '通用动作创建',
  dedicated_action: '专用动作进入',
  control_plane_only: '仅治理登记',
};

export const RECEIPT_STATUS_LABELS = {
  committed: '已提交（committed）',
};

export const OUTCOME_ACHIEVEMENT_LABELS = {
  not_assessed: '待评估',
  assessed: '已评估',
  achieved: '已达成',
  not_achieved: '未达成',
};

export function translate(map, raw) {
  if (raw === null || raw === undefined || raw === '') return { label: '（空）', raw: String(raw ?? '') };
  const key = String(raw);
  return { label: map[key] || key, raw: key, known: Boolean(map[key]) };
}

export function lifecycleLabel(raw) { return translate(LIFECYCLE_LABELS, raw); }
export function actionLabel(raw) { return translate(ACTION_LABELS, raw); }
export function verificationLabel(raw) { return translate(VERIFICATION_LABELS, raw); }
export function criterionLabel(raw) { return translate(CRITERION_LABELS, raw); }
export function creationModeLabel(raw) { return translate(CREATION_MODE_LABELS, raw); }

// 状态着色仅用于视觉区分，不表达任何跨对象推断。
export function lifecycleKind(raw) {
  if (raw === 'delivery_accepted' || raw === 'closed') return 'good';
  if (raw === 'delivery_changes_requested' || raw === 'open' || raw === 'routed') return 'wait';
  if (raw === 'active' || raw === 'confirmed' || raw === 'investigating' || raw === 'accepted') return 'info';
  return '';
}

export function verificationKind(raw) {
  if (raw === 'accepted') return 'good';
  if (raw === 'changes_requested') return 'wait';
  return '';
}

export function criterionKind(raw) {
  if (raw === 'passed') return 'good';
  if (raw === 'failed') return 'fail';
  return '';
}

export function outcomeAchievementLabel(raw) { return translate(OUTCOME_ACHIEVEMENT_LABELS, raw); }

export function outcomeAchievementKind(raw) {
  if (raw === 'achieved') return 'good';
  if (raw === 'not_achieved') return 'fail';
  if (raw === 'not_assessed' || raw === 'assessed') return 'wait';
  return '';
}

const TIME_PAD = (n) => String(n).padStart(2, '0');

export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return `${d.getFullYear()}-${TIME_PAD(d.getMonth() + 1)}-${TIME_PAD(d.getDate())} ` +
    `${TIME_PAD(d.getHours())}:${TIME_PAD(d.getMinutes())}:${TIME_PAD(d.getSeconds())}`;
}

export function shortId(id) {
  if (!id) return '—';
  return String(id).slice(0, 8);
}
