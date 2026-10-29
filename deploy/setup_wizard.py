"""Interactive local setup. Credentials travel only through stdin or TLS requests."""
import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

BOT_REPO = 'frankhjwx/FruitsAtelier_QA_CI_Bot'
SOURCE_REPO = 'frankhjwx/FruitsAtelier'
REMOTE_INSTALL = '''
import json, os, grp, tempfile
from pathlib import Path
data=json.load(__import__('sys').stdin)
root=Path('/etc/fruitsatelier-discord')
gid=grp.getgrnam('fruitsatelier-qabot').gr_gid
root.mkdir(mode=0o750,exist_ok=True)
os.chown(root,0,gid)
for name in ('openai-key','github-token'):
    value=data[name]
    if not isinstance(value,str) or len(value)<20 or '\\n' in value or '\\r' in value:
        raise SystemExit('Invalid credential format')
for name in ('openai-key','github-token'):
    fd,path=tempfile.mkstemp(prefix='.credential-',dir=root)
    try:
        os.fchmod(fd,0o640)
        os.fchown(fd,0,gid)
        with os.fdopen(fd,'w') as out:
            out.write(data[name]+'\\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(path,root/name)
    finally:
        if os.path.exists(path): os.unlink(path)
print('Credentials installed')
'''


REMOTE_CHECK = """
import json, sys, urllib.request, urllib.error
key=json.load(sys.stdin)['key']
request=urllib.request.Request('https://api.openai.com/v1/responses',
    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
    data=json.dumps({'model':'gpt-6-luna','input':'Reply OK.','max_output_tokens':64,
                     'reasoning':{'effort':'low'},'store':False}).encode())
try:
    with urllib.request.urlopen(request,timeout=35) as response:
        print(json.dumps({'ok':response.status==200}))
except urllib.error.HTTPError as error:
    print(json.dumps({'ok':False,'status':error.code}))
except (urllib.error.URLError,TimeoutError):
    print(json.dumps({'ok':False,'status':'connection_failed'}))
"""


def token_link(kind):
    return 'https://github.com/settings/personal-access-tokens/new?' + urllib.parse.urlencode({
        'name': 'FruitsAtelier-' + kind, 'target_name': 'frankhjwx',
        'expires_in': '90', 'actions': 'write',
        'contents': 'read' if kind == 'server' else 'write',
        'description': 'Discord task dispatch' if kind == 'server' else 'Experimental editor branches and CI'})


def ssh_command(remote):
    return ['ssh', '-p', '22', '-i', str(Path.home()/'.ssh/himiko_deploy'),
            '-o', 'KexAlgorithms=curve25519-sha256',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=15', 'root@198.44.103.184', remote]


def run_command(args, data=None, timeout=60):
    result = subprocess.run(args, input=data, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # Provider errors may repeat credentials. Show the operation and status only.
        raise RuntimeError(f'{Path(args[0]).name} failed (exit code {result.returncode}). Check authentication, network and permissions.')
    return result.stdout


def api(url, key, data=None):
    request = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + key, 'User-Agent': 'FruitsAtelier setup',
        'Content-Type': 'application/json'},
        data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Authentication or permission check failed: HTTP {error.code}. Check this credential and its permissions.') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Connection failed or timed out. Please try again later.') from None


def configure(values, start, report):
    qa, ci, server, source = [values[k].strip() for k in ('qa', 'ci', 'server', 'source')]
    if any(len(v) < 20 or '\n' in v or '\r' in v for v in (qa, ci, server, source)):
        raise ValueError('Enter all four complete credentials, one line each.')
    report('Checking local GitHub authentication and SSH connectivity...')
    run_command(['gh', 'auth', 'status'])
    run_command(ssh_command('test -f /opt/fruitsatelier-discord/deploy/configure.py'))
    for label, key, repos in [('Server GitHub Token', server, [BOT_REPO, SOURCE_REPO]),
                             ('CI GitHub Token', source, [SOURCE_REPO])]:
        report('Checking ' + label + '...')
        for repo in repos:
            api('https://api.github.com/repos/' + repo + '/actions/workflows', key)
    for label, key, model in [('QA OpenAI Key', qa, 'gpt-6-luna'),
                              ('CI OpenAI Key', ci, 'gpt-6.1-sol')]:
        report('Checking model access for ' + label + ' (one small API request)...')
        api('https://api.openai.com/v1/responses', key, {
            'model': model, 'input': 'Reply OK.', 'max_output_tokens': 64,
            'reasoning': {'effort': 'low'}, 'store': False})
    report('Checking OpenAI access from the deployment server...')
    remote = json.loads(run_command(ssh_command('python3 -c ' + shlex.quote(REMOTE_CHECK)), json.dumps({'key': qa})))
    if remote.get('ok') is not True:
        raise RuntimeError('Deployment server cannot access OpenAI. Check server region, network and API permissions before starting the Bot.')
    report('Saving CI credentials to GitHub Secrets in the private Bot repository...')
    run_command(['gh', 'secret', 'set', 'FA_OPENAI_CI_KEY', '--repo', BOT_REPO], ci)
    run_command(['gh', 'secret', 'set', 'FA_SOURCE_TOKEN', '--repo', BOT_REPO], source)
    report('Saving the QA key and server token on the server...')
    run_command(ssh_command('python3 -c ' + shlex.quote(REMOTE_INSTALL)),
                json.dumps({'openai-key': qa, 'github-token': server}))
    if start:
        report('Enabling and starting the Bot service...')
        run_command(ssh_command('systemctl enable fruitsatelier-discord && systemctl restart fruitsatelier-discord'))
    report('Configuration saved. ' + ('Service start requested; verify online status next.' if start else 'Service state unchanged.'))


