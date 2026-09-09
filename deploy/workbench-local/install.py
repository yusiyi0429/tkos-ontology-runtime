"""Install public workbench assets into the existing loopback-only QA viewer.

No credentials are printed or copied into public assets. This is not a production
identity gateway. It reuses only the four already configured synthetic actors.
"""
from pathlib import Path
import os
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.runtime-acceptance/workbench-local-container'


def main():
    config = STATE / 'nginx.conf'
    old = config.read_text()
    match = re.search(r'map \$demo_role \$demo_authorization\s*\{([^}]+)\}', old)
    if not match:
        raise SystemExit('Existing synthetic identity map is required.')
    actor_map = match.group(1)
    roles = re.findall(r'^\s*(\w+)\s+"Bearer [^"\s;]+";\s*$', actor_map, re.M)
    if sorted(roles) != sorted(['ceo', 'mission_dri', 'verifier', 'outsider']):
        raise SystemExit('Expected four synthetic identity mappings.')
    # Reject extra map directives, then preserve credential bytes unchanged.
    remainder = re.sub(r'^\s*\w+\s+"Bearer [^"\s;]+";\s*$', '', actor_map, flags=re.M)
    if re.sub(r'\s+', '', remainder) != 'default"";':
        raise SystemExit('Unexpected identity map structure.')
    source = ROOT / 'workbench'
    if not (source / 'index.html').is_file():
        raise SystemExit('Build the workbench before installation.')
    assets = list(source.rglob('*'))
    if any(p.is_symlink() for p in assets):
        raise SystemExit('Workbench assets must not contain symlinks.')
    public = STATE / 'public/workbench'
    public.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in assets:
        if path.is_file() and path.suffix in {'.html', '.css', '.js', '.mjs', '.json'}:
            target = public / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            os.chmod(target, 0o644)
            copied.append(str(path.relative_to(source)))
    manifest = STATE / 'workbench-installed-files.json'
    previous = json.loads(manifest.read_text()) if manifest.exists() else []
    for relative in set(previous) - set(copied):
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts:
            raise SystemExit('Invalid installed asset manifest.')
        target = public / rel
        if target.is_file() and not target.is_symlink():
            target.unlink()
    manifest.write_text(json.dumps(copied, indent=2) + '\n')
    os.chmod(manifest, 0o600)
    backup = STATE / 'nginx.before-workbench.conf'
    if not backup.exists():
        backup.write_text(old)
        os.chmod(backup, 0o600)
    template = (Path(__file__).parent / 'nginx.conf.template').read_text()
    config.write_text(template.replace('__LOCAL_AUTH_MAP__', actor_map.strip()))
    os.chmod(config, 0o600)
    print(f'Installed {len(copied)} public files; private configuration retained locally.')
    print('Validate and reload only tkos-workbench-local-api-viewer-1 to activate.')


if __name__ == '__main__':
    main()
