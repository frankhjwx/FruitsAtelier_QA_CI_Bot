"""Install a pinned TLS certificate for CI transfers; run as root on the VPS."""
import grp
import hashlib
import os
from pathlib import Path
import ssl
import subprocess

root=Path('/etc/fruitsatelier-discord')
cert=root/'ci-transfer.crt'; key=root/'ci-transfer.key'
if not cert.exists() and not key.exists():
    subprocess.run(['openssl','req','-x509','-newkey','rsa:3072','-nodes','-days','3650','-keyout',str(key),'-out',str(cert),'-subj','/CN=Papaya private CI transfer'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
if not cert.exists() or not key.exists(): raise RuntimeError('Incomplete transfer TLS identity')
for path in (cert,key):
    os.chmod(path,0o640); os.chown(path,0,grp.getgrnam('fruitsatelier-qabot').gr_gid)
print(hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest())
