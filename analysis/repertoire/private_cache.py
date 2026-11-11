"""Owned cache locks and bounded cleanup; source reports are never modified."""
from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import time
import shutil
import subprocess
import sys


def copy_snapshot(source,destination):
    """Independent SQLite inode; use filesystem CoW where Linux supports it."""
    from .io import version
    source,destination=Path(source),Path(destination)
    before=version(source)
    if destination.exists() or destination.is_symlink():raise ValueError('Statistics snapshot already exists')
    method='copy'
    if sys.platform.startswith('linux'):
        try:
            result=subprocess.run(['cp','--reflink=always','--',str(source),str(destination)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            if result.returncode==0:
                shutil.copystat(source,destination);method='reflink'
            else:destination.unlink(missing_ok=True)
        except FileNotFoundError:pass
    if method=='copy':shutil.copy2(source,destination)
    if version(source)!=before:
        destination.unlink(missing_ok=True)
        raise ValueError('Statistics changed while taking snapshot')
    return method


def directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Cache path contains a symbolic link')
    path.mkdir(parents=True,exist_ok=True)
    return path


@contextmanager
def locked(root, *, blocking=True):
    root=directory(root)
    path=root/'.lock'
    if path.is_symlink():raise ValueError('Cache lock contains a symbolic link')
    with path.open('a+b') as handle:
        if os.name=='posix':
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        else:
            import msvcrt
            if path.stat().st_size==0:handle.write(b'0');handle.flush()
            while True:
                try:
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if not blocking:raise BlockingIOError('Cache entry in use')
                    time.sleep(.05)
        try:yield root
        finally:
            if os.name=='posix':fcntl.flock(handle,fcntl.LOCK_UN)
            else:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def prune_files(root, budget, *, keep=()):
    """Caller holds the directory lock, including while reading cached files."""
    root=Path(root).resolve();keep=set(keep)
    files=[p for p in root.iterdir() if p.is_file() and not p.is_symlink() and p.suffix in {'.html','.json','.sqlite','.png'}]
    total=sum(p.stat().st_size for p in files)
    entries={}
    for path in files:entries.setdefault(path.stem,[]).append(path)
    for key,paths in sorted(entries.items(),key=lambda entry:max(p.stat().st_mtime_ns for p in entry[1])):
        if total<=budget:break
        if any(path.name in keep for path in paths):continue
        try:
            with locked(root/'.entries'/key,blocking=False) if (root/'.entries').is_dir() else nullcontext():
                for path in paths:
                    if not path.exists():continue
                    size=path.stat().st_size
                    if path.resolve().parent!=root:raise ValueError('Unsafe cache cleanup path')
                    path.unlink();total-=size
        except BlockingIOError:continue
