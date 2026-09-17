# Method 0.4 freeze checkpoint (comment/replace/withdraw fix)

- 时间（UTC）: 2026-09-17T09:33:15.908788+00:00
- HEAD: `acea4d4ed3338f41a7326b5324e3d59b3799b5f5`

| 文件 | SHA256 |
|---|---|
| `src/memory_service_runtime/governed/method_v04.py` | `e168d2331594bc45ffe9edcbdf59c9eb44d997049270b28f6de844ee8ba3445d` |
| `src/memory_service_runtime/governed/method_v04_models.py` | `1a2f86900037e3eb8516fd0bc2c8da1c07a27d6e824a2c4ba892448636ffa666` |
| `src/memory_service_runtime/governed/method_service.py` | `58cd28c9fe116e8becc046916c9a994c3c134d963ab9c84ff2e03ed573202843` |
| `src/memory_service_runtime/governed/method_access.py` | `68fd0b18d45fb4953da28a3eca101d1d9fe8e5da5026eee08896c29921f0e786` |
| `src/memory_service_runtime/governed/method_readers.py` | `34f509899a857a66dbbb2675b512ee53de0f69a479874e368578481687909904` |
| `src/memory_service_runtime/governed/governance.py` | `92a92257328f4013f386e085834d243f2f00eec26206f3c311b82b49e713562d` |
| `src/memory_service_runtime/governed/dashboard.py` | `ec9734ac20ca239036deca9a4c22b37f3e221d8a1d1ff91cdbc20df8611cfc14` |
| `src/memory_service_runtime/governed/protocol.py` | `e5a871e58569e336392016878b9f6d4c7e2782ad34413d56a10f9aae948b1023` |
| `src/memory_service_runtime/governed/models.py` | `20775c9ce219b3d7deafcce654421dc9a8c5601cea8b1d60c8077f1c6613f78c` |
| `src/memory_service_runtime/governed/routes.py` | `e40faa2a7928ca6dff996e6732e36dc5df105a3550cfdf7de92cafd21ba98443` |
| `docs/contracts/tkos-method-0.4.md` | `984c3e09dc9771e29e26aea858d19bb4639bb3d93dc4df12841130ef4f8e44aa` |
| `docs/contracts/method-profile-0.4.json` | `fe22a04b4e164efd369ec198c4fbd0739e579c18588c9177d9dec4a5e9280fc3` |
| `docs/runtime-method-registry-0.4.json` | `c1679cd25641d9a57f6c57964240489411baf74089478ee1b2efa947cde2a8d7` |
| `workbench/dashboard/src/MethodActions.tsx` | `f5550f60a090e7d210fb26bf77c02d0c7c2b69c9f16ccae0a6be4df06661ce8c` |
| `workbench/dashboard/src/lib/ontology.ts` | `958a03351415a6bb5cc5d9cfb5b79cc9d9d528caf4c9d702f66913f698ed5b39` |
| `workbench/dashboard/src/lib/urlState.ts` | `baec45aa6cb25e0e8e27bc65d58a82d4da40957bf238cd1033dd1b5b1a84dd5d` |
| `workbench/dashboard/src/__tests__/MethodActions.test.tsx` | `1ea50036a3f282752946de93cfba084e4add1b5a6a4bee63490191dd3d160c2e` |
| `workbench/dashboard/src/__tests__/ontology-semantics.test.ts` | `0273feb0bcded484f1eabbcdaa326fd1f83e111b5b3c245f1662f868cc2737eb` |
| `workbench/dashboard/src/__tests__/urlState.test.ts` | `b1a9bef56a47b192a1985cd92747cd57440ae9341eb2dab85fda37559f34900d` |
| `workbench/dashboard/src/__tests__/OntologyViews.test.tsx` | `adce59448fb77cf156d08dc7769ed74c6581228120f76b05a63759948ca98661` |
| `workbench/dashboard/src/__tests__/fixtures.ts` | `c8f98cccfed341ca05ba250316a9b556e475e66810667ac5eb212891d62a24db` |
| `tests/governance/test_v04_allowlist.py` | `37c2b80f88487e3cc507079622d51968fd7ee3fc1600d8dcef6fb09137322d40` |
| `tests/dashboard/test_dashboard_logic.py` | `fd6e3477afc854abccb80db4ef7c41849a36bb5db7eb39e7764bfffb819f9a1f` |
| `tests/test_method_v04_models.py` | `a3f90ac7e49cd59a7f67cc041693934c26c462cd7474257da1906fed3af6375d` |
| `acceptance/method_v04/browser_fixture.py` | `e5e48d11c5894ef4f9a5f2958ba5146863b0ce684d0d2862db7a09d2806a4836` |
| `acceptance/method_v04/run.py` | `ce59986ec58a8502f0c81bba8797ae356dcafe3619bd2334d82a4268f5a97938` |
| `acceptance/method_v04/consolidate_browser.py` | `8526f121529192b4da2632dd9aff8c33606192a9935dfe15bec51451cf80e19a` |
| `src/memory_service_app/dashboard_dist/asset-manifest.json` | `c7c9084c53fd915e367dc3800ba82bdddfbaacca2254585ca1a8e2a65712d706` |
| `src/memory_service_app/dashboard_dist/index.html` | `cf4a48448aadfeb673e1d6a386a8f9d579b63f6f6b9254193c444f1511ea7e9c` |
| `src/memory_service_app/dashboard_dist/assets/geist-cyrillic-ext-wght-normal-DjL33-gN.woff2` | `2317fa4bb293c9c0b110e18315d529235c47a0ddd3338cea3d8c7955e927899e` |
| `src/memory_service_app/dashboard_dist/assets/geist-cyrillic-wght-normal-BEAKL7Jp.woff2` | `6894439694946a589d157ece003086960a6a4013d74a813dab7602efdb3d8c09` |
| `src/memory_service_app/dashboard_dist/assets/geist-latin-ext-wght-normal-DC-KSUi6.woff2` | `824f485b5d26e2f2da3c2b236132ece1bc8e4e43373452950bb0e40548b4313f` |
| `src/memory_service_app/dashboard_dist/assets/geist-latin-wght-normal-BgDaEnEv.woff2` | `19f9c92546aa300c312235e3125af1b81394d8db9a4bc4a425cd5b641d2d54e1` |
| `src/memory_service_app/dashboard_dist/assets/geist-vietnamese-wght-normal-6IgcOCM7.woff2` | `8fa40e5d248247735eb97a0bd593b8852440430600d6ba01364c31fe0abc1fe1` |
| `src/memory_service_app/dashboard_dist/assets/index-BRehHRcO.css` | `86701f7cb77a6ef17d10769302077cdd0b8f994740b7bfe2b41a12c319210ace` |
| `src/memory_service_app/dashboard_dist/assets/index-Cn4bmQmk.js` | `eea55ad98c92a20f2c4ff783c6a4b4915db3ea717ae98342a285ecfdf9d65257` |
| `src/memory_service_app/dashboard_dist/index.html` | `cf4a48448aadfeb673e1d6a386a8f9d579b63f6f6b9254193c444f1511ea7e9c` |

## 本轮修复（root 真实 browser m1b_comment 422）

- `MethodActions`：`reason` 现在依次取 statement/reason/content，均不足 5 字符时用 `“<label>：本人工作台提交”`；
  修复 `发表意见`（4 字符）触发 ActionRequest.reason min_length=5 的 422。
- replace/withdraw 的意见选项：`governance._my_opinions` 同时返回 `ref`（exact 三元组）与 `target_ref`；
  UI `chooseOption` 接受 `option.ref ?? option.target_ref`，替代意见的 `target_ref` 不再为空。
- 测试：TS 新增 reason 长度下限与 target_ref-only 替代选项；Python 新增短 reason 负例、UI 回退 reason 正例、
  `_my_opinions` 暴露 exact ref 单测；真实 `ActionRequest` 覆盖 15 个人类动作（非 stub）。
- 验证：pytest 90 passed；vitest 20 files / 162 passed；`tsc -b` exit 0；build_dashboard 71 inputs / 8 outputs，
  新 JS `index-Cn4bmQmk.js`。无需改 app facade；但 58805 上的 API 进程需重启以加载 governance 投影变更。
