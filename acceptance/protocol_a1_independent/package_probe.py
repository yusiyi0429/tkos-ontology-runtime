"""Inspect an already-built wheel, offline, without importing its code.

``inspect_wheel(wheel, expected_manifest=None, required_resources=None)`` accepts
an optional mapping of wheel member names to SHA256 strings (or ``{"sha256":
...}`` records). Required resources are member names, optionally prefixed with
``profile=`` or ``legacy=`` to name those artifacts explicitly. A default
Profile is discovered only from an unambiguous packaged ``profile-core.json``;
no source-tree file substitutes for a missing wheel member. The current legacy
interpretation artifact is embedded in migration 0018. Passing a JSON legacy
resource instead checks that resource independently.

``inspect_bundle(bundle, expected_manifest=None)`` reads a real .tar/.tar.gz/
.zip archive. The Profile may be inside its unique wheel or the bundle's unique
profile-core.json sidecar; both copies are checked when both are shipped. A
plain expected_manifest is a wheel-member manifest; alternatively pass explicit
``{"wheel_members": {...}, "bundle_members": {...}}`` SHA256 mappings.

This is packaging evidence only, never Runtime acceptance. No build, extraction,
network, database, installed-package import, or application code execution occurs.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = "memory_service_app/migrations/0018_method_protocol.sql"
DEFAULT_REQUIRED = (
    MIGRATION,
    "memory_service_runtime/governed/canon.py",
    "memory_service_runtime/governed/profile.py",
    "memory_service_runtime/governed/protocol.py",
)
P1_HASH = "5050d542cc521991f17932562c4397331c0482b673f9bcd8fe694749bd43fc76"
CONTRACT_HASH = "fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4"
LEGACY_HASH = "93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598"
MAX_MEMBER = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_BUNDLE_TOTAL = 4 * MAX_TOTAL
ABS_SEMANTICA = re.compile(
    rb"(?:[A-Za-z]:[\\/]|/)(?:[^\s\"'<>\x00]*[\\/])?semantica(?:[\\/]|(?=[\s\"'<>\x00])|$)",
    re.IGNORECASE,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(data: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value: str) -> Any:
        raise ValueError("non-finite JSON number")

    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                       parse_constant=invalid)
    _canonical(value)  # Reject overflowed floats and unpaired surrogates too.
    return value


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and "\\" not in name \
        and ":" not in name and all(part not in ("", ".", "..")
                                   for part in name.rstrip("/").split("/"))


def _profile_check(data: bytes, member: str, check) -> None:
    try:
        value = _json(data)
        actual = _sha(_canonical({k: v for k, v in value.items() if k != "canonical_hash"}))
        check("packaged_profile_frozen_p1", value.get("canonical_hash") == actual == P1_HASH
              and value.get("profile_core_schema_version") == "tkos.profile-core/0.1"
              and value.get("experimental") is True
              and value.get("corporate_approved") is False
              and value.get("record_origin") == "synthetic"
              and value.get("action_contract_ref") == {
                  "contract_id": "tkos.contract-a", "revision": "0.1",
                  "content_sha256": CONTRACT_HASH},
              member=member, canonical_sha256=actual, raw_sha256=_sha(data))
    except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
        check("packaged_profile_parse", False, member=member, error=type(exc).__name__)


def _inspect_wheel_bytes(
    raw: bytes,
    wheel_label: str,
    expected_manifest: Mapping[str, str | Mapping[str, Any]] | None = None,
    required_resources: Iterable[str] | None = None,
    *,
    bundle_profile: tuple[str, bytes] | None = None,
) -> dict[str, Any]:
    """Private byte entry; only inspect_bundle supplies actual archive sidecars."""
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "probe": "a1-wheel-offline-independent-v2", "wheel": wheel_label,
        "status": "not_ready", "package_verified": False,
        "runtime_accepted": False, "checks": checks, "source_manifest": {},
        "scope": "wheel bytes and packaged resources; no runtime execution",
    }

    def check(name: str, ok: bool, **detail: Any) -> None:
        checks.append({"check": name, "status": "passed" if ok else "failed", **detail})

    def finish() -> dict[str, Any]:
        statuses = {item["status"] for item in checks}
        report["status"] = "failed" if "failed" in statuses else (
            "not_ready" if "not_ready" in statuses or not checks else "passed")
        report["package_verified"] = report["status"] == "passed"
        return report

    report["wheel_sha256"] = _sha(raw)
    check("wheel_filename", PurePosixPath(wheel_label).suffix == ".whl")
    manifest: dict[str, str] = {}
    try:
        for name, item in (expected_manifest or {}).items():
            digest = item.get("sha256") if isinstance(item, Mapping) else item
            if not _safe_member(name) or not isinstance(digest, str) \
                    or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("manifest requires safe member names and lowercase SHA256")
            manifest[name] = digest
        required = set(DEFAULT_REQUIRED) | set(manifest)
        roles: dict[str, str] = {}
        for resource in required_resources or ():
            role, sep, name = resource.partition("=")
            if sep:
                if role not in ("profile", "legacy") or role in roles:
                    raise ValueError("only one profile= and one legacy= resource allowed")
                roles[role] = name
            else:
                name = resource
            if not _safe_member(name):
                raise ValueError("required resource is not a safe wheel member name")
            required.add(name)
    except (TypeError, ValueError, AttributeError) as exc:
        check("probe_configuration", False, error=str(exc))
        return finish()

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            check("zip_unique_members", len(names) == len({name.rstrip("/") for name in names}))
            check("zip_safe_members", all(_safe_member(name) for name in names))
            check("zip_no_symlinks", all(not stat.S_ISLNK(item.external_attr >> 16)
                                          for item in infos))
            check("zip_bounded_uncompressed_size", sum(i.file_size for i in infos) <= MAX_TOTAL
                  and all(i.file_size <= MAX_MEMBER for i in infos), members=len(infos))
            if any(item["status"] == "failed" for item in checks):
                return finish()
            bad_crc = archive.testzip()
            check("zip_crc", bad_crc is None, bad_member=bad_crc)
            content = {item.filename: archive.read(item) for item in infos if not item.is_dir()}
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError) as exc:
        check("zip_read", False, error=type(exc).__name__)
        return finish()

    hashes = {name: _sha(data) for name, data in sorted(content.items())}
    report["wheel_member_manifest_sha256"] = _sha(_canonical(hashes))
    report["wheel_member_count"] = len(hashes)
    violations = [name for name, data in content.items() if ABS_SEMANTICA.search(data)]
    check("no_absolute_semantica_references", not violations, members=violations)

    records = [name for name in content if name.endswith(".dist-info/RECORD")]
    check("one_wheel_record", len(records) == 1)
    if len(records) == 1:
        record_name = records[0]
        prefix = record_name.rsplit("/", 1)[0]
        try:
            rows = list(csv.reader(io.StringIO(content[record_name].decode("utf-8"))))
            record: dict[str, tuple[str, str]] = {}
            for row in rows:
                if len(row) != 3 or row[0] in record:
                    raise ValueError("malformed or duplicate RECORD row")
                record[row[0]] = (row[1], row[2])
            signatures = {prefix + "/RECORD.jws", prefix + "/RECORD.p7s"}
            expected_members = set(content) - signatures
            check("record_exact_member_coverage", set(record) == expected_members)
            mismatches = []
            for name in sorted(expected_members):
                if name not in record:
                    mismatches.append(name)
                    continue
                digest, size = record[name]
                if name == record_name:
                    if digest or size:
                        mismatches.append(name)
                    continue
                expected = "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(content[name]).digest()).decode("ascii").rstrip("=")
                if digest != expected or size != str(len(content[name])):
                    mismatches.append(name)
            check("record_sha256_and_size", not mismatches, members=mismatches)
            check("wheel_metadata_present", prefix + "/WHEEL" in content
                  and prefix + "/METADATA" in content)
        except (ValueError, UnicodeError, csv.Error) as exc:
            check("record_parse", False, error=str(exc))

    if "profile" not in roles:
        candidates = [name for name in content if PurePosixPath(name).name == "profile-core.json"]
        if len(candidates) == 1:
            roles["profile"] = candidates[0]
        elif not candidates and bundle_profile is not None:
            report["profile_artifact"] = {"location": "bundle_sidecar", "member": bundle_profile[0],
                                           "sha256": _sha(bundle_profile[1])}
        else:
            checks.append({"check": "profile_resource_location", "status": "not_ready",
                           "reason": "expected one packaged profile-core.json or explicit profile= member",
                           "candidates": candidates})
    roles.setdefault("legacy", MIGRATION)
    required.update(roles.values())
    report["required_resources"] = sorted(required)
    report["artifact_roles"] = roles
    for name in sorted(required):
        check("required_resource_present", name in content, member=name)
        if name not in content:
            continue
        source = ROOT / "src" / name
        if source.is_file():
            source_digest = _sha(source.read_bytes())
            report["source_manifest"][name] = source_digest
        else:
            source_digest = None
        expected = manifest.get(name)
        if expected is not None:
            check("frozen_resource_sha256", hashes[name] == expected,
                  member=name, expected=expected, actual=hashes[name])
        elif name in DEFAULT_REQUIRED:
            if source_digest is None:
                checks.append({"check": "source_comparison_available", "status": "not_ready",
                               "member": name, "reason": "supply expected_manifest for absent source"})
            else:
                check("source_wheel_byte_identity", hashes[name] == source_digest,
                      member=name, source_sha256=source_digest, wheel_sha256=hashes[name])
        elif source_digest is not None:
            check("resource_source_wheel_byte_identity", hashes[name] == source_digest,
                  member=name, source_sha256=source_digest, wheel_sha256=hashes[name])
    report["source_manifest_sha256"] = _sha(_canonical(report["source_manifest"]))

    profile_member = roles.get("profile")
    if profile_member in content:
        _profile_check(content[profile_member], profile_member, check)
        report["profile_artifact"] = {"location": "wheel_member", "member": profile_member,
                                       "sha256": hashes[profile_member]}
    if bundle_profile is not None:
        _profile_check(bundle_profile[1], "bundle:" + bundle_profile[0], check)
        report["bundle_profile_artifact"] = {"member": bundle_profile[0], "sha256": _sha(bundle_profile[1])}

    legacy_member = roles["legacy"]
    if legacy_member in content:
        try:
            if legacy_member.endswith(".sql"):
                sql = content[legacy_member].decode("utf-8")
                literals = re.findall(r"'((?:[^']|'')*)'\s*::\s*jsonb", sql, re.IGNORECASE)
                records = []
                for literal in literals:
                    value = _json(literal.replace("''", "'").encode("utf-8"))
                    if isinstance(value, dict) and value.get("profile_kind") == "tkos.legacy-interpretation-record":
                        records.append(value)
                check("embedded_legacy_resource_unique", len(records) == 1, member=legacy_member)
                legacy = records[0] if len(records) == 1 else None
                check("legacy_registration_statement_packaged",
                      bool(re.search(r"INSERT\s+INTO\s+gov_method_profile_revisions\b", sql, re.I))
                      and LEGACY_HASH in sql, member=legacy_member)
            else:
                legacy = _json(content[legacy_member])
            legacy_digest = _sha(_canonical(legacy)) if legacy is not None else None
            check("packaged_legacy_interpretation_frozen", legacy_digest == LEGACY_HASH,
                  member=legacy_member, canonical_sha256=legacy_digest,
                  raw_sha256=hashes[legacy_member])
        except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
            check("packaged_legacy_parse", False, error=type(exc).__name__)
    return finish()


def inspect_wheel(
    wheel: str | Path,
    expected_manifest: Mapping[str, str | Mapping[str, Any]] | None = None,
    required_resources: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Inspect actual wheel bytes; no separately supplied Profile bytes accepted.

    Explicit manifest members are required. Defaults compare this checkout's
    code/migration source bytes; frozen hashes establish Profile and legacy
    artifact identity. Use inspect_bundle for a truly packaged Profile sidecar.
    """
    artifact = Path(wheel).expanduser().resolve()
    if not artifact.is_file():
        return {"probe": "a1-wheel-offline-independent-v2", "wheel": str(artifact),
                "status": "not_ready", "package_verified": False, "runtime_accepted": False,
                "checks": [{"check": "wheel_available", "status": "not_ready"}]}
    return _inspect_wheel_bytes(artifact.read_bytes(), str(artifact), expected_manifest, required_resources)


