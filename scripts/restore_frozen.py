"""Restore cumulative forecast archive from the last successful Actions artifact.

Artifacts have retention limits: this is recoverable continuity, not permanent
object storage. Fail closed if a prior v2 artifact lacks its frozen archive.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
ROOT=Path(__file__).resolve().parents[1]

def main():
    repo=os.environ['GITHUB_REPOSITORY']
    runs=json.loads(subprocess.check_output(['gh','run','list','-R',repo,'--workflow','publish.yml','--status','success','--limit','1','--json','databaseId'],text=True))
    if not runs:return
    run_id=str(runs[0]['databaseId']);target=ROOT/'outputs/restore-prior'
    subprocess.run(['gh','run','download',run_id,'-R',repo,'-n','radar-derived-'+run_id,'-D',str(target)],check=True)
    source=target/'state/frozen'
    if source.exists():
        for path in source.rglob('*.json'):
            dest=ROOT/'state/frozen'/path.relative_to(source)
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists() and dest.read_bytes()!=path.read_bytes():raise ValueError('archive restore conflict')
            if not dest.exists():shutil.copy2(path,dest)
        print('Restored cumulative prediction archive; retention limit still applies')
    else:
        if list(target.rglob('*-model.json')):raise RuntimeError('v2 audit exists but frozen archive missing')
        print('Legacy build: no previous v2 archive to restore')

if __name__=='__main__':main()
