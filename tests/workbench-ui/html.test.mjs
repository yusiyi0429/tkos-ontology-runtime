// HTML escaping 与构件安全性。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { esc, tag, panel, idLine, errorBox } from '../../workbench/lib/html.js';

test('esc 转义全部危险字符', () => {
  assert.equal(esc('<script>alert("x")&\'</script>'), '&lt;script&gt;alert(&quot;x&quot;)&amp;&#39;&lt;/script&gt;');
});

test('esc 处理 null/undefined/数字', () => {
  assert.equal(esc(null), '');
  assert.equal(esc(undefined), '');
  assert.equal(esc(42), '42');
});

test('tag/panel 对服务端文本转义', () => {
  const malicious = '<img src=x onerror=alert(1)>';
  assert.ok(!tag(malicious).includes('<img'));
  assert.ok(!panel(malicious, '').includes('<img'));
});

test('idLine 复制值转义且保留原文', () => {
  const html = idLine('ab"cd<');
  assert.ok(html.includes('data-copy="ab&quot;cd&lt;"'));
});

test('errorBox 重试 action 转义', () => {
  const html = errorBox('t', 'd', 'x"onclick=bad()');
  assert.ok(!html.includes('x"onclick'));
});
