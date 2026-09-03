# IR preprocessing models

These files are vendored from `preprocess/models/` and intentionally keep the
same calculation definitions. IR-specific concerns (representative-state
lookup, ID aliases, chunking and worker limits) are kept in the Stage 08
entry point (`../08.preprocessing.py`); do not put pipeline scheduling
changes in these model modules.
