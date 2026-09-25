"""Fetch a pinned Laya checkpoint, verifying Hugging Face LFS digests."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import argparse

REPO = 'convaiinnovations/laya'
REVISION = '55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851'
DEST = Path(r'<local-model-root>\laya-55cf4c4e')
FILES = ['rl_agent_config.json', 'model.safetensors', 'encoder/config.json',
         'tokenizer/tokenizer.json', 'tokenizer/tokenizer_config.json', 'README.md']


def fetch():
    DEST.mkdir(parents=True, exist_ok=True)
    request = f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true'
    metadata = json.load(urllib.request.urlopen(request, timeout=30))
    if metadata['sha'] != REVISION:
        raise RuntimeError('Unexpected checkpoint revision')
    entries = {entry['rfilename']: entry for entry in metadata['siblings']}
    receipt = {'repo': REPO, 'revision': REVISION, 'directory': str(DEST), 'files': []}
    for name in FILES:
        destination = DEST / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = entries[name].get('lfs', {}).get('sha256')
        digest = None
        if destination.exists():
            with destination.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if not destination.exists() or (expected and digest != expected):
            print(json.dumps({'downloading': name, 'bytes': entries[name].get('size')}), flush=True)
            temporary = destination.with_name(destination.name + '.part')
            sha = hashlib.sha256()
            count = 0
            last = time.monotonic()
            url = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'
            with urllib.request.urlopen(url, timeout=45) as response, temporary.open('wb') as out:
                while chunk := response.read(4 * 1024 * 1024):
                    out.write(chunk)
                    sha.update(chunk)
                    count += len(chunk)
                    if time.monotonic() - last > 15:
                        print(json.dumps({'file': name, 'downloaded_bytes': count}), flush=True)
                        last = time.monotonic()
            digest = sha.hexdigest()
            if expected and digest != expected:
                raise RuntimeError('Published LFS hash mismatch: ' + name)
            if entries[name].get('size') and count != entries[name]['size']:
                raise RuntimeError('Unexpected file length: ' + name)
            temporary.replace(destination)
        receipt['files'].append({'path': name, 'bytes': destination.stat().st_size,
                                 'sha256': digest, 'published_lfs_sha256': expected})
    (DEST / 'download-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'downloaded_and_hashed', 'directory': str(DEST)}), flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',choices=['base','typed'],default='base')
    parser.add_argument('--destination-root',type=Path)
    args=parser.parse_args()
    if args.model=='typed':
        REPO='convaiinnovations/laya-typed-decisions'
        REVISION='1a793eb568e6718f15941d08f85432581df534e3'
        DEST=Path(r'<local-model-root>\laya-typed-1a793eb5')
    if args.destination_root:
        DEST=args.destination_root/DEST.name
    fetch()
