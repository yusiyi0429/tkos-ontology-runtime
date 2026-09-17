"""Export only schemas reachable from the local governance-workbench routes."""
import json
import os
from pathlib import Path

os.environ['TKOS_DASHBOARD_ENABLED'] = '1'
os.environ.setdefault('MEMORY_TENANT', 'openapi-export')
os.environ.setdefault('MEMORY_ORG', 'openapi-export')
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from memory_service_app.main import app

spec = app.openapi()
paths = {p: v for p, v in spec['paths'].items() if p.startswith(('/v1/governance', '/dashboard/api/v1/commands', '/dashboard/api/v1/governance')) or p == '/dashboard/api/v1/session'}
schemas = {}

def collect(value):
    if isinstance(value, list):
        for item in value:
            collect(item)
    elif isinstance(value, dict):
        ref = value.get('$ref', '')
        if ref.startswith('#/components/schemas/'):
            name = ref.rsplit('/', 1)[-1]
            if name not in schemas:
                schemas[name] = spec['components']['schemas'][name]
                collect(schemas[name])
        for item in value.values():
            collect(item)

collect(paths)
Path('docs/runtime-governance-openapi.json').write_text(json.dumps({
    'openapi': spec['openapi'], 'info': {'title': 'Runtime local M1B governance workbench', 'version': '0.1'},
    'paths': paths, 'components': {'schemas': schemas}}, ensure_ascii=False, indent=2) + '\n')
