# Offline deployment

每个架构包固定包含五类 Linux 镜像：Runtime API、Runtime Worker、PostgreSQL 17＋pgvector、MinIO Server、MinIO Client。Clark、Nginx、模型服务、凭据、数据库备份和业务数据不在包内。

## 1. 校验并加载

选择与目标机器一致的 `linux-amd64` 或 `linux-arm64` 包。先校验下载目录中的 `SHA256SUMS`，再执行：

```bash
tar -xzf tkos-ontology-runtime-v0.3.0-linux-ARCH.tar.gz
cd tkos-ontology-runtime-v0.3.0-linux-ARCH
python3 deploy/offline-release/verify_bundle.py . --load
```

校验器拒绝缺少组件、额外镜像、架构不符、内部哈希不符或危险归档路径。`--load` 只写入本机镜像库，不启动服务。

## 2. 配置

```bash
cd deploy/offline-release
cp .env.example .env
chmod 600 .env
mkdir -m 700 private
printf '%s' 'REPLACE_WITH_APP_ACCESS_KEY' > private/minio-app-access-key
printf '%s' 'REPLACE_WITH_APP_SECRET_KEY' > private/minio-app-secret-key
chmod 600 private/*
```

替换 `.env` 中全部 `CHANGE_ME`。`MINIO_RETENTION_DAYS` 默认 30 天，初始化会为证据桶设置 GOVERNANCE 默认保留期；版本化及对象锁本身不足以满足 Runtime 的证据保留校验。应按组织要求设置该值，重跑初始化会更新新写入对象的默认保留期，不缩短已写入版本的既有保留期。密码建议使用只含字母和数字的高熵值，避免 `.env` 解析歧义。应用密钥文件不能与 MinIO root 凭据相同。Compose 仅把 Runtime 端口绑定到 `127.0.0.1`；公网 TLS、域名和反向代理由宿主机 Nginx 管理。

## 3. 首次启动

```bash
./start-offline.sh .env
```

脚本按顺序启动数据服务、创建 MinIO 桶和受限应用账号、建立数据库 owner／app 双角色、显式迁移、收紧授权，然后启动 API／Worker。重复执行迁移和初始化是幂等的。数据位于两个具名卷；停止服务不要加 `-v`。

本包不会创建企业正式 scope、人员身份、角色分配或 Method Profile 激活记录。启动健康只证明基础设施和进程就绪；M1A／M1B 业务使用前，仍需通过控制 CLI 导入经确认的身份与授权，并在目标环境完成备份恢复、业务回归及 Nginx 切换验收。
