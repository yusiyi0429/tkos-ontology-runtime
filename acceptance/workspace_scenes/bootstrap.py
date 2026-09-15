"""Upgrade only a fresh, explicitly generated local acceptance database."""
import argparse
from pathlib import Path
import re

from psycopg.conninfo import conninfo_to_dict

from acceptance.method_independent.database import method_grants
from acceptance.protocol_a1_independent.database import source_migrate
from acceptance.protocol_a1_independent.support import Environment, public_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    env = Environment(args.env_file)
    if not re.fullmatch(r"tkos_a1_method_[a-f0-9]{16}", conninfo_to_dict(env.values["APP_DATABASE_URL"])["dbname"]):
        raise ValueError("Expected an isolated Method acceptance database")
    source = Path(__file__).resolve().parents[2] / "src"
    first = source_migrate(env, source, args.output / "migrate.json")
    repeat = source_migrate(env, source, args.output / "repeat.json")
    assert first["applied"] == ["0021_method_foundation.sql", "0022_workspace_scenes.sql"]
    assert repeat["applied"] == []
    method_grants(env)
    public_json(args.output / "summary.json", {"applied": first["applied"], "repeat_applied": [], "roles_separate": True})
    print("Scene migration and repeat verified; application grants applied.")


if __name__ == "__main__":
    main()
