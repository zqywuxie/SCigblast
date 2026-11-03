"""Resolve C calls for derived metrics without choosing a multi-gene candidate."""
from functools import lru_cache
import re


@lru_cache(maxsize=4096)
def unique_constant_gene(call):
    if not isinstance(call, str):
        return None
    genes={value.strip().split('*')[0] for value in re.split(r'[,;]',call)
           if value.strip().upper() not in {'','*','-','NA','N/A','NONE','NULL','NO_HIT','UNMAPPED','NOT_FOUND'}}
    return next(iter(genes)) if len(genes)==1 else None
