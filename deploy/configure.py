"""Run interactively as root on the Bot server; secrets never appear in output."""
import getpass
import grp
import os
from pathlib import Path

root = Path('/etc/fruitsatelier-discord')
if os.geteuid() != 0:
    raise SystemExit('Run this helper as root on the Bot server.')
root.mkdir(mode=0o750, exist_ok=True)
gid = grp.getgrnam('fruitsatelier-qabot').gr_gid
os.chown(root, 0, gid)
for name, label in [('openai-key', 'OpenAI QA service account key'),
                    ('github-token', 'GitHub fine-grained token (Bot and editor repositories; Actions read/write, Contents read)')]:
    value = getpass.getpass(label + ' (empty keeps existing): ').strip()
    if not value:
        if not (root / name).exists():
            raise SystemExit('Required credential has not been configured: ' + name)
        continue
    if '\n' in value or '\r' in value or len(value) < 20:
        raise SystemExit('Invalid credential format')
    path = root / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, 'w') as output:
        output.write(value + '\n')
    os.chown(path, 0, gid)
    os.chmod(path, 0o640)
print('Credentials saved. Configure FA_OPENAI_CI_KEY in GitHub Actions Secrets separately.')
print('After the workflow is on main, enable the bot: systemctl enable --now fruitsatelier-discord')
