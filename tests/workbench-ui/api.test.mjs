// API 客户端：错误映射、no-store、会话缓存失败淘汰。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createClient, cachedJson, describeError, ApiError } from '../../workbench/lib/api.js';

function fakeFetch(response) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (response instanceof Error) throw response;
    return response;
  };
  fn.calls = calls;
  return fn;
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: new Map(),
  };
}

test('GET 路径拼 actor 前缀，带 no-store 与 Accept', async () => {
  const fetchImpl = fakeFetch(jsonResponse(200, { items: [] }));
  const client = createClient({ apiRoot: '/', actor: 'verifier', fetchImpl });
  await client.getJson('/v1/domains');
  assert.equal(fetchImpl.calls[0].url, '/verifier/v1/domains');
  assert.equal(fetchImpl.calls[0].init.cache, 'no-store');
  assert.equal(fetchImpl.calls[0].init.headers.Accept, 'application/json');
});

test('404 错误体映射为 ApiError，携带服务端 code', async () => {
  const fetchImpl = fakeFetch(jsonResponse(404, { error: { code: 'NOT_FOUND', message: 'The requested record is unavailable.' } }));
  const client = createClient({ apiRoot: '/', actor: 'outsider', fetchImpl });
  await assert.rejects(client.getJson('/v1/objects/x'), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 404);
    assert.equal(error.code, 'NOT_FOUND');
    return true;
  });
});

test('非 JSON 错误体（代理 405 HTML）不丢失状态码', async () => {
  const fetchImpl = fakeFetch({ ok: false, status: 405, json: async () => { throw new Error('not json'); }, headers: new Map() });
  const client = createClient({ apiRoot: '/', actor: 'ceo', fetchImpl });
  await assert.rejects(client.getJson('/v1/domains'), (error) => error.status === 405);
});

test('网络失败映射为 status 0；abort 原样抛出', async () => {
  const client = createClient({ apiRoot: '/', actor: 'ceo', fetchImpl: fakeFetch(new TypeError('fetch failed')) });
  await assert.rejects(client.getJson('/v1/domains'), (error) => error.status === 0 && error.code === 'NETWORK');
  const abort = new Error('aborted');
  abort.name = 'AbortError';
  const client2 = createClient({ apiRoot: '/', actor: 'ceo', fetchImpl: fakeFetch(abort) });
  await assert.rejects(client2.getJson('/v1/domains'), (error) => error.name === 'AbortError');
});

test('403 文案是通用事实描述，不断言 assignment 状态', () => {
  const info = describeError(new ApiError(403, 'FORBIDDEN', 'denied'));
  assert.match(info.title, /不具备本次请求所需授权/);
  assert.ok(!JSON.stringify(info).includes('失去全部'));
});

test('cachedJson：失败（含 abort）即淘汰，重试发新请求', async () => {
  const cache = new Map();
  let calls = 0;
  const fetcher = () => {
    calls += 1;
    return calls === 1 ? Promise.reject(new Error('HTTP 500')) : Promise.resolve({ items: ['fresh'] });
  };
  await assert.rejects(cachedJson(cache, 'k', fetcher));
  assert.equal(cache.has('k'), false);
  const result = await cachedJson(cache, 'k', fetcher);
  assert.deepEqual(result, { items: ['fresh'] });
  assert.equal(calls, 2);
  // 成功结果被复用
  await cachedJson(cache, 'k', fetcher);
  assert.equal(calls, 2);
});

test('POST 双时间请求体原样发出', async () => {
  const fetchImpl = fakeFetch(jsonResponse(200, { context_snapshot_id: 's' }));
  const client = createClient({ apiRoot: '/', actor: 'ceo', fetchImpl });
  const body = { object_ids: ['a'], valid_at: '2026-09-09T00:00:00Z', known_at: '2026-09-09T01:00:00Z' };
  await client.postJson('/v1/context-packs', body);
  assert.equal(fetchImpl.calls[0].init.method, 'POST');
  assert.equal(fetchImpl.calls[0].init.cache, 'no-store');
  assert.deepEqual(JSON.parse(fetchImpl.calls[0].init.body), body);
});
