// API 客户端：同源本地 QA 代理 '/{actor}/v1/...'。浏览器不接触任何凭据。
import { isAbort } from './guard.js';

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || 'UNKNOWN';
  }
}

// 统一的中文错误描述；retryable 决定是否展示重试按钮。
export function describeError(error) {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401: return { title: '身份未通过认证（401）', detail: '本地代理未能解析当前演练身份凭据。', retryable: true };
      case 403: return { title: '当前身份不具备本次请求所需授权（403）', detail: '服务端按当前身份与 assignment 判定；切换演练身份或更换对象后可重试。', retryable: true };
      case 404: return { title: '对象不存在或当前不可读（404）', detail: '服务对「不存在」与「无权限」统一返回 404，不代表数据一定不存在。', retryable: false };
      case 422: return { title: '请求不符合接口契约（422）', detail: error.message || '参数未被服务端接受。', retryable: false };
      case 0: return { title: '网络请求失败', detail: '无法连接本地代理，请确认本地 QA 代理正在运行。', retryable: true };
      default: return { title: `请求失败（HTTP ${error.status}）`, detail: error.message || '未知错误。', retryable: true };
    }
  }
  if (isAbort(error)) return { title: '请求已取消', detail: '视图已切换，本次读取被放弃。', retryable: false };
  return { title: '请求失败', detail: error?.message || '未知错误。', retryable: true };
}

async function parseError(response) {
  let code = 'UNKNOWN';
  let message = `HTTP ${response.status}`;
  try {
    const body = await response.json();
    if (body && body.error) {
      code = body.error.code || code;
      message = body.error.message || message;
    }
  } catch { /* 非 JSON 错误体（如代理 405 HTML），保留状态码。 */ }
  return new ApiError(response.status, code, message);
}

// 会话缓存：存 Promise 但失败即淘汰，重试不会反复读到同一个被拒绝的 Promise。
export function cachedJson(cache, key, fetcher) {
  if (cache.has(key)) return cache.get(key);
  const promise = fetcher().catch((error) => {
    cache.delete(key);
    throw error;
  });
  cache.set(key, promise);
  return promise;
}

export function createClient({ apiRoot, actor, fetchImpl = fetch }) {
  const base = `${apiRoot.replace(/\/?$/, '/')}${actor}`;
  async function getJson(path, { signal } = {}) {
    let response;
    try {
      response = await fetchImpl(`${base}${path}`, { signal, cache: 'no-store', headers: { Accept: 'application/json' } });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError(0, 'NETWORK', error?.message);
    }
    if (!response.ok) throw await parseError(response);
    return response.json();
  }
  async function postJson(path, body, { signal } = {}) {
    let response;
    try {
      response = await fetchImpl(`${base}${path}`, {
        method: 'POST',
        signal,
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError(0, 'NETWORK', error?.message);
    }
    if (!response.ok) throw await parseError(response);
    return response.json();
  }
  // 证据字节：返回原始字节与响应头（etag 即服务端 hash）。
  async function getBytes(path, { signal } = {}) {
    let response;
    try {
      response = await fetchImpl(`${base}${path}`, { signal, cache: 'no-store' });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError(0, 'NETWORK', error?.message);
    }
    if (!response.ok) throw await parseError(response);
    const buffer = await response.arrayBuffer();
    return {
      bytes: new Uint8Array(buffer),
      contentType: response.headers.get('content-type') || 'application/octet-stream',
      etag: (response.headers.get('etag') || '').replace(/^"|"$/g, ''),
      length: Number(response.headers.get('content-length') || buffer.byteLength),
    };
  }
  return { getJson, postJson, getBytes, actor };
}
