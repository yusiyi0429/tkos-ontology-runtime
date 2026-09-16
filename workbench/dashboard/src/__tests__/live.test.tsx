import { act, renderHook, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { ApiError } from "@/lib/errors"
import { useLiveResource } from "@/lib/live"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe("useLiveResource", () => {
  it("ignores an abort-ignoring late response after the key becomes null", async () => {
    const pending = deferred<{ id: string }>()
    const fetcher = vi.fn((_key: string, _signal: AbortSignal) => pending.promise)
    const { result, rerender } = renderHook(
      ({ key }: { key: string | null }) =>
        useLiveResource({ key, fetcher, identity: (data) => data.id }),
      { initialProps: { key: "a" as string | null } })
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
    rerender({ key: null })
    await act(async () => {
      pending.resolve({ id: "late" })
      await pending.promise
    })
    expect(result.current.data).toBeNull()
    expect(result.current.pending).toBeNull()
  })

  it("ignores a late response from a previous key", async () => {
    const first = deferred<{ id: string }>()
    const second = deferred<{ id: string }>()
    const fetcher = vi.fn((key: string, _signal: AbortSignal) =>
      key === "a" ? first.promise : second.promise)
    const { result, rerender } = renderHook(
      ({ key }: { key: string }) =>
        useLiveResource({ key, fetcher, identity: (data) => data.id }),
      { initialProps: { key: "a" } })
    rerender({ key: "b" })
    await act(async () => {
      first.resolve({ id: "old" })
      second.resolve({ id: "new" })
      await Promise.all([first.promise, second.promise])
    })
    expect(result.current.data).toEqual({ id: "new" })
  })

  it("clears protected content on 403/404 access denial and never keeps it stale", async () => {
    const onAccessDenied = vi.fn()
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ id: "secret" })
      .mockRejectedValueOnce(new ApiError(404, "NOT_FOUND", "The requested record is unavailable."))
    const { result } = renderHook(() => useLiveResource({
      key: "detail", fetcher, identity: (data: { id: string }) => data.id, onAccessDenied }))
    await waitFor(() => expect(result.current.data).toEqual({ id: "secret" }))
    act(() => { result.current.refresh() })
    await waitFor(() => expect(result.current.data).toBeNull())
    expect(result.current.stale).toBe(false)
    expect(onAccessDenied).toHaveBeenCalledTimes(1)
  })

  it("clears on an unavailable viewer (503) as well", async () => {
    const onAccessDenied = vi.fn()
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ id: "secret" })
      .mockRejectedValueOnce(new ApiError(503, "DASHBOARD_VIEWER_UNAVAILABLE", "viewer"))
    const { result } = renderHook(() => useLiveResource({
      key: "overview", fetcher, identity: (data: { id: string }) => data.id, onAccessDenied }))
    await waitFor(() => expect(result.current.data).toEqual({ id: "secret" }))
    act(() => { result.current.refresh() })
    await waitFor(() => expect(result.current.data).toBeNull())
    expect(onAccessDenied).toHaveBeenCalled()
  })

  it("keeps clearly stale data for a recoverable storage error under the same identity", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ id: "one" })
      .mockRejectedValueOnce(new ApiError(503, "EVIDENCE_UNAVAILABLE", "storage unavailable"))
    const { result } = renderHook(() =>
      useLiveResource({ key: "list", fetcher, identity: (data: { id: string }) => data.id }))
    await waitFor(() => expect(result.current.data).toEqual({ id: "one" }))
    act(() => { result.current.refresh() })
    await waitFor(() => expect(result.current.stale).toBe(true))
    expect(result.current.data).toEqual({ id: "one" })
  })

  it("offers a changed identity as a prompt instead of silently replacing content", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ id: "v1" })
      .mockResolvedValueOnce({ id: "v2" })
    const { result } = renderHook(() =>
      useLiveResource({ key: "detail", fetcher, identity: (data: { id: string }) => data.id }))
    await waitFor(() => expect(result.current.data).toEqual({ id: "v1" }))
    act(() => { result.current.refresh() })
    await waitFor(() => expect(result.current.pending).toEqual({ id: "v2" }))
    expect(result.current.data).toEqual({ id: "v1" })
    act(() => { result.current.acceptPending() })
    expect(result.current.data).toEqual({ id: "v2" })
  })

  it("clears immediately and refetches when the resource generation key changes", async () => {
    const fetcher = vi.fn()
      .mockImplementation(async (key: string) => ({ id: key }))
    const { result, rerender } = renderHook(
      ({ key }: { key: string }) =>
        useLiveResource({ key, fetcher, identity: (data: { id: string }) => data.id }),
      { initialProps: { key: "a" } })
    await waitFor(() => expect(result.current.data).toEqual({ id: "a" }))
    rerender({ key: "b" })
    await waitFor(() => expect(result.current.data).toEqual({ id: "b" }))
  })

  it("disabled resources never repopulate after being cleared", async () => {
    const pending = deferred<{ id: string }>()
    const fetcher = vi.fn((_key: string, _signal: AbortSignal) => pending.promise)
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useLiveResource({ key: "a", fetcher, identity: (data: { id: string }) => data.id, enabled }),
      { initialProps: { enabled: true } })
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    rerender({ enabled: false })
    await act(async () => {
      pending.resolve({ id: "late" })
      await pending.promise
    })
    expect(result.current.data).toBeNull()
  })
})
