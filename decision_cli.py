"""Finite local Jev-style decision batch; stdin JSON, stdout JSON, no server."""
import argparse, contextlib, json, os, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--model',choices=['base','typed'],default='base')
parser.add_argument('--model-root',type=Path,default=Path(os.environ.get('JEV_MODEL_ROOT', str(Path.home() / 'Downloads' / 'Jev-Models'))))
parser.add_argument('--input',type=Path,required=True)
args=parser.parse_args()
requests=json.loads(args.input.read_text(encoding='utf-8'))
if not isinstance(requests,list) or not 1<=len(requests)<=20:
    raise SystemExit('Supply a finite JSON list of 1 to 20 requests')
for request in requests:
    if set(request)!={'id','state','questions'} or not isinstance(request['state'],str) or len(request['state'])>1500:
        raise SystemExit('Each request requires id, short text state, and questions')
    if not isinstance(request['questions'],dict) or not 1<=len(request['questions'])<=5:
        raise SystemExit('Supply one to five questions per request')
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0',TOKENIZERS_PARALLELISM='false')
sys.path.insert(0,str(ROOT/'runtime/laya-source'))
folder='laya-55cf4c4e' if args.model=='base' else 'laya-typed-1a793eb5'
model=args.model_root/folder
receipt=json.loads((model/'download-receipt.json').read_text())
expected={x['path']:x['sha256'] for x in receipt['files'] if x['path']!='README.md'}
with contextlib.redirect_stdout(sys.stderr):
    import laya,torch
    torch.set_num_threads(4)
    start=time.perf_counter()
    agent=laya.load(str(model),device='cuda',expected_sha256=expected)
    results=[]
    for request in requests:
        begin=time.perf_counter()
        result=agent.predict(request['state'],request['questions'])
        results.append(dict(id=request['id'],answers=result['answers'],milliseconds=round((time.perf_counter()-begin)*1000,3)))
print(json.dumps(dict(model=receipt['repo'],revision=receipt['revision'],device=str(agent.device),total_seconds=round(time.perf_counter()-start,3),results=results)))
