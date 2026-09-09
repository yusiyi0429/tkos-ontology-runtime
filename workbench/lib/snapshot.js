// 快照条件规范化与比较：统一时区，保留 Runtime 微秒精度，不把相近时刻视为同一条件。
// 条件包含 valid_at / known_at 与对象 ID 集合。显示时保留服务端原始 ISO 字符串。
import { isUuid } from './url.js';

export function timeMs(iso) {
  if (!iso) return null;
  const ms = Date.parse(String(iso));
  return Number.isNaN(ms) ? null : ms;
}

// Date.parse 只保留毫秒；额外保留小数部分的后三位，避免回读历史快照时误报条件相同。
export function timeMicros(iso) {
  const ms = timeMs(iso);
  if (ms === null) return null;
  const fraction = String(iso).match(/T\d{2}:\d{2}:\d{2}\.(\d+)/)?.[1] || '';
  const remainder = fraction.padEnd(6, '0').slice(3, 6);
  return (BigInt(ms) * 1000n + BigInt(remainder || '0')).toString();
}

export function normalizeIds(ids) {
  return [...new Set((ids || []).map((x) => String(x).trim().toLowerCase()).filter(isUuid))].sort();
}

// 从快照响应还原请求对象集合（selected + excluded）。
export function snapshotObjectIds(data) {
  const selected = (data?.selected || []).map((item) => item.object_id);
  const excluded = (data?.excluded || []).map((item) => item.object_id);
  return normalizeIds([...selected, ...excluded]);
}

// 当前输入条件（datetime-local 或 ISO 字符串 + 选中对象集合）。
export function currentConditions({ validAt, knownAt, objectIds }) {
  return {
    validMs: timeMs(validAt),
    validMicros: timeMicros(validAt),
    knownMs: timeMs(knownAt),
    knownMicros: timeMicros(knownAt),
    ids: normalizeIds(objectIds),
  };
}

// 快照自身条件；requestIds 可选（POST 时已知请求集合，优先于响应还原）。
export function snapshotConditions(data, requestIds) {
  return {
    validMs: timeMs(data?.valid_at),
    validMicros: timeMicros(data?.valid_at),
    knownMs: timeMs(data?.known_at),
    knownMicros: timeMicros(data?.known_at),
    ids: normalizeIds(requestIds || snapshotObjectIds(data)),
  };
}

function idsEqual(a, b) {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

// 返回 {same, timeDiffers, objectsDiffer}；时间不完整时不能声称与已存快照条件相同。
export function conditionsMatch(current, snapshot) {
  const complete = [current.validMicros, current.knownMicros, snapshot.validMicros, snapshot.knownMicros].every((value) => value !== null && value !== undefined);
  const timeDiffers = !complete || current.validMicros !== snapshot.validMicros || current.knownMicros !== snapshot.knownMicros;
  const objectsDiffer = !idsEqual(current.ids, snapshot.ids);
  return { same: !timeDiffers && !objectsDiffer, timeDiffers, objectsDiffer };
}
