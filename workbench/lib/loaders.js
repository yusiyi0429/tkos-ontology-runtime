// 子请求守卫与竞态安全加载器。
// 同页快速切换筛选/选中项时，旧请求立即 abort，迟到响应经 token 比对丢弃。

export class SlotGuard {
  constructor() {
    this.token = 0;
    this.controller = null;
  }
  begin() {
    this.token += 1;
    if (this.controller) this.controller.abort();
    this.controller = new AbortController();
    return { token: this.token, signal: this.controller.signal };
  }
  isCurrent(token) {
    return token === this.token;
  }
  cancel() {
    this.begin();
  }
}

export function combineSignals(...signals) {
  const list = signals.filter(Boolean);
  if (!list.length) return undefined;
  if (typeof AbortSignal !== 'undefined' && AbortSignal.any) return AbortSignal.any(list);
  return list[0];
}

function isAbortError(error) {
  return Boolean(error) && (error.name === 'AbortError' || error.code === 'ABORT_ERR');
}

// keyset 分页加载器。reset() 作废旧请求并清空 cursor；乱序到达的旧响应不写入 pager。
export class PagedLoader {
  constructor({ fetchPage, pageSize = 20 }) {
    this.fetchPage = fetchPage;
    this.pageSize = pageSize;
    this.guard = new SlotGuard();
    this.reset();
  }
  reset() {
    this.items = [];
    this.nextCursor = null;
    this.started = false;
    this.done = false;
    this.loading = false;
    this.error = null;
    this.meta = null;
    this.guard.begin();
  }
  get hasMore() {
    return this.started && !this.done && !this.error;
  }
  get isEmpty() {
    return this.started && !this.loading && !this.error && this.items.length === 0;
  }
  // baseParams 为当次筛选快照；返回 'applied' | 'stale' | 'error' | 'done'。
  async loadMore(baseParams = {}, outerSignal) {
    if (this.done || this.loading) return { status: 'done' };
    const params = { ...baseParams, limit: this.pageSize };
    if (this.nextCursor) params.cursor = this.nextCursor;
    const { token, signal } = this.guard.begin();
    this.loading = true;
    this.error = null;
    let page;
    try {
      page = await this.fetchPage(params, combineSignals(signal, outerSignal));
    } catch (error) {
      if (!this.guard.isCurrent(token) || isAbortError(error)) return { status: 'stale' };
      this.loading = false;
      this.error = error;
      return { status: 'error', error };
    }
    if (!this.guard.isCurrent(token)) return { status: 'stale' };
    const items = Array.isArray(page?.items) ? page.items : [];
    this.items.push(...items);
    this.nextCursor = page?.next_cursor ?? null;
    this.meta = page || null;
    this.started = true;
    this.loading = false;
    if (!this.nextCursor) this.done = true;
    return { status: 'applied', added: items.length };
  }
}

// 单值详情加载器（回执详情 / 证据字节）：连续选择时旧响应不覆盖新选中项。
export class ValueLoader {
  constructor(fetchValue) {
    this.fetchValue = fetchValue;
    this.guard = new SlotGuard();
    this.key = null;
    this.value = null;
    this.error = null;
    this.loading = false;
  }
  clear() {
    this.guard.begin();
    this.key = null;
    this.value = null;
    this.error = null;
    this.loading = false;
  }
  async load(key, outerSignal) {
    const { token, signal } = this.guard.begin();
    this.key = key;
    this.value = null;
    this.error = null;
    this.loading = true;
    let value;
    try {
      value = await this.fetchValue(key, combineSignals(signal, outerSignal));
    } catch (error) {
      if (!this.guard.isCurrent(token) || isAbortError(error)) return { status: 'stale' };
      this.loading = false;
      this.error = error;
      return { status: 'error', error };
    }
    if (!this.guard.isCurrent(token)) return { status: 'stale' };
    this.value = value;
    this.loading = false;
    return { status: 'applied' };
  }
}
