# World 0.1 冻结后复验检查点

这是对冻结点 `0a6736a` 之后改动的差异复验（#32），原冻结记录仍在 [world-v01-freeze-checkpoint.md](world-v01-freeze-checkpoint.md)。复验覆盖的改动：

- #29 的运行日志字段；
- #32 批次 D 的取上下文与 MCP 调整；
- 迁移 0037 的授权修复；
- 批次 A、C 对内核的改动：锁与语句上限、证据字节移出栅栏、请求体上限、规范化摘要统一。

验收矩阵的检查项没有增减。MCP 两条检查改为按瘦身后的返回核对；WORLD-01 允许排在 world 迁移之后的 0037。

- 时间（UTC）: 2026-09-24T15:23:16.800158+00:00
- 提交: `5734e0fa4543908f26a88d2cea0f9d792e1d9a9e`
- 验收：`world_api_accepted: true`，12 组 248 项检查、4 个环境门槛全部通过；摘要见 [world-v01-recheck-summary.json](acceptance/world-v01-recheck-summary.json)
- 源码清单 SHA256（src/ 下 185 个文件）: `5131aa2bd168628e8c0f39066011368a55af9bd858de38b1b2e8cb94b14125c7`
- 文件清单：相对基线 ef31b02 改动过的 src/ 文件，加上 world 契约、登记、profile 与支持登记，共 48 个

