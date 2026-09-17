"""Fill a discovered login form without emitting the personal code to CLI output."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--login-file',type=Path,required=True);a=p.parse_args()
    login=json.loads(a.login_file.read_text())
    code='async (page) => { const login = '+json.dumps(login)+'; await page.getByLabel("用户名", {exact:true}).fill(login.username); await page.getByLabel("个人登录码", {exact:true}).fill(login.code); await page.getByRole("button", {name:"登录", exact:true}).click(); await page.getByRole("heading", {name:"需要我处理的事项"}).waitFor(); }'
    tool=Path.home()/'.codex/skills/playwright/scripts/playwright_cli.sh'
    r=subprocess.run(['bash',str(tool),'--session',a.session,'run-code',code],capture_output=True,text=True)
    if r.returncode or '### Error' in r.stdout:
        print('Browser login did not complete; inspect the page without exposing the code.')
        raise SystemExit(1)
    print('Browser login completed using the personal code; no credential emitted.')


if __name__=='__main__':main()
