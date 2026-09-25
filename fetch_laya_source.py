"""Reproduce the audited inference source at a pinned commit, without installation."""
import hashlib, io, json, urllib.request, zipfile
from pathlib import Path, PurePosixPath
ROOT=Path(__file__).resolve().parent
REVISION='4066d5d5fbf08b66c6757ddeedbd797bd7655bc0'
URL=f'https://codeload.github.com/NandhaKishorM/laya/zip/{REVISION}'
dest=ROOT/'runtime/laya-source'
if dest.exists():
    raise SystemExit('Source already present; refusing overwrite. See runtime/source-receipt.json.')
data=urllib.request.urlopen(URL,timeout=60).read()
with zipfile.ZipFile(io.BytesIO(data)) as z:
    count=0
    for info in z.infolist():
        parts=PurePosixPath(info.filename).parts[1:]
        if not parts or info.is_dir() or any(p in ('.','..') for p in parts): continue
        rel=PurePosixPath(*parts)
        if not (str(rel).startswith('laya/') and rel.suffix=='.py') and str(rel) not in ('LICENSE','pyproject.toml'): continue
        p=dest.joinpath(*parts)
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(z.read(info)); count+=1
receipt=dict(repo='https://github.com/NandhaKishorM/laya',revision=REVISION,url=URL,zip_sha256=hashlib.sha256(data).hexdigest(),file_count=count)
(ROOT/'runtime/source-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
print(json.dumps(receipt))
