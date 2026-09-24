# World 0.1 freeze checkpoint

- 时间（UTC）: 2026-09-24T09:35:56.134368+00:00
- 提交: `0a6736ac19eca29ad2bca14099792b3d4adac133`
- 验收：`world_api_accepted: true`，12 组 248 项检查、4 个环境门槛全部通过；摘要见 [world-v01-summary.json](acceptance/world-v01-summary.json)
- 源码清单 SHA256（src/ 下 182 个文件）: `3e2bc742ab34c82a21167128760530e13508d875601364d988f52df432cf7fc9`
- 文件清单：相对基线 ef31b02 改动过的 src/ 文件，加上 world 契约、登记、profile 与支持登记，共 30 个

| 文件 | SHA256 |
|---|---|
| `docs/contracts/tkos-world-0.1.md` | `1a56c60a7210732f51f1bec3d566e725ff27414970ade369bd43aa2fb7792209` |
| `docs/contracts/world-profile-0.1.json` | `88f35b41b0696ed9190492f44df96701b8eafc51171dae83e9a8e3ca8bae5ae4` |
| `docs/contracts/world-registry-0.1.json` | `be6559996c8aa10be130758cbdfbf72e8940b01f6bb2d8f6dbd3a1b54062cefd` |
| `docs/runtime-world-support-0.1.json` | `a12ecd747f24043e7a41952604f80cf3d15f586bdc06fe487c50a39525c8099d` |
| `src/memory_service/__init__.py` | `daebe3cee0580f07074f3ada8e8bed13bc095f8e345f280e0c18a596ba1f3c9d` |
| `src/memory_service_app/migrations/0030_world_v01.sql` | `aae9236d3850736caa1b610e21fba6123059fb21523b0e9f42c07eef5b2ecdf6` |
| `src/memory_service_app/migrations/0031_world_v01_contract_repin.sql` | `35cc9290668fdd92f4c88a1c9003e365e34120e203d260faa0500f35b1c77de1` |
| `src/memory_service_app/migrations/0032_world_v01_revise_repin.sql` | `e72d60a4f00e6e4f16bc1dd04d5dcfe0971c9f633aea04ebcec2a54658bc3ca2` |
| `src/memory_service_app/migrations/0033_world_v01_state_events_repin.sql` | `4cc7e1d12aa775d15f14993f63c8de9f0582cd7c7352b0fbfc2b605fd5a0f552` |
| `src/memory_service_app/migrations/0034_world_v01_assign_owner.sql` | `cb7822387749fb3db0822a9ae3208fa3d58cee3a8897883c10cf1c1b93ebcea8` |
| `src/memory_service_app/migrations/0035_world_v01_gates_repin.sql` | `7cac7f4e7f5f390f9d9d8558c99f3184ba9e9c6dd3ceff164be3b06f6c63cfd3` |
| `src/memory_service_app/migrations/0036_world_v01_context_packs.sql` | `25695f9a7ef17cc702d5754f7887bee2b230ee9beb95ec8213b3da4da56c4924` |
| `src/memory_service_runtime/governed/control.py` | `961915bca39bb41d15f5ddc825d13c80d4fe088840fedd53b9760cfa5a78880f` |
| `src/memory_service_runtime/governed/models.py` | `d4d571d5303e0d7905d4cb1e748125492a44ff865e9a9fb2c32dac869ab27562` |
| `src/memory_service_runtime/governed/profile.py` | `e172fda2f58ac4ab30405de2b658de6a294de7f432ca57991f751da5bc592474` |
| `src/memory_service_runtime/governed/protocol.py` | `f7816c77b519f61dd177c292832d654baa945dda7a8f2cfe30d6704894d07b7d` |
| `src/memory_service_runtime/governed/readers.py` | `6f0ed6244384e0805bd6d9dee0f75cac93b97eb5c05459998ce72ef46518c93e` |
| `src/memory_service_runtime/governed/resources/world-registry-0.1.json` | `be6559996c8aa10be130758cbdfbf72e8940b01f6bb2d8f6dbd3a1b54062cefd` |
| `src/memory_service_runtime/governed/routes.py` | `71b50ecac2ed48b8ad9bd1e0b33162f82e6a02afbdeba73ec48c9379f05e2014` |
| `src/memory_service_runtime/governed/service.py` | `dec9a44bae79e3019ef8b76aed7bf1df7429ac71ddcec3e500f75fb07f47db6b` |
| `src/memory_service_runtime/governed/world_v01.py` | `d64ef7361f58f4d1381fdd1e78165594bdb79952f68842c565cf64b64ca04e80` |
| `src/memory_service_runtime/governed/world_v01_context.py` | `1135821fc9bf10070acbf9f572e5f14e9a84a6a5b5c3d0ccfba31a365e8ca393` |
| `src/memory_service_runtime/governed/world_v01_lifecycle.py` | `0ca0829da4d92933a5f0ec5d1c459c744e20371718be96fb57957dda979c0be3` |
| `src/memory_service_runtime/governed/world_v01_models.py` | `c089b2ad5ab1266c0e4bb0e3790000d47794ebedf426ba2bdbda4862b4a8b0c8` |
| `src/memory_service_runtime/governed/world_v01_profile.py` | `ff2c21afa6a435e9d2b704c51be12978f06b7de8b60056428482df64700d2de3` |
| `src/memory_service_runtime/governed/world_v01_readers.py` | `3c5be31f25a96abda7983c06ab6fc2d06058e74ccdd18a99f33c8166aeb3aa9e` |
| `src/memory_service_runtime/governed/world_v01_registry.py` | `c1bb404ae7cea16f8a04091100186a094fbed3b8b4d43ec16122af15b9277512` |
| `src/tkos_world_mcp/__init__.py` | `0442f09becb18c325c998425e4d905ca728d15df39c183f2046f943a438ec640` |
| `src/tkos_world_mcp/cli.py` | `609f23de2969dca00e01e140d368e94886fa9e836e0c8813cf0adabc480d9471` |
| `src/tkos_world_mcp/server.py` | `fc2736d09d49583b7bd0f1df742c0b35e5c051591824591661734d574caf30de` |
