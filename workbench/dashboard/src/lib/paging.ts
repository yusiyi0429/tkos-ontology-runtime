/** Session list reads with 「加载更多」 that are also refreshed while the page is open. */

export const PAGE_SIZE = 25
// The session list reads accept at most 100 items per request.
const MAX_LIMIT = 100

export type CursorPage<T> = { items: T[]; next_after: string | null }

/** `?limit=…&after=…` for the session list reads. */
export function pageQuery(limit: number, after: string | null): string {
  return `?limit=${limit}${after ? `&after=${encodeURIComponent(after)}` : ""}`
}

/**
 * Re-read a list from its start until it covers the `shown` items the person
 * already loaded.  A refresh (or 「加载更多」 with a larger `shown`) thereby
 * replaces the whole visible window under current authorization, instead of
 * falling back to the first page and dropping what was loaded.
 */
export async function readWindow<T>(read: (after: string | null, limit: number) => Promise<CursorPage<T>>,
                                    shown: number): Promise<CursorPage<T>> {
  const wanted = Math.max(PAGE_SIZE, shown)
  const items: T[] = []
  let after: string | null = null
  for (;;) {
    const page = await read(after, Math.min(MAX_LIMIT, wanted - items.length))
    items.push(...page.items)
    after = page.next_after
    if (!after || page.items.length === 0 || items.length >= wanted) return { items, next_after: after }
  }
}
