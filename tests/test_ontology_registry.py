"""本体登记（docs/contracts/ontology-registry-0.7.yaml）与其冻结 JSON、运行时模型的一致性。"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Annotated, Union, get_args, get_origin

import yaml
from pydantic import BaseModel

from memory_service_runtime.governed import method_v04_models as v4
from memory_service_runtime.governed.method_m1a_models import ExactRef

ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = ROOT / "docs/contracts/ontology-registry-0.7.yaml"
JSON_PATH = ROOT / "docs/contracts/ontology-registry-0.7.json"
STATUSES = {"exists", "partial", "missing", "product_mechanism"}


def registry():
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def _unwrap(annotation):
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def model_paths(model, prefix="", depth=0):
    """模型上所有字段路径；列表加 []，嵌套模型下钻两层，到 ExactRef 为止。"""
    out = set()
    for name, field in model.model_fields.items():
        annotation = _unwrap(field.annotation)
        candidates = [annotation]
        if get_origin(annotation) in (Union, types.UnionType):
            candidates = [_unwrap(a) for a in get_args(annotation) if a is not type(None)]
        for candidate in candidates:
            path = prefix + name
            if get_origin(candidate) is list:
                candidate = _unwrap(get_args(candidate)[0])
                path += "[]"
            out.add(path)
            if (isinstance(candidate, type) and issubclass(candidate, BaseModel)
                    and not issubclass(candidate, ExactRef) and depth < 2):
                out |= model_paths(candidate, path + ".", depth + 1)
    return out


def test_frozen_json_is_the_canonical_export_of_the_yaml():
    expected = json.dumps(registry(), ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    assert JSON_PATH.read_text(encoding="utf-8") == expected


def test_ids_are_unique_relations_name_registered_objects_and_statuses_are_known():
    data = registry()
    assert data["revision"] == "0.7.1"
    ids = [o["id"] for o in data["objects"]]
    assert len(ids) == len(set(ids)) == 20
    rids = [r["id"] for r in data["relations"]]
    assert len(rids) == len(set(rids)) == 33
    for relation in data["relations"]:
        assert relation["from"] in ids and relation["to"] in ids, relation["id"]
    for item in [*data["objects"], *data["relations"], *data["gates"]]:
        for key in ("runtime_0_4", "runtime_0_5"):
            assert item[key]["status"] in STATUSES, (item["id"], key)


# Registered object types that deliberately have no entry in a Method PAYLOAD_MODELS
# dict: CompanyReference is Contract-A's payload (a2_models.py, a different protocol),
# and EvidenceAsset stores raw-byte hashes with no payload schema (evidence.py).
NOT_METHOD_PAYLOAD_MODELS = {"CompanyReference", "EvidenceAsset"}


def test_runtime_0_4_fields_exist_on_the_0_4_models():
    paths = {kind: model_paths(model) for kind, model in v4.PAYLOAD_MODELS.items()}
    for item in registry()["objects"]:
        runtime = item["runtime_0_4"]
        kind = runtime.get("object_type")
        if kind is None or kind in NOT_METHOD_PAYLOAD_MODELS:
            continue
        assert kind in paths, (item["id"], kind)
        for field in runtime.get("fields", []):
            assert field in paths[kind], (item["id"], field)


def test_runtime_0_5_fields_exist_on_the_0_5_models_and_tallies_match_the_plan():
    from memory_service_runtime.governed import method_v05_models as v5
    data = registry()
    paths = {kind: model_paths(model) for kind, model in v5.PAYLOAD_MODELS.items()}
    for item in data["objects"]:
        runtime = item["runtime_0_5"]
        kind = runtime.get("object_type")
        if kind is None or kind in NOT_METHOD_PAYLOAD_MODELS:
            continue
        assert kind in paths, (item["id"], kind)
        for field in runtime.get("fields", []):
            assert field in paths[kind], (item["id"], field)
    for kind in ("Constraint", "LTCO", "PCO", "Mission", "OperatingState", "PeriodReview"):
        registered = next(o for o in data["objects"] if o["runtime_0_5"].get("object_type") == kind)
        assert registered["runtime_0_5"]["status"] in {"exists", "partial"}
    tally = lambda items, key: {s: sum(1 for i in items if i[key]["status"] == s) for s in ("exists", "partial", "missing")}
    assert tally(data["objects"], "runtime_0_5") == {"exists": 9, "partial": 7, "missing": 4}
    assert tally(data["relations"], "runtime_0_5") == {"exists": 27, "partial": 4, "missing": 2}
    assert all(g["runtime_0_5"]["status"] == "exists" for g in data["gates"])
