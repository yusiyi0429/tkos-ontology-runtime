// 过期请求防护：epoch + AbortController。快速切换身份/对象/分页时，
// 旧请求立即 abort，迟到的响应通过 token 比对丢弃，绝不回填到新视图。

export class Epoch {
  constructor() { this.value = 0; }
  next() { this.value += 1; return this.value; }
  isCurrent(token) { return token === this.value; }
}

export class RequestGuard {
  constructor() {
    this.epoch = new Epoch();
    this.controller = null;
  }
  // 开始一轮新渲染：作废旧请求，返回 {token, signal}。
  begin() {
    const token = this.epoch.next();
    if (this.controller) this.controller.abort();
    this.controller = new AbortController();
    return { token, signal: this.controller.signal };
  }
  isCurrent(token) {
    return this.epoch.isCurrent(token);
  }
  cancel() {
    this.epoch.next();
    if (this.controller) this.controller.abort();
    this.controller = null;
  }
}

export function isAbort(error) {
  return Boolean(error) && (error.name === 'AbortError' || error.code === 'ABORT_ERR');
}
