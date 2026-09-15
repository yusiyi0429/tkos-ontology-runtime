"""Create a new isolated database in the existing local PostgreSQL container."""
import argparse
from pathlib import Path
from acceptance.method_independent import database
from acceptance.protocol_a1_independent.database import source_migrate
from acceptance.protocol_a1_independent.support import Environment, public_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--private',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    # Same endpoint and role separation as the original acceptance helper;
    # the retained local container has a different deployment project name.
    database.CONTAINER = 'tkos-clark-linked-20260914-postgres-1'
    args.container = database.CONTAINER
    created = database.create(args)
    env = Environment(args.private/'env.json')
    source = Path(__file__).resolve().parents[2]/'src'
    first = source_migrate(env,source,args.output/'upgrade.json')
    repeated = source_migrate(env,source,args.output/'repeat.json')
    assert first['applied'] == ['0021_method_foundation.sql','0022_workspace_scenes.sql',
        '0023_method_lifecycle_v02.sql','0024_method_v02_binding_gate.sql']
    assert repeated['applied'] == []
    database.method_grants(env)
    public_json(args.output/'lifecycle-database.json',{'base':created,'upgrade':first,'repeat':repeated,
        'existing_databases_modified':False,'containers_modified':False})
    print('New isolated database migrated through 0024; replay applied no migrations.')


if __name__ == '__main__':
    main()
