import { useCallback, useEffect, useReducer, useRef } from "react"
import { isAbort, isAccessDenial } from "@/lib/errors"

/** Live read state with foreground polling, abort/epoch guarding and update prompts. */
export interface LiveState<T> {
  data: T | null
  /** The newest successful payload, even while an update prompt is pending.
   *  Identity/authorization changes must be observable before the user accepts. */
  latest: T | null
  pending: T | null
  error: unknown
  stale: boolean
  updatedAt: string | null
  loading: boolean
}

export type LiveAction<T> =
  | { type: "start" }
  | { type: "success"; data: T }
  | { type: "failure"; error: unknown }
  | { type: "acceptPending" }
  | { type: "dismissPending" }
  | { type: "reset" }

export function initialLiveState<T>(): LiveState<T> {
  return { data: null, latest: null, pending: null, error: null, stale: false,
           updatedAt: null, loading: false }
}

/**
 * A new payload with the same logical identity replaces the current data
 * silently; a different identity while content is already displayed is offered
 * as a prompt so a reviewer never loses the exact version being read.
 */
export function liveReducer<T>(
  state: LiveState<T>,
  action: LiveAction<T>,
  identity: (data: T) => string,
): LiveState<T> {
  switch (action.type) {
    case "start":
      return { ...state, loading: state.data === null, error: null }
    case "success": {
      const changed = state.data !== null && identity(state.data) !== identity(action.data)
      const stamp = new Date().toISOString()
      if (changed) {
        return { ...state, latest: action.data, pending: action.data, loading: false,
                 stale: false, updatedAt: stamp }
      }
      return { ...state, data: action.data, latest: action.data, pending: null, error: null,
               stale: false, loading: false, updatedAt: stamp }
    }
    case "failure":
      return { ...state, error: action.error, stale: state.data !== null, loading: false }
    case "acceptPending":
      return state.pending === null ? state : { ...state, data: state.pending, pending: null }
    case "dismissPending":
      return { ...state, pending: null }
    case "reset":
      return initialLiveState<T>()
    default:
      return state
  }
}

export interface LiveResourceOptions<T> {
  key: string | null
  fetcher: (key: string, signal: AbortSignal) => Promise<T>
  identity: (data: T) => string
  intervalMs?: number
  enabled?: boolean
  onAccessDenied?: () => void
}

export interface LiveResource<T> extends LiveState<T> {
  refresh: () => void
  acceptPending: () => void
  dismissPending: () => void
  reset: () => void
}

export function useLiveResource<T>(options: LiveResourceOptions<T>): LiveResource<T> {
  const { key, fetcher, identity, intervalMs = 5000, enabled = true, onAccessDenied } = options
  const [state, rawDispatch] = useReducer(
    (current: LiveState<T>, action: LiveAction<T>) => liveReducer(current, action, identity),
    undefined,
    initialLiveState<T>,
  )
  const epochRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const fetcherRef = useRef(fetcher)
  const identityRef = useRef(identity)
  const onAccessDeniedRef = useRef(onAccessDenied)
  fetcherRef.current = fetcher
  identityRef.current = identity
  onAccessDeniedRef.current = onAccessDenied

  const run = useCallback(async (silent: boolean) => {
    const currentKey = key
    if (!currentKey || !enabled) return
    const epoch = ++epochRef.current
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    if (!silent) rawDispatch({ type: "start" })
    try {
      const data = await fetcherRef.current(currentKey, controller.signal)
      if (epoch !== epochRef.current) return // a late obsolete result is ignored
      rawDispatch({ type: "success", data })
    } catch (error) {
      if (isAbort(error) || epoch !== epochRef.current) return
      if (isAccessDenial(error)) {
        // Authority is gone: never keep prior protected data as "stale".
        // The epoch bump makes every other in-flight response obsolete too.
        epochRef.current += 1
        abortRef.current?.abort()
        onAccessDeniedRef.current?.()
        rawDispatch({ type: "reset" })
        return
      }
      rawDispatch({ type: "failure", error })
    }
  }, [key, enabled])

  useEffect(() => {
    rawDispatch({ type: "reset" })
    if (!key || !enabled) {
      // A cleared/disabled resource still invalidates any late response.
      epochRef.current += 1
      abortRef.current?.abort()
      return
    }
    void run(false)
    return () => {
      epochRef.current += 1
      abortRef.current?.abort()
    }
  }, [run, key, enabled])

  useEffect(() => {
    if (!enabled) return
    const tick = () => {
      if (document.visibilityState === "visible") void run(true)
    }
    const interval = window.setInterval(tick, intervalMs)
    window.addEventListener("focus", tick)
    document.addEventListener("visibilitychange", tick)
    return () => {
      window.clearInterval(interval)
      window.removeEventListener("focus", tick)
      document.removeEventListener("visibilitychange", tick)
    }
  }, [run, enabled, intervalMs])

  const reset = useCallback(() => {
    epochRef.current += 1
    abortRef.current?.abort()
    rawDispatch({ type: "reset" })
  }, [])

  useEffect(() => {
    if (!enabled) reset()
  }, [enabled, reset])

  return {
    ...state,
    refresh: () => void run(false),
    acceptPending: () => rawDispatch({ type: "acceptPending" }),
    dismissPending: () => rawDispatch({ type: "dismissPending" }),
    reset,
  }
}
