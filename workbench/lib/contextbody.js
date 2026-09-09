// Context Pack POST 请求体构造与校验：双时间截面 + 1..100 个互不重复 UUID。
import { isUuid } from './url.js';

export function normalizeTimeInput(value) {
  if (value === null || value === undefined || value === '') {
    throw new Error('请填写有效时间与知悉时间。');
  }
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) throw new Error('时间格式无法解析。');
    return value.toISOString();
  }
  const text = String(value).trim();
  // datetime-local 给出 'YYYY-MM-DDTHH:mm'（本地时区）；其余按 Date 解析。
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) throw new Error(`时间格式无法解析：${text}`);
  return d.toISOString();
}

// datetime-local 输入框值（本地时区，保留毫秒，与 step=0.001 对应）。
export function toLocalInputValue(date = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${String(date.getMilliseconds()).padStart(3, '0')}`;
}

export function buildContextPackBody({ objectIds, validAt, knownAt }) {
  const ids = Array.isArray(objectIds) ? objectIds.map((x) => String(x).trim().toLowerCase()) : [];
  if (ids.length === 0) throw new Error('请至少选择一个对象。');
  if (ids.length > 100) throw new Error('一次最多选择 100 个对象。');
  const seen = new Set();
  for (const id of ids) {
    if (!isUuid(id)) throw new Error(`对象 ID 不是有效 UUID：${id}`);
    if (seen.has(id)) throw new Error(`对象 ID 重复：${id}`);
    seen.add(id);
  }
  return {
    object_ids: ids,
    valid_at: normalizeTimeInput(validAt),
    known_at: normalizeTimeInput(knownAt),
  };
}
