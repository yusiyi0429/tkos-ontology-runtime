# 交付候选的干净环境安装验收

票 #32 批次 E。从一个提交出发，按 `docs/deployment.md` 与 `deploy/offline-release/` 的做法走完：

1. 在临时 git worktree 里检出该提交，工作区里未提交、未跟踪的文件都不会带进去；
2. 准备 wheelhouse，并从该检出构建本项目的 wheel；
3. 构建 runtime 与 worker 镜像，核对镜像上的版本与提交；
4. 在全新卷上起一套离线栈，完成空库迁移、授权与 API/Worker 启动；
5. 核对迁移清单与每个文件的摘要，用镜像里的材料安装 Method 0.4、0.5 与 world 0.1，并做一次带凭证的读取；
6. 用已发布的 v0.4.0 镜像建库，再用候选镜像升级到迁移头；
7. 在同一检出上跑无库测试。

结果写成一份交付清单：提交、wheel 摘要、镜像 id、迁移与摘要、协议安装、升级、测试结果。

```bash
.venv/bin/python acceptance/delivery_candidate/run.py --commit HEAD \
  --private .runtime-acceptance/candidate-$(date +%Y%m%d-%H%M%S) \
  --output artifacts/runtime-acceptance/candidate-$(date +%Y%m%d-%H%M%S)
```

**前提**：

- 本机有离线包用的 PostgreSQL/pgvector、MinIO、mc 镜像（`tkos/offline-*:v0.4.0-<arch>`）与已发布的 `tkos/ontology-runtime:v0.4.0-<arch>`；
- 能从容器访问 PyPI 下载依赖 wheel；
- 只在本机隔离运行，不连现有栈，不碰 54350/54351。

**凭证**：只在 `--private` 与临时 worktree 里。跑完删除临时栈的卷与 worktree，清单里的口令值会被抹掉。

**这不是发布**：它证明「按文档能从干净源码装起来、升得上去」，不代替目标环境的部署、恢复与业务回归。
