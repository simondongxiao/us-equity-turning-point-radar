"""Authenticated encrypted state in immutable same-repository release assets.

Key stays in RADAR_STATE_KEY Actions secret and a local ignored recovery file.
Never emit the key or upload plaintext models, datasets or full ledgers.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
REPO='simondongxiao/us-equity-turning-point-radar'
TAG='radar-encrypted-state'

def gh(*args, input=None):
    return subprocess.check_output(['gh',*args],input=input)

def key():
    value=os.environ.get('RADAR_STATE_KEY')
    if value:return value.encode()
    return (ROOT/'state/radar-recovery.key').read_bytes().strip()

def setup():
    secrets=json.loads(gh('secret','list','-R',REPO,'--json','name'))
    path=ROOT/'state/radar-recovery.key'
    if not any(s['name']=='RADAR_STATE_KEY' for s in secrets):
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as f:f.write(Fernet.generate_key())
        gh('secret','set','RADAR_STATE_KEY','-R',REPO,input=path.read_bytes())
    result=subprocess.run(['gh','release','view',TAG,'-R',REPO],capture_output=True)
    if result.returncode:
        gh('release','create',TAG,'-R',REPO,'--title','Encrypted radar state archive','--notes',
           'Authenticated encrypted prediction, model and source archives. Decryption key is held only in repository Actions secrets and local recovery storage. Assets are append-only; public dashboards contain derived summaries only.')
    print('State secret/release configured; key not printed')

def pack(paths):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
        for path in paths:
            if path.is_file():tar.add(path,arcname=path.relative_to(ROOT).as_posix(),recursive=False)
    return buffer.getvalue()

def save():
    paths=list((ROOT/'state/frozen').rglob('*.json'))+list((ROOT/'data/raw_prices').glob('*.csv'))
    latest=sorted((ROOT/'state/audit').glob('*/report.json'),key=lambda p:p.stat().st_mtime)
    if latest:paths+=list(latest[-1].parent.glob('*.json'))
    for name in ('model_card.json','radar.sqlite3'):
        path=ROOT/'state'/name
        if path.exists():paths.append(path)
    if not paths:raise ValueError('no state to archive')
    plain=pack(paths);cipher=Fernet(key()).encrypt(plain)
    run=os.environ.get('GITHUB_RUN_ID','local')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    target=ROOT/'outputs'/f'state-{run}-{hashlib.sha256(plain).hexdigest()[:12]}.fernet'
    with target.open('xb') as f:f.write(cipher)
    gh('release','upload',TAG,str(target),'-R',REPO)
    print(f'Encrypted state saved: {target.name}; files={len(paths)}')

def restore():
    release=json.loads(gh('api',f'repos/{REPO}/releases/tags/{TAG}'))
    assets=[a for a in release['assets'] if a['name'].endswith('.fernet') and a['state']=='uploaded']
    if not assets:
        print('First encrypted-state run: no prior archive');return
    asset=max(assets,key=lambda a:a['id'])
    cipher=gh('api',f"repos/{REPO}/releases/assets/{asset['id']}",'-H','Accept: application/octet-stream')
    plain=Fernet(key()).decrypt(cipher)
    with tarfile.open(fileobj=io.BytesIO(plain),mode='r:gz') as tar:
        for member in tar.getmembers():
            relative=Path(member.name)
            if not member.isfile() or relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe archive entry')
            if not (member.name.startswith('state/') or member.name.startswith('data/raw_prices/')):
                raise ValueError('unexpected archive namespace')
            target=ROOT/relative
            if not target.resolve().is_relative_to(ROOT.resolve()):raise ValueError('archive escaped root')
            raw=tar.extractfile(member).read()
            if target.exists() and member.name.startswith('state/frozen/'):
                if target.read_bytes()!=raw:raise ValueError('frozen restore conflict')
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(raw)
    print('Restored authenticated encrypted state:',asset['name'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['setup','save','restore']);args=p.parse_args()
    {'setup':setup,'save':save,'restore':restore}[args.action]()
