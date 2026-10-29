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


def token_link(kind):
    return 'https://github.com/settings/personal-access-tokens/new?' + urllib.parse.urlencode({
        'name': 'FruitsAtelier-' + kind, 'target_name': 'frankhjwx',
        'expires_in': '90', 'actions': 'write',
        'contents': 'read' if kind == 'server' else 'write',
        'description': 'Discord task dispatch' if kind == 'server' else 'Experimental editor branches and CI'})


def ssh_command(remote):
    return ['ssh', '-p', '29036', '-i', str(Path.home()/'.ssh/himiko_deploy'),
            '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=15', 'root@45.78.30.208', remote]


def run_command(args, data=None, timeout=60):
    result = subprocess.run(args, input=data, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # Provider errors may repeat credentials. Show the operation and status only.
        raise RuntimeError(f'{Path(args[0]).name} 操作失败（退出码 {result.returncode}），请检查登录、网络或权限。')
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
        raise RuntimeError(f'认证或权限检查失败：HTTP {error.code}。请检查当前这项密钥及权限。') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('网络连接失败或超时，请稍后重试。') from None


def configure(values, start, report):
    qa, ci, server, source = [values[k].strip() for k in ('qa', 'ci', 'server', 'source')]
    if any(len(v) < 20 or '\n' in v or '\r' in v for v in (qa, ci, server, source)):
        raise ValueError('请填写四项完整密钥，每项只填一行。')
    report('检查本机 GitHub 登录与 SSH 连接…')
    run_command(['gh', 'auth', 'status'])
    run_command(ssh_command('test -f /opt/fruitsatelier-discord/deploy/configure.py'))
    for label, key, repos in [('服务器 GitHub Token', server, [BOT_REPO, SOURCE_REPO]),
                             ('CI GitHub Token', source, [SOURCE_REPO])]:
        report('检查' + label + '…')
        for repo in repos:
            api('https://api.github.com/repos/' + repo + '/actions/workflows', key)
    for label, key, model in [('QA OpenAI Key', qa, 'gpt-6-luna'),
                              ('CI OpenAI Key', ci, 'gpt-6.1-sol')]:
        report('检查 ' + label + ' 的模型访问权限（一次极小 API 请求）…')
        api('https://api.openai.com/v1/responses', key, {
            'model': model, 'input': 'Reply OK.', 'max_output_tokens': 64,
            'reasoning': {'effort': 'low'}, 'store': False})
    report('保存 CI 密钥到私有 Bot 仓库的 GitHub Secrets…')
    run_command(['gh', 'secret', 'set', 'FA_OPENAI_CI_KEY', '--repo', BOT_REPO], ci)
    run_command(['gh', 'secret', 'set', 'FA_SOURCE_TOKEN', '--repo', BOT_REPO], source)
    report('保存 QA 密钥和服务器 Token 到服务器…')
    run_command(ssh_command('python3 -c ' + shlex.quote(REMOTE_INSTALL)),
                json.dumps({'openai-key': qa, 'github-token': server}))
    if start:
        report('启用并启动 Bot 服务…')
        run_command(ssh_command('systemctl enable fruitsatelier-discord && systemctl restart fruitsatelier-discord'))
    report('配置已保存。' + ('服务已收到启动命令，接下来检查上线状态。' if start else '服务保持当前状态。'))


def main():
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title('FruitsAtelier Bot · 一次性配置')
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
    ttk.Label(frame, text='一次配置 QA、CI 和服务器', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='密钥不会写入本机文件、聊天或日志。保存时会进行两次极小的 OpenAI API 权限检查。',
              wraplength=840).pack(anchor='w', pady=(6, 16))
    entries = {}
    fields = [
        ('qa', '1. OpenAI · fruitsatelier-qa', '填入你已经创建的 QA Key。', None),
        ('ci', '2. OpenAI · fruitsatelier-ci', '填入你已经创建的 CI Key。', None),
        ('server', '3. GitHub · 服务器 Token',
         'Repository access 选 Only select repositories：FruitsAtelier_QA_CI_Bot 和 FruitsAtelier。\n权限：Actions = Read and write；Contents = Read-only。', token_link('server')),
        ('source', '4. GitHub · CI Token',
         'Repository access 选 Only select repositories：只选 FruitsAtelier。\n权限：Actions 和 Contents 均为 Read and write。', token_link('ci'))]
    for key, label, description, url in fields:
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 4))
        ttk.Label(row, text=label, font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        if url:
            ttk.Button(row, text='打开预填创建页', command=lambda u=url: webbrowser.open(u)).pack(side='right')
        ttk.Label(frame, text=description, wraplength=840).pack(anchor='w')
        entry = ttk.Entry(frame, show='•', font=('Consolas', 11))
        entry.pack(fill='x', pady=(4, 12))
        entries[key] = entry
    invite = 'https://discord.com/oauth2/authorize?' + urllib.parse.urlencode({
        'client_id': '1554883096560537681', 'scope': 'bot applications.commands',
        'permissions': '274877910016', 'guild_id': '1479878482438918297', 'disable_guild_select': 'true'})
    ttk.Button(frame, text='将 Bot 邀请到 FruitsAtelier 正式服务器（目前仅在测试服）',
               command=lambda: webbrowser.open(invite)).pack(anchor='w', pady=(0, 8))
    start = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text='保存成功后启动 Bot', variable=start).pack(anchor='w')
    status = tk.StringVar(value='填写后点击“一次保存并配置”。无需在终端粘贴密钥。')
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
                events.put(('done', '已完成配置。可关闭窗口并回到聊天，继续验收问答和测试包。'))
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                message = str(error) if isinstance(error, (RuntimeError, ValueError)) else '本机命令失败或超时，请检查 gh、ssh 和网络连接。'
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

    button = ttk.Button(frame, text='一次保存并配置', command=submit)
    button.pack(anchor='e')
    root.after(100, poll)
    root.mainloop()


if __name__ == '__main__':
    main()
