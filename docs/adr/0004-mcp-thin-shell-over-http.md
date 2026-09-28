---
status: accepted
date: 2026-09-24
---

# MCP server 只是 HTTP 读写面的薄壳，不直接访问数据库

Agent 经 MCP 读写业务世界。我们决定 MCP server 作为仓库内独立包运行，持一个 Agent 身份的凭证，所有读取与写入都经现有 HTTP 服务转发；它不连数据库、不做判权、不做事务。写入所需的三项声明（所属场景、触发事件、是否人工验收及验收人）由 HTTP 服务校验，MCP 只透传。

## Considered Options

- MCP 直连数据库以省一跳：被否，认证栅栏、单事务、行级安全与回执都在 HTTP 服务里，第二条写路径会绕开它们。

## Consequences

- MCP 能做的事严格等于 HTTP 面暴露的事；新增工具先加 HTTP 端点。
- MCP 的运行日志（每次工具调用与返回的引用）是实验指标的数据来源，需要单独落盘。
