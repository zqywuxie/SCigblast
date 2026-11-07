"""Materialize versioned standalone HTML from existing public report materials."""
import hashlib
import json
import os
from pathlib import Path

from . import chart_data, report_html
from .io import digest, safe_file, version, write_json
from .private_cache import locked, prune_files


def purge(cache_root):
    root=Path(cache_root)
    if not root.exists():return
    with locked(root):prune_files(root,0)


def viewer_signature():
    result=hashlib.sha256()
    result.update(report_html.viewer_signature().encode())
    result.update(Path(__file__).read_bytes())
    return result.hexdigest()


def stream(data, manifest, resolve, cache_root, *, run_id='', budget_mb=128, assets=None):
    """The caller holds a source read lease through completion/disconnection."""
    from scigblast.locus_split import atomic_text
    from . import TABLE_PATHS
    names={f['png'] for f in data.get('figures',[])}
    names.update(TABLE_PATHS[key] for key in chart_data.TABLES if key in TABLE_PATHS and TABLE_PATHS[key] in manifest['files'])
    names.update(value for key,value in data.get('exports',{}).items() if key in chart_data.TABLES)
    receipts={name:version(resolve(name)) for name in sorted(names)}
    key=digest([run_id,viewer_signature(),data,receipts])
    budget=max(1,int(budget_mb))*1024**2
    # One lock also protects active response readers from bounded eviction.
    with locked(cache_root) as root:
        path=root/(key+'.html');receipt=root/(key+'.json')
        try:
            saved=json.loads(safe_file(receipt,root).read_text(encoding='utf-8'))
            with safe_file(path,root).open('rb') as incoming:valid=hashlib.file_digest(incoming,'sha256').hexdigest()==saved['sha256']
        except (OSError,ValueError,KeyError,TypeError):valid=False
        if not valid:
            assets=assets or report_html.prepare(data,Path(cache_root),resolve=resolve)
            def write(handle):
                for chunk in report_html.stream(data,assets,run_id=run_id):handle.write(chunk)
            atomic_text(path,write)
            if any(version(resolve(name))!=signature for name,signature in receipts.items()):
                path.unlink(missing_ok=True)
                raise ValueError('Report materials changed during HTML generation')
            with path.open('rb') as incoming:sha=hashlib.file_digest(incoming,'sha256').hexdigest()
            write_json(receipt,dict(sha256=sha))
        os.utime(path,None);os.utime(receipt,None)
        prune_files(root,budget,keep={path.name,receipt.name})
        try:
            with path.open('rb') as incoming:
                while block:=incoming.read(128*1024):yield block
        finally:
            # A single unusually large HTML can be served without retaining it.
            if path.stat().st_size>budget:
                path.unlink(missing_ok=True);receipt.unlink(missing_ok=True)
