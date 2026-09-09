// 竞态与分页生命周期：PagedLoader / ValueLoader 的真实乱序场景。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PagedLoader, ValueLoader } from '../../workbench/lib/loaders.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function abortError() {
  const error = new Error('aborted');
  error.name = 'AbortError';
  return error;
}

test('筛选切换竞态：旧筛选的迟到响应不写入新 pager', async () => {
  const calls = [];
  const loader = new PagedLoader({
    pageSize: 2,
    fetchPage: (params, signal) => {
      const d = deferred();
      calls.push({ params, signal, ...d });
      return d.promise;
    },
  });
  // 旧筛选 domainA 的请求挂起
  const first = loader.loadMore({ domain_id: 'domainA' });
  // 切换到 domainB：reset 作废旧请求
  loader.reset();
  const second = loader.loadMore({ domain_id: 'domainB' });
  // 新筛选先返回
  calls[1].resolve({ items: ['b1', 'b2'], next_cursor: null });
  await second;
  // 旧筛选迟到返回（无视 abort）
  calls[0].resolve({ items: ['a1'], next_cursor: null });
  const staleResult = await first;
  assert.equal(staleResult.status, 'stale');
  assert.deepEqual(loader.items, ['b1', 'b2']);
});

test('旧请求以 AbortError 拒绝时同样视为 stale', async () => {
  const calls = [];
  const loader = new PagedLoader({
    pageSize: 2,
    fetchPage: (params) => {
      const d = deferred();
      calls.push(d);
      return d.promise;
    },
  });
  const first = loader.loadMore({ domain_id: 'domainA' });
  loader.reset();
  calls[0].reject(abortError());
  assert.equal((await first).status, 'stale');
  assert.equal(loader.error, null);
});

test('分页：next_cursor 真正传给下一页，末页 done', async () => {
  const seen = [];
  const loader = new PagedLoader({
    pageSize: 2,
    fetchPage: (params) => {
      seen.push(params);
      if (!params.cursor) return Promise.resolve({ items: ['a', 'b'], next_cursor: 'cursor-2' });
      return Promise.resolve({ items: ['c'], next_cursor: null });
    },
  });
  await loader.loadMore({ domain_id: 'd' });
  assert.equal(loader.hasMore, true);
  await loader.loadMore({ domain_id: 'd' });
  assert.deepEqual(loader.items, ['a', 'b', 'c']);
  assert.equal(loader.done, true);
  assert.equal(seen[1].cursor, 'cursor-2');
  // 完成后不再发请求
  assert.equal((await loader.loadMore({ domain_id: 'd' })).status, 'done');
  assert.equal(seen.length, 2);
});

test('加载中重复触发被 busy 守卫拒绝', async () => {
  const d = deferred();
  let count = 0;
  const loader = new PagedLoader({
    pageSize: 1,
    fetchPage: () => { count += 1; return d.promise; },
  });
  const first = loader.loadMore({});
  assert.equal((await loader.loadMore({})).status, 'done');
  assert.equal(count, 1);
  d.resolve({ items: ['x'], next_cursor: null });
  await first;
});

test('错误可被记录，reset 后可重试成功', async () => {
  let fail = true;
  const loader = new PagedLoader({
    pageSize: 1,
    fetchPage: () => (fail ? Promise.reject(Object.assign(new Error('HTTP 404'), { status: 404 })) : Promise.resolve({ items: ['ok'], next_cursor: null })),
  });
  const result = await loader.loadMore({});
  assert.equal(result.status, 'error');
  assert.equal(loader.error.status, 404);
  fail = false;
  loader.reset();
  assert.equal((await loader.loadMore({})).status, 'applied');
  assert.deepEqual(loader.items, ['ok']);
});

test('ValueLoader：连续选中两条，旧详情响应不覆盖新选中', async () => {
  const calls = new Map();
  const loader = new ValueLoader((key) => {
    const d = deferred();
    calls.set(key, d);
    return d.promise;
  });
  const first = loader.load('receipt-1');
  const second = loader.load('receipt-2');
  // 新选中先返回
  calls.get('receipt-2').resolve({ receipt: 'r2' });
  await second;
  assert.equal(loader.value.receipt, 'r2');
  assert.equal(loader.key, 'receipt-2');
  // 旧选中迟到返回
  calls.get('receipt-1').resolve({ receipt: 'r1' });
  assert.equal((await first).status, 'stale');
  assert.equal(loader.value.receipt, 'r2');
});

test('ValueLoader：clear 后迟到响应不出现', async () => {
  const d = deferred();
  const loader = new ValueLoader(() => d.promise);
  const pending = loader.load('evidence-1');
  loader.clear();
  d.resolve({ bytes: 'old' });
  assert.equal((await pending).status, 'stale');
  assert.equal(loader.value, null);
  assert.equal(loader.key, null);
});
