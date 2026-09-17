#!/usr/bin/env python3
"""Generate the curated Method→Runtime map snapshot module.

Read-only derivation from the frozen working documents:

- ``docs/reviews/2026-09-17-business-data/inventory-runtime-map.csv``
- ``docs/reviews/2026-09-17-business-data/method-source-manifest.json``
- concise business definitions from the normalized working-document snapshots
  in ``--definitions-dir`` (default ``/tmp/tkos-method-current``), files
  ``anchors-normalized.json`` / ``references-normalized.json`` /
  ``artifacts-normalized.json`` / ``records-normalized.json``.  Only the short
  definition/purpose fields are embedded, never the full source documents.

The generated module keeps the 44 inventory entries, their published source
locators and the calibration columns verbatim; the small curated tables below
add the runtime object-type correspondence, the confirmed documented-contract
scope and the concise business definition/purpose.  Nothing here is a server
rule: the runtime reads its compiled registries separately.

Run from the repository root:

    python scripts/generate_method_map_snapshot.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pprint
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWS = ROOT / "docs" / "reviews" / "2026-09-17-business-data"
OUTPUT = ROOT / "src" / "memory_service_runtime" / "governed" / "method_map_snapshot.py"
DEFAULT_DEFINITIONS_DIR = Path("/tmp/tkos-method-current")

CATEGORY = {
    "Anchor": "anchor",
    "Reference": "reference",
    "Business Artifact": "business_artifact",
    "Evidence / Runtime Record": "evidence_runtime_record",
}

MATURITY = {
    "Defined": "defined",
    "Partial": "partial",
    "To Define": "to_define",
    "Open Classification": "open_classification",
    "Defined for M1-A": "defined_for_m1a",
}

# category -> (normalized file, definition field)
DEFINITION_SOURCES = {
    "anchor": ("anchors-normalized.json", "Formation Logic"),
    "reference": ("references-normalized.json", "Content / Minimum Semantics"),
    "business_artifact": ("artifacts-normalized.json", "Formation Logic"),
    "evidence_runtime_record": ("records-normalized.json", "Minimum Business Semantics"),
}
DEFINITION_LIMIT = 480
PURPOSE_LIMIT = 320

# Curated runtime object-type correspondence.  Names are the registered Method
# object types; entries with no runtime object type are carried by projections,
# readers or control records instead.
RUNTIME_TYPES = {
    "I01": ["StrategicIssue", "PotentialIssue"],
    "I02": ["Strategy", "StrategyUpdateProposal"],
    "I03": ["StrategicArchitecture"],
    "I04": ["LTCO"],
    "I05": ["PCO"],
    "I06": ["Mission"],
    "I07": [],
    "I08": [],
    "I09": ["OperatingState"],
    "I10": ["OperatingProblem"],
    "I11": [],
    "I12": [],
    "I13": ["EvidenceAsset"],
    "I14": [],
    "I15": [],
    "I16": [],
    "I17": [],
    "I18": [],
    "I19": ["PeriodReview"],
    "I20": [],
    "I21": [],
    "I22": [],
    "I23": ["ResearchReport", "EvidenceAsset"],
    "I24": ["ResearchBrief", "ResearchMemo", "ResearchPlan"],
    "I25": ["EvidenceAsset"],
    "I26": ["CandidateSet"],
    "I27": ["StrategicJudgment"],
    "I28": ["StrategicAgreement"],
    "I29": ["ReviewRecord"],
    "I30": ["BusinessFact", "EvidenceAsset"],
    "I31": ["BusinessFact"],
    "I32": ["BusinessFact", "EvidenceAsset"],
    "I33": ["Signal"],
    "I34": ["EvidenceAsset"],
    "I35": ["BusinessFact", "EvidenceAsset"],
    "I36": ["OperatingState", "OperatingProblem"],
    "I37": [],
    "I38": ["MethodRun"],
    "I39": [],
    "I40": [],
    "I41": ["MeetingMinutes", "MeetingRound"],
    "I42": ["ReviewRecord"],
    "I43": [],
    "I44": ["ReviewWindow"],
}

# Concepts whose rules the user-confirmed 0.4 / workspace 0.2 increments
# explicitly revisit.  Presence here means "documented scope", never support.
PLANNED_CONTRACTS = {
    "I01": ["tkos.method/0.4"],
    "I02": ["tkos.method/0.4"],
    "I03": ["tkos.method/0.4"],
    "I04": ["tkos.method/0.4"],
    "I05": ["tkos.method/0.4"],
    "I06": ["tkos.method/0.4"],
    "I09": ["tkos.method/0.4"],
    "I10": ["tkos.method/0.4"],
    "I19": ["tkos.method/0.4"],
    "I28": ["tkos.method/0.4"],
    "I41": ["tkos.workspace/0.2"],
}

# Source rows that carry a runtime calibration conclusion.
SUPPORT_ASSESSMENT = {
    "需契约调整": "requires_contract_change",
    "待业务收口": "pending_business_close",
    "未覆盖": "not_implemented",
    "可复用": "reusable_partial",
}

# Published document names from source-index.md; ids alone are not readable.
DOC_NAMES = {
    "B02": "L1–L5 设计标准 v1.3",
    "B03": "L1–L5 模板 2026-09-16",
    "B04": "M1A 战略管理 v2.3",
    "B05": "M1B 公司经营管理 L3 v4",
    "B06": "Anchor 目标模板质量标准 v0.6",
    "B07": "Artifact 分类及规范逻辑 v0.3",
    "B08": "方法概览 1-pager v1.0",
    "B09": "M2 业务域经营管理 v2.0",
    "B10": "Method Domain 9月经营承诺（2026-09-10）",
}


def assess(text: str) -> str:
    for token, value in SUPPORT_ASSESSMENT.items():
        if token in text:
            return value
    return "unknown"


def record_ref(url: str) -> str:
    marker = "record="
    return url.split(marker, 1)[1] if marker in url else ""


def table_ref(url: str) -> str:
    marker = "table="
    if marker not in url:
        return ""
    return url.split(marker, 1)[1].split("&", 1)[0]


def clip(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load_definitions(directory: Path):
    loaded: dict[str, list[dict]] = {}
    for category, (filename, _field) in DEFINITION_SOURCES.items():
        path = directory / filename
        if not path.is_file():
            raise SystemExit(f"missing normalized definition source: {path}")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise SystemExit(f"unexpected definition source shape: {path}")
        loaded[category] = rows
    return loaded


def load_sources():
    manifest = json.loads((REVIEWS / "method-source-manifest.json").read_text(encoding="utf-8"))
    tables = {row["table_id"]: row for row in manifest["inventory"]}
    docs = {row["source_id"]: row for row in manifest["documents"]}
    return manifest, tables, docs


def build_entries(definitions: dict[str, list[dict]], tables: dict) -> list[dict]:
    with (REVIEWS / "inventory-runtime-map.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 44, f"expected 44 inventory rows, found {len(rows)}"
    positions: dict[str, int] = {}
    entries = []
    for row in rows:
        entry_id = row["索引"].strip()
        category = CATEGORY[row["Method分类"].strip()]
        source_rows = definitions[category]
        index = positions.get(category, 0)
        if index >= len(source_rows):
            raise SystemExit(f"definition source has no row for {entry_id} ({category})")
        positions[category] = index + 1
        definition_row = source_rows[index]
        url = row["来源定位"].strip()
        table_id = table_ref(url)
        table = tables.get(table_id, {})
        filename, field = DEFINITION_SOURCES[category]
        entries.append({
            "id": entry_id,
            "business_category": category,
            "ssot_name": row["SSOT对象"].strip(),
            "business_name": clip(definition_row.get("中文名称"), 80),
            "business_maturity": MATURITY[row["Method标准状态"].strip()],
            "purpose": clip(definition_row.get("Purpose"), PURPOSE_LIMIT),
            "definition": clip(definition_row.get(field), DEFINITION_LIMIT),
            "definition_source": {"document": filename, "index": index},
            "source_ref": {
                "source_id": table.get("source_id", "B01"),
                "table_id": table_id,
                "table_name": table.get("name"),
                "record_ref": record_ref(url),
                "source_locator": url,
            },
            "runtime_correspondence": row["Runtime对应与依据"].strip(),
            "runtime_object_types": RUNTIME_TYPES[entry_id],
            "runtime_support_assessment": assess(row["校准结论"]),
            "calibration_conclusion": row["校准结论"].strip(),
            "gap": row["差异或边界"].strip(),
            "next_step": row["建议下一步"].strip(),
            "instance_verification": row["实际业务实例核验"].strip(),
            "review_status": row["评审结论"].strip(),
            "planned_contracts": PLANNED_CONTRACTS.get(entry_id, []),
        })
    for category, source_rows in definitions.items():
        if positions.get(category, 0) != len(source_rows):
            raise SystemExit(f"definition source row count mismatch for {category}")
    return entries


def build_sources(manifest: dict, docs: dict) -> list[dict]:
    sources = []
    for source_id in sorted(docs):
        row = docs[source_id]
        sources.append({
            "source_id": source_id,
            "kind": "document",
            "name": DOC_NAMES.get(source_id, source_id),
            "revision_id": row.get("revision_id"),
            "content_sha256": row.get("content_sha256"),
            "read_scope": row.get("read_scope"),
        })
    b01 = manifest["inventory"][0]
    sources.insert(0, {
        "source_id": b01["source_id"],
        "kind": "inventory",
        "name": "Artifact Master Inventory v0.4（2026-09-16）",
        "revision_id": None,
        "content_sha256": None,
        "read_scope": "6张表全部分页读取；业务条目 10 + 8 + 11 + 15 = 44",
        "tables": [
            {"table_id": row["table_id"], "name": row["name"], "rows_read": row["rows_read"],
             "business_entries": row["business_entries"], "ndjson_sha256": row["ndjson_sha256"]}
            for row in manifest["inventory"]
        ],
    })
    return sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--definitions-dir", type=Path, default=DEFAULT_DEFINITIONS_DIR)
    args = parser.parse_args()
    manifest, tables, docs = load_sources()
    definitions = load_definitions(args.definitions_dir)
    module = {
        "checked_date": manifest["checked_date"],
        "scope": manifest["scope"],
        "source_note": (
            "工作文档快照的只读映射；不复制完整来源文档，不自动更新服务器规则。"
            "映射条目按当时可见版本记录，来源变化需人工重新核对。"
        ),
        "sources": build_sources(manifest, docs),
        "entries": build_entries(definitions, tables),
    }
    canonical = json.dumps(module, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Readable generated data: a plain Python literal (not one escaped JSON
    # string) keeps the committed snapshot diffable and inspectable.
    literal = pprint.pformat(module, width=110, sort_dicts=False)
    OUTPUT.write_text(
        '"""Generated by scripts/generate_method_map_snapshot.py — do not edit by hand.\n\n'
        f"content_sha256: {digest}\n"
        '"""\nfrom __future__ import annotations\n\n'
        "CONTENT_SHA256 = " + json.dumps(digest) + "\n\n"
        "SNAPSHOT = " + literal + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT} sha256={digest}")


if __name__ == "__main__":
    main()
