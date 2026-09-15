"""Verify delivered files against SHA256SUMS.json. Generated changes will change hashes."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'SHA256SUMS.json').read_text(encoding='utf-8'))
failed=[]
for name,expected in manifest.items():
    p=(ROOT/name).resolve()
    if not p.is_relative_to(ROOT.resolve()) or not p.is_file():failed.append(name+': missing/invalid path');continue
    if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:failed.append(name+': changed')
if failed:
    print('\n'.join(failed));sys.exit(1)
print(f'PASS: {len(manifest)} delivered-file hashes. This is file integrity, not financial validation.')