| 文件 | SHA256 |
|---|---|
| `docs/contracts/tkos-world-0.1.md` | `1a56c60a7210732f51f1bec3d566e725ff27414970ade369bd43aa2fb7792209` |
| `docs/contracts/world-profile-0.1.json` | `88f35b41b0696ed9190492f44df96701b8eafc51171dae83e9a8e3ca8bae5ae4` |
| `docs/contracts/world-registry-0.1.json` | `be6559996c8aa10be130758cbdfbf72e8940b01f6bb2d8f6dbd3a1b54062cefd` |
| `docs/runtime-world-support-0.1.json` | `a12ecd747f24043e7a41952604f80cf3d15f586bdc06fe487c50a39525c8099d` |
| `src/adapter/deps.py` | `17d2d5193722cfac4cdca5a634e2688c327508afeb72f9d8c145a8095d2ec41d` |
| `src/memory_service/__init__.py` | `daebe3cee0580f07074f3ada8e8bed13bc095f8e345f280e0c18a596ba1f3c9d` |
| `src/memory_service_app/dashboard_dist/asset-manifest.json` | `8315c86495dec4bc50bc6c6594a9adc69a062fb53628e0c823308da9bf4299ff` |
| `src/memory_service_app/dashboard_dist/assets/index-Bao8H1y5.css` | `5b7f1ccbf88ae75c144427326d7d251ceb4e3bbce7f134bf528b6a2248c39190` |
| `src/memory_service_app/dashboard_dist/assets/index-CamHqM66.js` | `8ac311c243945b63d8a0c3a9ffa981df9da87196c2e75b2c2660b504a9d2e8eb` |
| `src/memory_service_app/dashboard_dist/index.html` | `f775c6d5e85c0a3fa633f6abcb2b1ff37c76ee36569b79f7e65f91ce613d2fec` |
| `src/memory_service_app/governance_sessions.py` | `58ab52a942ff24e95593cdd1ccb59c503a333849a89101c24bb162e4e2d8f0ac` |
| `src/memory_service_app/health.py` | `17ee666d1aa4d55312f7a0a85a77afc7848401815f3908d9a40d07535d87606e` |
| `src/memory_service_app/limits.py` | `a073a26e5f9188084358bb796d724090d0c243a9be2f122262e27eb9faf7449a` |
| `src/memory_service_app/main.py` | `5174768415a39be18215007250bf1af2fc513191dc5c0f8a81862983346ec809` |
| `src/memory_service_app/migrate.py` | `8067f5e8bf0677eabc0ee770a90fbdac23905a211e3e11c4cd12aa9a5fd18277` |
| `src/memory_service_app/migrations/0030_world_v01.sql` | `aae9236d3850736caa1b610e21fba6123059fb21523b0e9f42c07eef5b2ecdf6` |
| `src/memory_service_app/migrations/0031_world_v01_contract_repin.sql` | `35cc9290668fdd92f4c88a1c9003e365e34120e203d260faa0500f35b1c77de1` |
| `src/memory_service_app/migrations/0032_world_v01_revise_repin.sql` | `e72d60a4f00e6e4f16bc1dd04d5dcfe0971c9f633aea04ebcec2a54658bc3ca2` |
| `src/memory_service_app/migrations/0033_world_v01_state_events_repin.sql` | `4cc7e1d12aa775d15f14993f63c8de9f0582cd7c7352b0fbfc2b605fd5a0f552` |
| `src/memory_service_app/migrations/0034_world_v01_assign_owner.sql` | `cb7822387749fb3db0822a9ae3208fa3d58cee3a8897883c10cf1c1b93ebcea8` |
| `src/memory_service_app/migrations/0035_world_v01_gates_repin.sql` | `7cac7f4e7f5f390f9d9d8558c99f3184ba9e9c6dd3ceff164be3b06f6c63cfd3` |
| `src/memory_service_app/migrations/0036_world_v01_context_packs.sql` | `25695f9a7ef17cc702d5754f7887bee2b230ee9beb95ec8213b3da4da56c4924` |
| `src/memory_service_app/migrations/0037_append_only_grant_repair.sql` | `deb706804a5d1993fde901cac3b8bd7c0a16d312366c87ae113015262cf9907e` |
| `src/memory_service_runtime/governed/bootstrap.py` | `fd8d93ce27e690f7f66267f64386d697484dfdf6ab55a46512cfcac47815c26b` |
| `src/memory_service_runtime/governed/control.py` | `8b86c83c6a6498fb5e39888f9b0255f7fdc8990f5d47bcbe450d37bef8cd0d27` |
| `src/memory_service_runtime/governed/db.py` | `984b574476ccfa1fd8578661fc28972398acdd8e237e3833c5c1eb7c8705ea02` |
| `src/memory_service_runtime/governed/effects.py` | `4786ff6987f47f61a0f3a5ada14af376cc6975583d116ffc41fb630bf8eeb5b3` |
| `src/memory_service_runtime/governed/evidence.py` | `82e0c6b647b072b24959c9e10f787d591754e04addd9cf256bda8f6bddb42aa3` |
| `src/memory_service_runtime/governed/models.py` | `d4d571d5303e0d7905d4cb1e748125492a44ff865e9a9fb2c32dac869ab27562` |
| `src/memory_service_runtime/governed/profile.py` | `e172fda2f58ac4ab30405de2b658de6a294de7f432ca57991f751da5bc592474` |
| `src/memory_service_runtime/governed/protocol.py` | `f7816c77b519f61dd177c292832d654baa945dda7a8f2cfe30d6704894d07b7d` |
| `src/memory_service_runtime/governed/readers.py` | `6f0ed6244384e0805bd6d9dee0f75cac93b97eb5c05459998ce72ef46518c93e` |
| `src/memory_service_runtime/governed/resources/world-registry-0.1.json` | `be6559996c8aa10be130758cbdfbf72e8940b01f6bb2d8f6dbd3a1b54062cefd` |
| `src/memory_service_runtime/governed/routes.py` | `cf010ceea0c718af0f1887364d3dc414479ecae6589d9ed04590e03103acac83` |
| `src/memory_service_runtime/governed/service.py` | `eb30f4f581a9a64e819d7d2cb3fbb6730c0a7a835a3a48b3033a3d26192a6b12` |
| `src/memory_service_runtime/governed/workspace_service.py` | `7583a53ea04c345a2f46cf059a4e0de0c1ade358c6e14d368cec25e391e6b4c1` |
| `src/memory_service_runtime/governed/workspace_v02_service.py` | `40721e8acd3e5973942c76bd90ee7a3489d9616b7786488a37439b6dfdb9968f` |
| `src/memory_service_runtime/governed/world_v01.py` | `d64ef7361f58f4d1381fdd1e78165594bdb79952f68842c565cf64b64ca04e80` |
| `src/memory_service_runtime/governed/world_v01_context.py` | `4ed12ef2ac0e660512b01f3667ee852bbe23fb8a659bf3264df2ed9d39aabc69` |
| `src/memory_service_runtime/governed/world_v01_lifecycle.py` | `0ca0829da4d92933a5f0ec5d1c459c744e20371718be96fb57957dda979c0be3` |
| `src/memory_service_runtime/governed/world_v01_models.py` | `c089b2ad5ab1266c0e4bb0e3790000d47794ebedf426ba2bdbda4862b4a8b0c8` |
| `src/memory_service_runtime/governed/world_v01_profile.py` | `ff2c21afa6a435e9d2b704c51be12978f06b7de8b60056428482df64700d2de3` |
| `src/memory_service_runtime/governed/world_v01_readers.py` | `3c5be31f25a96abda7983c06ab6fc2d06058e74ccdd18a99f33c8166aeb3aa9e` |
| `src/memory_service_runtime/governed/world_v01_registry.py` | `c1bb404ae7cea16f8a04091100186a094fbed3b8b4d43ec16122af15b9277512` |
| `src/memory_service_runtime/observability.py` | `5ec6728783c41b7961d4e6e77525bd0ada1eef0af37449d02c13685f60aec398` |
| `src/tkos_world_mcp/__init__.py` | `0442f09becb18c325c998425e4d905ca728d15df39c183f2046f943a438ec640` |
| `src/tkos_world_mcp/cli.py` | `609f23de2969dca00e01e140d368e94886fa9e836e0c8813cf0adabc480d9471` |
| `src/tkos_world_mcp/server.py` | `68fa55b573656bd9a2af50c5e01aa396c523739385e279b0af75189b4108295d` |
