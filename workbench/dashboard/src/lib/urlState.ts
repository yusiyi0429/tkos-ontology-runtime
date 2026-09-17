/** URL state: the selected strategy/object/version/filters survive reload. */

export interface ViewState {
  view: string
  strategy: string | null
  object: string | null
  rev: string | null
  group: string
  basis: string
  objectType: string | null
  domain: string | null
  periodFrom: string | null
  periodTo: string | null
  owner: string | null
  rules: string
  otype: string | null
}

export const DEFAULT_VIEW: ViewState = {
  view: "map",
  strategy: null,
  object: null,
  rev: null,
  group: "strategy",
  basis: "all",
  objectType: null,
  domain: null,
  periodFrom: null,
  periodTo: null,
  owner: null,
  rules: "0.4",
  otype: null,
}

const KEYS: Array<[keyof ViewState, string]> = [
  ["view", "view"],
  ["strategy", "strategy"],
  ["object", "object"],
  ["rev", "rev"],
  ["group", "group"],
  ["basis", "basis"],
  ["objectType", "object_type"],
  ["domain", "domain"],
  ["periodFrom", "period_from"],
  ["periodTo", "period_to"],
  ["owner", "owner"],
  ["rules", "rules"],
  ["otype", "otype"],
]

/**
 * Legacy standalone object-list routes fold into the ontology map: an exact
 * object keeps its detail route, a bare list route opens the map.  The legacy
 * type filter selects the same type in the map, and every other filter
 * parameter stays in the URL instead of being silently dropped.
 */
export function normalizeView(view: ViewState): ViewState {
  const next: ViewState = view.view === "list"
    ? { ...view, view: view.object ? "graph" : "map" }
    : { ...view }
  if (!next.otype && next.objectType) next.otype = next.objectType
  return next
}

export function parseView(search: string): ViewState {
  const params = new URLSearchParams(search)
  const view: ViewState = { ...DEFAULT_VIEW }
  for (const [field, key] of KEYS) {
    const value = params.get(key)
    if (value) (view[field] as string | null) = value
  }
  return normalizeView(view)
}

export function serializeView(view: ViewState): string {
  const params = new URLSearchParams()
  for (const [field, key] of KEYS) {
    const value = view[field]
    if (value) params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ""
}

export function mergeView(current: ViewState, patch: Partial<ViewState>): ViewState {
  const next = { ...current, ...patch }
  // Changing the strategy or the selected object invalidates a pinned revision
  // unless that revision was explicitly part of the patch.
  if (patch.strategy !== undefined && patch.strategy !== current.strategy && patch.rev === undefined) {
    next.rev = null
  }
  if (patch.object !== undefined && patch.object !== current.object && patch.rev === undefined) {
    next.rev = null
  }
  return next
}
