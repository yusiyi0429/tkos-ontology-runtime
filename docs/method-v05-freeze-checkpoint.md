# Method 0.5 freeze checkpoint

- 时间（UTC）: 2026-09-23T00:10:00.273635+00:00
- HEAD: `15434b5749690ecdc6e5c59a8417590c550edef5`
- 文件清单：task-13-brief.md 给定的 15 个文件 + `git diff --name-only $(git merge-base main HEAD)..HEAD -- src/` 找到的、本分支改动过但不在该清单里的 src/ 文件（不含 `src/memory_service_app/dashboard_dist/assets/*`），共 27 个

| 文件 | SHA256 |
|---|---|
| `src/memory_service_runtime/governed/method_v05.py` | `1461ef8b30a211d42963e85e09e9548bbed1aa768b7c7d8222f97dee3a820070` |
| `src/memory_service_runtime/governed/method_v05_models.py` | `86e1a1b74c150174f5303f793f28edcb596dc67347d708ec2b28a27940b3786e` |
| `src/memory_service_runtime/governed/method_v05_profile.py` | `51330d4478c25d9a7eee5c85b8e897992bd9863acefb83d9b9f2f5f064139d56` |
| `src/memory_service_runtime/governed/method_v04.py` | `7ef4d13bd3b73beb061f4f2f8e10afea900f77c66cf1e4d2539ec940697613ed` |
| `src/memory_service_runtime/governed/method_service.py` | `f5f7a047096fa11a275039ad011fb6cc2a36cfd58c13756021a9603560f5d326` |
| `src/memory_service_runtime/governed/method_readers.py` | `9fc791efbfe09c691ed007e5020bc864794f9b4898d0950aba51ed1d6bcfc99c` |
| `src/memory_service_runtime/governed/governance.py` | `b44d31c2253bd207ee070c07569ff872624a1daa69e366f90d01d944b2627ac2` |
| `src/memory_service_runtime/governed/routes.py` | `d094ec2271461731a6119ec499c95d6d256e728a9f1b2b4186b6e5a62fed8d59` |
| `src/memory_service_app/migrations/0029_method_v05.sql` | `1865fcca203aa93acaad6cbb98cd1d9c7273e0282fc261c5ac2023de69f4174d` |
| `docs/contracts/tkos-method-0.5.md` | `d2ea113231533862fb2aed1611608b54c00fe66db0bbb99fe904ec6c69ed6d6f` |
| `docs/contracts/method-profile-0.5.json` | `ad04f43fa2217678e25d0cc99a2d960c18e184bf1763fca8da188884e55976f2` |
| `docs/contracts/ontology-registry-0.7.json` | `4f44c759d26db4e6812c60b11664add106697a1abf69935b9a9ca316e62220f8` |
| `docs/runtime-method-registry-0.5.json` | `f7efc90566326bf21f55a4a1bc533fc76dd640df2f86d25a8537cabde24ff78f` |
| `workbench/dashboard/src/lib/ontology.ts` | `fdcd323f136ad42cf1d92d5e353c2ef9446a7c510ac3a4c40d2e110de752a606` |
| `workbench/dashboard/src/MethodActions.tsx` | `84f82b9fd02255752ca9f7d58a0da4bffa33a3fdbf41dfe8eb99e4f47c7ea8cc` |
| `src/memory_service_app/dashboard_dist/asset-manifest.json` | `5988d614ac89ccd7563138de78d3e275f65209d7e6da62341884d47afdc834d0` |
| `src/memory_service_app/dashboard_dist/index.html` | `ed16074363fbfa9eed67330fb59c5e5aab9c81a7282c171ea696401487c53d22` |
| `src/memory_service_app/governance_commands.py` | `2cf2c907fe4d956e85744bc2d048375b68c477cd1c53f5fec404adc9c8d79b16` |
| `src/memory_service_runtime/governed/control.py` | `fd738fc2255d6b426beed1c95c95b0b64d420f9cf10da88eecc5532341b02b0f` |
| `src/memory_service_runtime/governed/dashboard.py` | `fe0af475e145d0adf9f35961dbb6898ce45d6b2514b9e618232589175fcd4542` |
| `src/memory_service_runtime/governed/method_access.py` | `cf25bf736810cd16384b07e0443fd3687addbbebb5758aa843a0abd3f57c3ba3` |
| `src/memory_service_runtime/governed/method_map.py` | `8dbe0fc8b66645a51cb204a1b2c0201ba46f8590925b8470f39d25c68fd6438b` |
| `src/memory_service_runtime/governed/method_models.py` | `b8cc6cb3f2b276da5fae819680be4a28eaea7d36dd728c5980b7ce3f4dd58565` |
| `src/memory_service_runtime/governed/models.py` | `2ebbf7100d466a66115b72214bd53522c73eabecfdc327e3080882540fd0d52d` |
| `src/memory_service_runtime/governed/profile.py` | `48faffd8186f32a976f0dd5c9bf826d62bb16bfbf579b09407623614890d7a4e` |
| `src/memory_service_runtime/governed/protocol.py` | `f8166586e270a8e878e3a533b8978088ce190f56c348a761699e74d1fdc301fa` |
| `src/memory_service_runtime/governed/workbench.py` | `f0457d3b4929aa3e17a835ecbbbf567531e60b90a5c85c45d010901de1dcee0c` |
