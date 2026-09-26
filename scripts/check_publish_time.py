"""Fail closed when a build would regress the public snapshot date."""
import json
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def check(new,old):
    if not new.get('as_of') or not old.get('as_of'):raise ValueError('missing comparison timestamp')
    if new['as_of']<old['as_of']:raise ValueError('refusing older data over current website')
    if new['as_of']==old['as_of'] and new.get('generated_at','')<old.get('generated_at',''):
        raise ValueError('refusing older generated snapshot')

if __name__=='__main__':
    new=json.loads((ROOT/'site/data.json').read_text(encoding='utf-8'))
    request=urllib.request.Request('https://simondongxiao.github.io/us-equity-turning-point-radar/data.json',headers={'Cache-Control':'no-cache'})
    with urllib.request.urlopen(request,timeout=90) as response:old=json.load(response)
    check(new,old);print('Publication date guard PASS:',old['as_of'],'->',new['as_of'])
