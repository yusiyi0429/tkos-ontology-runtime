# 历史 Memory Compose 参考

`compose.yaml` 从原 Memory Service 基线保留，用于来源追溯。它引用旧镜像，且没有本期治理 Runtime 所需的受限应用角色、身份/策略初始化、版本化对象存储与外部接收器配置。

不要把它当作当前 Runtime 的远程部署入口。本地功能验证使用根目录 README 中的独立验收流程；远程试点要求见 `docs/deployment.md`。
