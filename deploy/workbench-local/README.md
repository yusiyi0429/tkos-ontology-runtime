# 四页工作台本地接入

适用于已在 `127.0.0.1:8031/8032` 运行的 `tkos-workbench-local` 验证栈。
保留现有 API 调试入口 `/`，工作台入口 `/workbench/`。不连接生产或修改 Clark。

在 Runtime 仓库根目录运行：

```sh
.venv/bin/python deploy/workbench-local/install.py
docker exec tkos-workbench-local-api-viewer-1 nginx -t
docker exec tkos-workbench-local-api-viewer-1 nginx -s reload
```

复用既有私有配置中的四个合成演练身份映射，不向浏览器发送凭据。只有
`POST /{actor}/v1/context-packs` 可写入审计快照，且需要本机页面 Origin；
其余写操作返回 405。Host 受限且仅监听 loopback，此身份切换器不得用作正式用户登录。

原始只读配置备份为 `.runtime-acceptance/workbench-local-container/nginx.before-workbench.conf`。
安装脚本不停止容器、不操作数据卷，也不重启 Runtime API。所有私有配置留在忽略目录。
