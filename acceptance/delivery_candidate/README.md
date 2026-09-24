# 交付候选的干净环境安装验收

票 #32 批次 E。从一个提交出发，按 `docs/deployment.md` 与 `deploy/offline-release/` 的做法走完：

1. 在临时 git worktree 里检出该提交，工作区里未提交、未跟踪的文件都不会带进去；版本号取该提交的 `pyproject.toml`；
2. 准备 wheelhouse，并从该检出构建本项目的 wheel；
3. 构建 runtime 与 worker 镜像，核对镜像上的版本与提交；
4. 在全新卷上起一套离线栈，完成空库迁移、授权与 API/Worker 启动；
5. 在这套栈上：
   - 核对迁移清单与每个文件的摘要；
   - 只用镜像里 `/opt/tkos/docs` 的材料，经控制面四步（profile、scope 默认策略、支持登记、激活策略）把 Method 0.4、0.5 与 world 0.1 各装进一个合成 scope（`install_protocols.py`，在候选镜像里运行）；
   - 经 HTTP 办真实动作：Method 0.5 由 CEO 登记并确认一条公司级约束（0.5 独有动作），再读回自己的回执；world 0.1 由 CEO 建一个 Company；
   - Method 0.4 只核对分派：装了 0.4 的 scope 由 0.4 的规则答复，没装 0.4 的 scope 在协议层拒绝。0.4 的第一步要 CEO Agent 与运行记录，成功路径由 `acceptance/method_v04/` 覆盖；
6. 升级：已发布的 v0.4.0 镜像里的迁移文件与候选的同名文件逐字节一致；用 v0.4.0 镜像建库、迁到它的迁移头，再用候选镜像升级，只补上之后的迁移，旧行回填的摘要就是 v0.4.0 文件的摘要；
7. 在同一检出上跑无库测试。

结果写成一份交付清单：提交、wheel 摘要、镜像 id、迁移与摘要、协议安装与动作、升级、测试结果，以及 `not_covered`（库测试与各验收矩阵另跑，不在这里）。

```bash
.venv/bin/python acceptance/delivery_candidate/run.py --commit HEAD \
  --private .runtime-acceptance/candidate-$(date +%Y%m%d-%H%M%S) \
  --output artifacts/runtime-acceptance/candidate-$(date +%Y%m%d-%H%M%S)
```

**前提**：

- 本机有离线包用的 PostgreSQL/pgvector、MinIO、mc 镜像（`tkos/offline-*:v0.4.0-<arch>`）与已发布的 `tkos/ontology-runtime:v0.4.0-<arch>`；
- 能从容器访问 PyPI 下载依赖 wheel；
- 只在本机隔离运行，不连现有栈，不碰 54350/54351。

**凭证**：只在 `--private`、临时 worktree 与 0600 的 `--env-file` 里，不上 `docker run` 的命令行。清单与打印的错误里的口令和凭证都会先抹掉。

**收尾**：删除临时栈（含卷）、worktree 与候选镜像，各步的退出码记在清单的 `teardown` 里；要保留镜像就加 `--keep-images`。

**这不是发布**：它证明「按文档能从干净源码装起来、升得上去」，不代替目标环境的部署、恢复与业务回归。
