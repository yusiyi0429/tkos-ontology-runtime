"""Export the installed API contract without opening a database or loading secrets."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "src"))
    from memory_service_app.main import app
    value = app.openapi()
    target = root / "docs/runtime-workspace-openapi.json"
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not target.exists() or target.read_text() != text:
            raise SystemExit("Workspace API snapshot differs from the source")
    else:
        target.write_text(text)
    print("Workspace OpenAPI snapshot verified" if args.check else "Workspace OpenAPI snapshot exported")


if __name__ == "__main__":
    main()
