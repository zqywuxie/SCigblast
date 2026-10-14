"""Identify a detached Linux runner group without trusting a persisted PID alone."""
import os
from pathlib import Path


def group_state(pgid, token):
    """Return gone/verified/unverified; every live member must carry our token."""
    if not pgid:
        return 'gone'
    proc = Path('/proc')
    if not proc.is_dir():
        return 'unverified'
    found = False
    try:
        entries = list(proc.iterdir())
    except OSError:
        return 'unverified'
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            # comm may contain whitespace and parentheses.
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) != int(pgid) or fields[0] == 'Z':
                continue
            found = True
            if not token or f'SCIGBLAST_WEB_PROCESS_TOKEN={token}'.encode() not in (entry / 'environ').read_bytes().split(b'\0'):
                return 'unverified'
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError):
            return 'unverified'
    return 'verified' if found else 'gone'


def signal_group(pgid, token, sig):
    state = group_state(pgid, token)
    if state == 'verified':
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return 'gone'
    return state
