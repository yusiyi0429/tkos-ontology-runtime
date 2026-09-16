import type { ExactRef, Missing } from "@/lib/types"

export function isMissing(value: unknown): value is Missing {
  return Boolean(value) && typeof value === "object" && (value as Missing).status === "missing"
}

export function shortId(value: string | null | undefined, length = 8): string {
  if (!value) return "—"
  return value.length <= length ? value : `${value.slice(0, length)}…`
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "未记录"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function formatDay(value: string | null | undefined): string {
  if (!value) return "未记录"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`
}

export function formatPeriod(period: { start: string; end: string } | null | undefined): string {
  if (!period) return "未记录"
  return `${formatDay(period.start)} 至 ${formatDay(period.end)}`
}

export function formatRef(ref: ExactRef | null | undefined): string {
  if (!ref) return "未记录"
  return shortId(ref.object_id)
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "未记录"
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (Array.isArray(value)) return value.map(formatValue).join("；")
  return JSON.stringify(value)
}

export function relativeToNow(value: string | null | undefined): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 5) return "刚刚"
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  return formatTime(value)
}
