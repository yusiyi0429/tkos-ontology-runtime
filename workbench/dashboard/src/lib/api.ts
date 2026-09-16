import { ApiError } from "@/lib/errors"
import type { Detail, ObjectsPage, Overview } from "@/lib/types"

export const API_BASE = "/dashboard/api/v1"

export interface ObjectQuery {
  group: string
  basis: string
  strategyId?: string | null
  objectType?: string | null
  domainId?: string | null
  periodFrom?: string | null
  periodTo?: string | null
  ownerId?: string | null
  scopeId?: string | null
  limit?: number
  cursor?: string | null
}

export function objectPath(query: ObjectQuery): string {
  const params = new URLSearchParams()
  params.set("group", query.group)
  params.set("basis", query.basis)
  if (query.strategyId) params.set("strategy_id", query.strategyId)
  if (query.objectType) params.set("object_type", query.objectType)
  if (query.domainId) params.set("domain_id", query.domainId)
  if (query.periodFrom) params.set("period_from", query.periodFrom)
  if (query.periodTo) params.set("period_to", query.periodTo)
  if (query.ownerId) params.set("owner_id", query.ownerId)
  if (query.scopeId) params.set("scope_id", query.scopeId)
  params.set("limit", String(query.limit ?? 25))
  if (query.cursor) params.set("cursor", query.cursor)
  return `/objects?${params.toString()}`
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
    credentials: "same-origin",
    signal,
  })
  if (!response.ok) {
    let code = "UNKNOWN"
    let message = ""
    try {
      const body = (await response.json()) as { error?: { code?: string; message?: string } }
      code = body.error?.code ?? code
      message = body.error?.message ?? message
    } catch {
      // Keep the generic code; the UI never renders server text as content.
    }
    throw new ApiError(response.status, code, message)
  }
  return (await response.json()) as T
}

export const fetchOverview = (strategyId: string | null, signal?: AbortSignal) =>
  getJson<Overview>(`/overview${strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : ""}`, signal)

export const fetchObjects = (query: ObjectQuery, signal?: AbortSignal) =>
  getJson<ObjectsPage>(objectPath(query), signal)

export const fetchDetail = (objectId: string, revisionId: string | null, strategyId: string | null,
                            signal?: AbortSignal) => {
  const params = new URLSearchParams()
  if (revisionId) params.set("revision_id", revisionId)
  if (strategyId) params.set("strategy_id", strategyId)
  const suffix = params.size ? `?${params.toString()}` : ""
  return getJson<Detail>(`/objects/${encodeURIComponent(objectId)}${suffix}`, signal)
}

export const fetchDownstream = (objectId: string, revisionId: string, cursor: string | null,
                                signal?: AbortSignal) => {
  const params = new URLSearchParams({ revision_id: revisionId, limit: "25" })
  if (cursor) params.set("cursor", cursor)
  return getJson<import("@/lib/types").DownstreamPage>(
    `/objects/${encodeURIComponent(objectId)}/downstream?${params.toString()}`, signal)
}

export const fetchRevisions = (objectId: string, cursor: string | null, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: "25" })
  if (cursor) params.set("cursor", cursor)
  return getJson<{ items: import("@/lib/types").HistoryRevision[]; next_cursor: string | null }>(
    `/objects/${encodeURIComponent(objectId)}/revisions?${params.toString()}`, signal)
}

export const fetchObjectReceipts = (objectId: string, cursor: string | null, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: "25" })
  if (cursor) params.set("cursor", cursor)
  return getJson<{ items: Array<Record<string, unknown>>; next_cursor: string | null }>(
    `/objects/${encodeURIComponent(objectId)}/receipts?${params.toString()}`, signal)
}

export function evidenceUrl(objectId: string, revisionId: string): string {
  return `${API_BASE}/evidence-assets/${encodeURIComponent(objectId)}/revisions/${encodeURIComponent(revisionId)}`
}
