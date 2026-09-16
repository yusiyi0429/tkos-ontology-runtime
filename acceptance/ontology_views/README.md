# 本体视图独立验收

先按 `acceptance/dashboard_0_3/README.md` 在新隔离库中通过合法动作准备数据。
启动本轮源码 API，使用该隔离 fixture 的 CEO 身份配置仅限本机的看板 facade。
不要使用生产身份、生产库或较早版本的镜像冒充本轮代码。

```sh
uv run --extra s3 python -m acceptance.ontology_views.run \
  --url http://127.0.0.1:<isolated-api-port> \
  --env-file <private-isolated-env.json> \
  --identities <dashboard-0-3-private-dir>/identities.json \
  --private .runtime-acceptance/ontology-observer/private \
  --output .runtime-acceptance/ontology-observer/report
```

观察器只做 GET、拒绝路径探测与只读 SQL 快照，不准备或修改业务数据。
它核对版本化目录、所有注册类型的分页、内容版本号、身份与游标边界，以及浏览前后数据库一致性。
报告只包含检查名、通过数和失败数；环境文件、身份文件与原始日志不得提交。

该脚本不代替浏览器验收。还须在真实页面验证默认本体地图、五区展开、业务说明卡、
类型到实例联动、真实关联与旧依据、版本切换、搜索缩放、键盘与窄屏布局。
