# 本机 M1B 治理工作台独立验收

此入口只修改 Runtime。Clark 接线、真实模型、远程部署和企业 SSO 不在此次验收内。
使用全新数据库及 scope，真实 API、真实 PostgreSQL、独立个人浏览器会话。Agent 驱动只使用受控输出，不提供人类代行 Agent 的接口。

## 复跑

先按 `acceptance/anchors_v03/README.md` 建立隔离数据库和独立 API 角色，将私有配置保存为 `.runtime-acceptance/governance/db/env.json`。不要输出配置内容。

```sh
uv run python -m acceptance.governance_workbench.bootstrap \
  --env-file .runtime-acceptance/governance/db/env.json \
  --private .runtime-acceptance/governance/scenario
uv run python -m acceptance.governance_workbench.serve \
  --env-file .runtime-acceptance/governance/db/env.json \
  --private .runtime-acceptance/governance/scenario
uv run python -m acceptance.governance_workbench.check_http \
  --url http://127.0.0.1:58803 \
  --env-file .runtime-acceptance/governance/db/env.json \
  --private .runtime-acceptance/governance/scenario
```

打开 http://127.0.0.1:58803/dashboard/ 。个人用户名和随机登录码分别保存在私有目录 `ceo-login.json`、`dri-a-login.json`、`dri-b-login.json`。用独立浏览器 profile 登录，禁止将三个账号当成身份切换下拉菜单。登录码、token、原始 HTTP 记录不得提交。

CEO 建立月度核对场景；两位 DRI 发表、替代和撤回意见。随后单次运行独立 Agent 验收驱动：

```sh
uv run python -m acceptance.governance_workbench.consolidate \
  --url http://127.0.0.1:58803 \
  --env-file .runtime-acceptance/governance/db/env.json \
  --private .runtime-acceptance/governance/scenario
```

该驱动仅供新窗口的受控验收，不是生产恢复编排器。随后 DRI 核对差异，CEO 审阅完整候选集合并确认，DRI 在正式 Mission 查看结果。点击刷新、重启服务并重新登录，核对数据和“我的提交”仍在。

HTTP 验收生成另一个合法窗口，不消费最初的浏览器窗口。`api-checks/summary.json` 仅记录脱敏检查结果。响应丢失检查模拟客户端忘记结果并重放原信封；异常提交恢复另有单元测试，不冒充网络故障注入。

## 当前源码容器验收

运行 `./scripts/build_dashboard.sh`、`uv build` 后，将**本轮生成**的 wheel 放入已按 `requirements.lock` 准备的本机架构 wheelhouse（依赖 wheel 可复用，Runtime wheel 不可复用旧版本），再构建：

```sh
docker build --target runtime \
  --build-context wheelhouse=.runtime-acceptance/governance/wheelhouse \
  --build-arg VERSION=0.3.0 --build-arg VCS_REF=acea4d4-governance-working-tree \
  -t tkos-runtime:governance-local .
uv run python -m acceptance.governance_workbench.container \
  --env-file .runtime-acceptance/governance/db/env.json \
  --private .runtime-acceptance/governance/scenario \
  --image tkos-runtime:governance-local
```

本机映射 `127.0.0.1:58804`。容器以当前非 root 用户运行以读取私有挂载文件，只挂载三个人的凭据和命令日志，不挂载 Agent 或数据库 owner 凭据。复用现有隔离 PG/MinIO，不启动重复基础组件。源码进程与容器二选一作为日常入口；启用新入口后应停止本轮临时源码进程。记录实际源码 wheel 哈希和镜像 ID；这不是正式发布。
