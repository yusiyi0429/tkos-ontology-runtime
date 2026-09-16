"""Build-manifest regression: a stale source tree or tampered archive must fail."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_dashboard_assets.py"
spec = importlib.util.spec_from_file_location("verify_dashboard_assets", MODULE_PATH)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)


def test_real_tree_passes_manifest_verification():
    result = verifier.verify_source_tree()
    assert result["outputs"] > 0
    assert any(reference.endswith(".js") for reference in result["references"])


def _fixture_tree(monkeypatch, tmp_path: Path):
    root = tmp_path
    frontend = root / "workbench" / "dashboard"
    dist = root / "src" / "memory_service_app" / "dashboard_dist"
    (frontend / "src").mkdir(parents=True)
    (dist / "assets").mkdir(parents=True)
    (frontend / "package.json").write_text('{"name":"x"}\n')
    (frontend / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    (frontend / "src" / "App.tsx").write_text('export const App = () => null\n')
    (dist / "index.html").write_text('<div id="root"></div>'
                                     '<script src="/dashboard/assets/app.js"></script>'
                                     '<link rel="stylesheet" href="/dashboard/assets/app.css">\n')
    (dist / "assets" / "app.js").write_text("console.log(1)\n")
    (dist / "assets" / "app.css").write_text("body{}\n")
    monkeypatch.setattr(verifier, "ROOT", root)
    monkeypatch.setattr(verifier, "FRONTEND", frontend)
    monkeypatch.setattr(verifier, "DIST", dist)
    verifier.write_manifest(dist)
    return frontend, dist


def test_source_edit_without_rebuild_fails(monkeypatch, tmp_path):
    frontend, dist = _fixture_tree(monkeypatch, tmp_path)
    assert verifier.verify_source_tree()["outputs"] == 3
    (frontend / "src" / "App.tsx").write_text('export const App = () => 2\n')
    with pytest.raises(SystemExit) as exc:
        verifier.verify_source_tree()
    assert "DASHBOARD_ASSETS_STALE" in str(exc.value)
    # Rebuilding (writing a new manifest) makes it pass again.
    verifier.write_manifest(dist)
    assert verifier.verify_source_tree()["inputs"] > 0


def test_tampered_output_fails(monkeypatch, tmp_path):
    _frontend, dist = _fixture_tree(monkeypatch, tmp_path)
    (dist / "assets" / "app.js").write_text("console.log(2)\n")
    with pytest.raises(SystemExit) as exc:
        verifier.verify_source_tree()
    assert "DASHBOARD_ASSETS_STALE" in str(exc.value)


def _archive(tmp_path: Path, digest: str, content: bytes) -> Path:
    archive = tmp_path / "pkg.whl"
    manifest = {
        "schema_version": verifier.MANIFEST_SCHEMA,
        "outputs": [{"path": "assets/app.js", "sha256": digest, "bytes": len(content)}],
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("memory_service_app/dashboard_dist/asset-manifest.json",
                        json.dumps(manifest))
        handle.writestr("memory_service_app/dashboard_dist/assets/app.js", content)
    return archive


def test_archive_hash_verification(tmp_path):
    content = b"console.log(1)\n"
    digest = verifier.hashlib.sha256(content).hexdigest()
    archive = _archive(tmp_path, digest, content)
    assert verifier.verify_archive(archive, compare_source=False)["assets"] == 1
    tampered = _archive(tmp_path, digest, b"console.log(2)\n")
    with pytest.raises(SystemExit) as exc:
        verifier.verify_archive(tampered, compare_source=False)
    assert "DASHBOARD_ASSETS_STALE" in str(exc.value)


def test_archive_without_manifest_fails(tmp_path):
    archive = tmp_path / "empty.whl"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("memory_service_app/dashboard_dist/index.html", "<div/>")
    with pytest.raises(SystemExit) as exc:
        verifier.verify_archive(archive, compare_source=False)
    assert "DASHBOARD_MANIFEST_MISSING" in str(exc.value)