def main():
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title('FruitsAtelier Bot - One-time setup')
    root.geometry('900x720')
    root.minsize(800, 680)
    canvas = tk.Canvas(root, highlightthickness=0)
    scrollbar = ttk.Scrollbar(root, orient='vertical', command=canvas.yview)
    scrollbar.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    frame = ttk.Frame(canvas, padding=22)
    window = canvas.create_window((0, 0), window=frame, anchor='nw')
    frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
    ttk.Label(frame, text='Set up QA, CI and the server', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Credentials are not saved to local files, chat or logs. Saving makes two small OpenAI API requests to check access.',
              wraplength=840).pack(anchor='w', pady=(6, 16))
    entries = {}
    fields = [
        ('qa', '1. OpenAI - fruitsatelier-qa', 'Enter the QA key you created.', None),
        ('ci', '2. OpenAI - fruitsatelier-ci', 'Enter the CI key you created.', None),
        ('server', '3. GitHub - Server Token',
         'Only select repositories: FruitsAtelier_QA_CI_Bot and FruitsAtelier.\nPermissions: Actions = Read and write; Contents = Read-only.', token_link('server')),
        ('source', '4. GitHub · CI Token',
         'Only select repositories: FruitsAtelier.\nPermissions: Actions and Contents = Read and write.', token_link('ci'))]
    for key, label, description, url in fields:
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 4))
        ttk.Label(row, text=label, font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        if url:
            ttk.Button(row, text='Open token creation page', command=lambda u=url: webbrowser.open(u)).pack(side='right')
        ttk.Label(frame, text=description, wraplength=840).pack(anchor='w')
        entry = ttk.Entry(frame, show='•', font=('Consolas', 11))
        entry.pack(fill='x', pady=(4, 12))
        entries[key] = entry
    invite = 'https://discord.com/oauth2/authorize?' + urllib.parse.urlencode({
        'client_id': '1554883096560537681', 'scope': 'bot applications.commands',
        'permissions': '274877910016', 'guild_id': '1479878482438918297', 'disable_guild_select': 'true'})
    ttk.Button(frame, text='Invite the Bot to the FruitsAtelier production server',
               command=lambda: webbrowser.open(invite)).pack(anchor='w', pady=(0, 8))
    start = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text='Start the Bot after saving', variable=start).pack(anchor='w')
    status = tk.StringVar(value='Complete the fields, then click Save and configure. No terminal input is needed.')
    ttk.Label(frame, textvariable=status, wraplength=840).pack(anchor='w', pady=12)
    events = queue.Queue()

    def submit():
        values = {k: entry.get() for k, entry in entries.items()}
        should_start = start.get()
        button.configure(state='disabled')

        def work():
            stage = ''

            def report(text):
                nonlocal stage
                stage = text
                events.put(('status', text))
            try:
                configure(values, should_start, report)
                events.put(('done', 'Setup complete. Close this window and return to chat to verify answers and test packages.'))
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                message = str(error) if isinstance(error, (RuntimeError, ValueError)) else 'A local command failed or timed out. Check gh, ssh and network connectivity.'
                events.put(('error', stage + '\n' + message))
            finally:
                values.clear()
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            while True:
                kind, text = events.get_nowait()
                status.set(text)
                if kind in ('done', 'error'):
                    button.configure(state='normal')
                if kind == 'done':
                    for entry in entries.values():
                        entry.delete(0, 'end')
        except queue.Empty:
            pass
        root.after(100, poll)

    button = ttk.Button(frame, text='Save and configure', command=submit)
    button.pack(anchor='e')
    root.after(100, poll)
    root.mainloop()


if __name__ == '__main__':
    main()
