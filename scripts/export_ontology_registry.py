"""把本体登记 YAML 导出为确定性 JSON（sort_keys、ensure_ascii=False、indent=1）。

用法：PYTHONPATH=src .venv/bin/python scripts/export_ontology_registry.py \
        docs/contracts/ontology-registry-0.7.yaml docs/contracts/ontology-registry-0.7.json
导出的字节被 method-profile-0.5.json 的 ontology_registry_ref 与迁移 0029 钉定；
登记内容一变就要重新导出、重新 pin。
"""
import json
import sys
from pathlib import Path

import yaml


def main() -> None:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    target.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    print(target, "objects", len(data["objects"]), "relations", len(data["relations"]))


if __name__ == "__main__":
    main()