def _bundle_key(name: str, *, directory: bool) -> str:
    # Common tar tools emit ./ as their root and ./wheel.whl as member names.
    # Normalize only harmless leading dots, then reject traversal/absolute paths.
    while name.startswith("./"):
        name = name[2:]
    if directory and name in {"", "."}:
        return ""
    if not _safe_member(name):
        raise ValueError("unsafe archive member path")
    return name.rstrip("/")


def inspect_bundle(bundle: str | Path, expected_manifest: Mapping | None = None) -> dict[str, Any]:
    """Inspect a real offline archive without extraction or wheel modification.

    Exactly one .whl is required. Up to one profile-core.json sidecar is allowed;
    absence is fine only when the wheel itself actually contains trusted P1.
    Member digests and the wheel's inner RECORD bind the exact archived bytes.
    Archives above the explicit 1 GiB uncompressed bound are not silently read.
    """
    artifact = Path(bundle).expanduser().resolve()
    checks = []
    report = {"probe": "a1-offline-bundle-independent-v1", "bundle": str(artifact),
              "status": "not_ready", "package_verified": False, "runtime_accepted": False,
              "checks": checks, "scope": "actual offline archive members; no extraction or Runtime execution"}

    def check(name, okay, **detail):
        checks.append({"check": name, "status": "passed" if okay else "failed", **detail})

    def finish():
        statuses = {row["status"] for row in checks}
        report["status"] = "failed" if "failed" in statuses else (
            "not_ready" if "not_ready" in statuses or not checks else "passed")
        report["package_verified"] = report["status"] == "passed"
        return report

    if not artifact.is_file():
        checks.append({"check": "bundle_available", "status": "not_ready"})
        return finish()
    wheel_manifest = expected_manifest
    bundle_manifest = {}
    if expected_manifest is not None and not isinstance(expected_manifest, Mapping):
        check("bundle_manifest_configuration", False)
        return finish()
    if expected_manifest is not None and {"wheel_members", "bundle_members"} & expected_manifest.keys():
        if set(expected_manifest) - {"wheel_members", "bundle_members"}:
            check("bundle_manifest_configuration", False)
            return finish()
        wheel_manifest = expected_manifest.get("wheel_members")
        bundle_manifest = expected_manifest.get("bundle_members", {})
        if not isinstance(bundle_manifest, Mapping):
            check("bundle_manifest_configuration", False)
            return finish()
    archive_sha = hashlib.sha256()
    with artifact.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            archive_sha.update(block)
    report["bundle_sha256"] = archive_sha.hexdigest()
    member_records = {}
    preserved = {}
    violations = []
    seen = set()

    def read_member(name, size, stream):
        wanted = name.endswith(".whl") or PurePosixPath(name).name == "profile-core.json"
        if wanted and size > MAX_MEMBER:
            raise ValueError("wheel/Profile archive member exceeds the explicit 32 MiB bound")
        member_sha, count, saved, overlap = hashlib.sha256(), 0, [], b""
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(block)
            if count > size:
                raise ValueError("archive member exceeds its declared size")
            member_sha.update(block)
            if wanted:
                saved.append(block)
            if ABS_SEMANTICA.search(overlap + block) and name not in violations:
                violations.append(name)
            overlap = block[-4096:]
        if count != size:
            raise ValueError("truncated archive member")
        member_records[name] = {"sha256": member_sha.hexdigest(), "bytes": count}
        if wanted:
            preserved[name] = b"".join(saved)

    try:
        if artifact.name.endswith(".zip"):
            with zipfile.ZipFile(artifact) as archive:
                infos = archive.infolist()
                if sum(info.file_size for info in infos) > MAX_BUNDLE_TOTAL:
                    raise ValueError("archive exceeds the explicit 1 GiB uncompressed bound")
                for info in infos:
                    key = _bundle_key(info.filename, directory=info.is_dir())
                    if key in seen:
                        raise ValueError("duplicate normalized archive member")
                    seen.add(key)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                        raise ValueError("archive links and special files are forbidden")
                bad = archive.testzip()
                if bad is not None:
                    raise ValueError("bundle ZIP CRC mismatch")
                check("bundle_zip_crc", True)
                for info in infos:
                    if not info.is_dir():
                        with archive.open(info) as stream:
                            read_member(info.filename, info.file_size, stream)
        elif artifact.name.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(artifact, mode="r:*") as archive:
                infos = archive.getmembers()
                if sum(info.size for info in infos) > MAX_BUNDLE_TOTAL:
                    raise ValueError("archive exceeds the explicit 1 GiB uncompressed bound")
                for info in infos:
                    key = _bundle_key(info.name, directory=info.isdir())
                    if key in seen:
                        raise ValueError("duplicate normalized archive member")
                    seen.add(key)
                    if not (info.isfile() or info.isdir()) or info.linkname:
                        raise ValueError("archive links and special files are forbidden")
                for info in infos:
                    if info.isfile():
                        with archive.extractfile(info) as stream:
                            read_member(info.name, info.size, stream)
                check("bundle_tar_members_read", True)
        else:
            raise ValueError("bundle format must be .tar, .tar.gz, .tgz or .zip")
        check("bundle_safe_paths_unique_no_links", True)
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError) as exc:
        check("bundle_archive_read", False, error=type(exc).__name__ + ": " + str(exc))
        return finish()
    report["bundle_members"] = member_records
    report["bundle_member_manifest_sha256"] = _sha(_canonical(member_records))
    check("bundle_no_absolute_semantica_references", not violations, members=violations)
    for name, expected in bundle_manifest.items():
        expected_hash = expected.get("sha256") if isinstance(expected, Mapping) else expected
        check("frozen_bundle_member_sha256", name in member_records and isinstance(expected_hash, str)
              and member_records[name]["sha256"] == expected_hash, member=name)
    wheels = [name for name in preserved if name.endswith(".whl")]
    profiles = [name for name in preserved if PurePosixPath(name).name == "profile-core.json"]
    check("bundle_exactly_one_wheel", len(wheels) == 1, candidates=wheels)
    check("bundle_at_most_one_profile_sidecar", len(profiles) <= 1, candidates=profiles)
    if len(wheels) != 1 or len(profiles) > 1:
        return finish()
    wheel_member = wheels[0]
    sidecar = (profiles[0], preserved[profiles[0]]) if profiles else None
    inner = _inspect_wheel_bytes(preserved[wheel_member], wheel_member,
                                 wheel_manifest, bundle_profile=sidecar)
    report["wheel_member"] = wheel_member
    report["wheel_member_sha256"] = member_records[wheel_member]["sha256"]
    report["wheel_report"] = inner
    report["source_manifest"] = inner.get("source_manifest", {})
    report["source_manifest_sha256"] = inner.get("source_manifest_sha256")
    check("wheel_bytes_match_bundle_member", inner["wheel_sha256"] == member_records[wheel_member]["sha256"])
    checks.append({"check": "archived_wheel_and_profile_verified", "status": inner["status"],
                   "wheel_member": wheel_member, "sidecar_member": profiles[0] if profiles else None})
    return finish()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    artifacts = parser.add_mutually_exclusive_group(required=True)
    artifacts.add_argument("--wheel", type=Path)
    artifacts.add_argument("--bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--required-resource", action="append", default=[],
                        help="wheel member, profile=member, or legacy=member; repeatable")
    parser.add_argument("--expected-manifest", type=Path,
                        help="JSON mapping of wheel member to SHA256 or {sha256: ...}")
    args = parser.parse_args()
    manifest = _json(args.expected_manifest.read_bytes()) if args.expected_manifest else None
    if args.bundle and args.required_resource:
        parser.error("--required-resource applies to --wheel; bundle resources must actually be unambiguous members")
    report = inspect_bundle(args.bundle, manifest) if args.bundle else inspect_wheel(args.wheel, manifest, args.required_resource)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "package_verified": report["package_verified"],
                      "runtime_accepted": False, "report": str(args.output.resolve())}))
    return 0 if report["package_verified"] else (2 if report["status"] == "not_ready" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
