"""Push exact unsigned local commits via gh API when git HTTPS is unavailable."""
import base64
import json
import subprocess

def git(*args):return subprocess.check_output(['git',*args]).decode().strip()
def api(repo,path,payload=None):
    command=['gh','api',f'repos/{repo}/{path}']
    if payload is not None:command+=['--method','PATCH' if path.startswith('git/refs/') else 'POST','--input','-']
    return json.loads(subprocess.check_output(command,input=json.dumps(payload).encode() if payload is not None else None))

def main():
    repo='simondongxiao/us-equity-turning-point-radar'
    remote=api(repo,'git/ref/heads/main')['object']['sha']
    subprocess.run(['git','merge-base','--is-ancestor',remote,'HEAD'],check=True)
    commits=git('rev-list','--reverse',f'{remote}..HEAD').splitlines()
    for commit in commits:
        parent=git('rev-parse',commit+'^')
        entries=[]
        for path in git('diff','--name-only',parent,commit).splitlines():
            spec=git('ls-tree',commit,'--',path)
            if not spec:
                entries.append({'path':path,'mode':'100644','type':'blob','sha':None});continue
            mode,kind,sha=spec.split('\t')[0].split()
            if kind!='blob':raise ValueError('unsupported git object')
            raw=subprocess.check_output(['git','cat-file','blob',sha])
            created=api(repo,'git/blobs',{'encoding':'base64','content':base64.b64encode(raw).decode()})
            if created['sha']!=sha:raise ValueError('blob mismatch')
            entries.append({'path':path,'mode':mode,'type':'blob','sha':sha})
        tree=api(repo,'git/trees',{'base_tree':git('rev-parse',parent+'^{tree}'),'tree':entries})
        if tree['sha']!=git('rev-parse',commit+'^{tree}'):raise ValueError('tree mismatch')
        author=dict(zip(('name','email','date'),git('show','-s','--format=%an%n%ae%n%aI',commit).splitlines()))
        committer=dict(zip(('name','email','date'),git('show','-s','--format=%cn%n%ce%n%cI',commit).splitlines()))
        message=subprocess.check_output(['git','show','-s','--format=%B',commit]).decode().rstrip('\n')+'\n'
        created=api(repo,'git/commits',{'tree':tree['sha'],'parents':[parent],'author':author,'committer':committer,'message':message})
        if created['sha']!=commit:raise ValueError(f'commit mismatch {created["sha"]} != {commit}')
    if api(repo,'git/ref/heads/main')['object']['sha']!=remote:raise ValueError('remote moved; refusing overwrite')
    if commits:api(repo,'git/refs/heads/main',{'sha':commits[-1],'force':False})
    print('Verified remote HEAD:',api(repo,'git/ref/heads/main')['object']['sha'])

if __name__=='__main__':main()
