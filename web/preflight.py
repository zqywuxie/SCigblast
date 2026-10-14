"""Read-only checks of the dependencies required by the next runner invocation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

FOLDERS = {'mapping': 'mapping', 'ir_split': 'IR_split', '10x_split': '10X_split',
           'igblast_base': 'Igblast_base', 'pig_igblast': 'PigIgblast'}
SUPPORTED = {'IGH', 'IGK', 'IGL', 'TRA', 'TRB', 'TRD', 'TRG'}
CONFIG_KEYS = {'PYTHON_BIN', 'SCIGBLAST_FASTP_BIN', 'SCIGBLAST_PANDASEQ_BIN',
               'SCIGBLAST_IGBLAST_BIN', 'FASTP_BIN', 'PANDASEQ_BIN', 'IGBLAST_BIN',
               'SCIGBLAST_IGBLAST_DB_DIR', 'SCIGBLAST_IGBLAST_SPECIES', 'DB_ROOT', 'PIG_DB_VERSION'}


def effective_config(pipeline, env, pipeline_root):
    """Mirror scalar dependency settings without executing the configuration file.

    FASTQ entrypoints source their config (overriding ordinary environment values).
    Mapping's Python loader applies environment values after its literal defaults.
    Runtime binary overrides are applied last in both cases.
    """
    folder = Path(pipeline_root) / FOLDERS[pipeline] / 'pipeline'
    values = {**os.environ, **{k: str(v) for k, v in env.items()}}
    config = {}
    for line in (folder / '00.pipeline_config.env').read_text(encoding='utf-8-sig').splitlines():
        match = re.match(r'^([A-Z][A-Z0-9_]*)=(.*)$', line)
        if not match or match[1] not in CONFIG_KEYS:
            continue
        parts = shlex.split(match[2], comments=True)
        if len(parts) == 1:
            config[match[1]] = parts[0]
    values = {**config, **values} if pipeline == 'mapping' else {**values, **config}
    # These are the only expansions used by dependency paths in the shipped config.
    context = {**values, 'PIPELINE_DIR': str(folder), 'SCRIPT_DIR': str(folder)}
    for key in CONFIG_KEYS & values.keys():
        values[key] = re.sub(r'\$\{([A-Za-z_][A-Za-z_0-9]*)(?::-([^}]*))?\}',
                            lambda m: context.get(m[1]) or m[2] or '', values[key])
        values[key] = re.sub(r'\$([A-Za-z_][A-Za-z_0-9]*)',
                            lambda m: context.get(m[1], ''), values[key])
    if values.get('SCIGBLAST_RUNTIME_BIN_DIR'):
        runtime = Path(values['SCIGBLAST_RUNTIME_BIN_DIR'])
        for key, command in {'PYTHON_BIN': 'python3', 'SCIGBLAST_FASTP_BIN': 'fastp',
                             'SCIGBLAST_PANDASEQ_BIN': 'pandaseq', 'SCIGBLAST_IGBLAST_BIN': 'igblastn',
                             'FASTP_BIN': 'fastp', 'PANDASEQ_BIN': 'pandaseq', 'IGBLAST_BIN': 'igblastn'}.items():
            values[key] = str(runtime / command)
    return values, folder


def check_dependencies(pipeline, env, chains=None, *, pipeline_root=None, stage=None):
    stage = stage or ('match' if pipeline != 'mapping' and env.get('SCIGBLAST_WEB_MATCH_ONLY') == '1' else 'analysis')
    result = dict(valid=True, errors=[], warnings=[], commands={}, missing_modules=[],
                  missing_databases=[], stage=stage)

    def error(code, message, field='environment'):
        result['errors'].append(dict(code=code, message=message, field=field, blocking=True, stage=stage))
        result['valid'] = False

    if pipeline not in FOLDERS or stage not in {'match', 'analysis'}:
        error('invalid_pipeline', '未知分析流程或阶段', 'pipeline')
        return result
    root = Path(pipeline_root) if pipeline_root else Path(__file__).resolve().parent.parent
    try:
        config, folder = effective_config(pipeline, env, root)
    except (OSError, ValueError) as exc:
        error('configuration_unavailable', f'无法读取流程配置：{exc}')
        return result
    configured = {'bash': 'bash', 'flock': 'flock', 'python': config.get('PYTHON_BIN', 'python3')}
    if stage == 'analysis':
        configured['igblastn'] = config.get('SCIGBLAST_IGBLAST_BIN', config.get('IGBLAST_BIN', 'igblastn'))
        if pipeline != 'mapping':
            configured.update(fastp=config.get('SCIGBLAST_FASTP_BIN', 'fastp'),
                              pandaseq=config.get('SCIGBLAST_PANDASEQ_BIN', config.get('PANDASEQ_BIN', 'pandaseq')))
    for name, command in configured.items():
        if ('/' in command or '\\' in command) and not Path(command).is_absolute():
            command = str(folder / command)
        result['commands'][name] = shutil.which(command, path=config.get('PATH'))
        if not result['commands'][name] and name == 'igblastn' and pipeline == 'igblast_base':
            db_root = Path(config.get('SCIGBLAST_IGBLAST_DB_DIR', ''))
            if not db_root.is_absolute():
                db_root = folder / db_root
            fallback = db_root / 'bin/igblastn'
            if fallback.is_file() and os.access(fallback, os.X_OK):
                result['commands'][name] = str(fallback)
        if not result['commands'][name]:
            error('missing_command', f'运行环境缺少 {name}：{command}')
    modules = []
    if stage == 'analysis' and pipeline in {'ir_split', '10x_split'}:
        modules = ['Bio', 'pandas', 'numpy']
        if pipeline == 'ir_split':
            modules += ['parmap', 'skbio']
    if result['commands'].get('python'):
        code = 'import importlib.util,json; print(json.dumps([m for m in ' + repr(modules) + ' if importlib.util.find_spec(m) is None]))'
        try:
            check = subprocess.run([result['commands']['python'], '-c', code], capture_output=True,
                                   text=True, timeout=15, env=config, cwd=folder)
            missing = json.loads(check.stdout) if check.returncode == 0 else ['Python 环境检查失败']
            if not isinstance(missing, list):
                raise ValueError('invalid module check response')
        except (OSError, subprocess.TimeoutExpired, ValueError):
            missing = ['Python 环境检查失败']
        result['missing_modules'] = missing
        for module in missing:
            error('missing_module', f'运行所用 Python 缺少依赖或不可用：{module}')
    if stage == 'match':
        result['warnings'].append(dict(code='analysis_check_pending', field='environment', blocking=False,
                                       message='本次仅匹配样本；确认分析时将检查比对工具及所需参考库。', stage=stage))
        return result
    selected = {str(c).upper() for c in (chains or [])}
    if not selected or not selected.issubset(SUPPORTED):
        error('select_chains', '请先识别并选择有效链，或核对 Match 中的链信息',
              'mapping_chains' if pipeline == 'mapping' else ('input_assignments' if env.get('SCIGBLAST_RUN_INPUT_MANIFEST') else 'submission_path'))
        return result
    pig = pipeline == 'pig_igblast'
    species = 'pig' if pig else ('human' if pipeline == 'mapping' else config.get('SCIGBLAST_IGBLAST_SPECIES', 'human'))
    database_root = Path(config.get('DB_ROOT' if pig else 'SCIGBLAST_IGBLAST_DB_DIR', ''))
    if not database_root.is_absolute():
        database_root = folder / database_root
    version = config.get('PIG_DB_VERSION', 'pig_new') if pig else species
    aux = database_root / 'optional_file' / ('pig.aux' if pig else f'{species}_gl.aux')
    missing_db = []
    if not aux.is_file():
        missing_db.append(f'辅助文件不可用：{aux}')
    # Match the arguments actually passed by each branch (including VJ placeholders).
    parts = ('V', 'D', 'J', 'C') if pipeline in {'ir_split', '10x_split'} else (
        ('V', 'D', 'J') if pig else ('V', 'J'))
    for chain in sorted(selected):
        for part in parts:
            prefix = database_root / 'database_251117' / version / chain / f'{species}_gl_{chain}_{part}'
            if not any(p.is_file() for p in prefix.parent.glob(prefix.name + '.*')):
                missing_db.append(f'所选 {chain} {part} 参考库不可用：{prefix}')
    result['missing_databases'] = missing_db
    for message in missing_db:
        error('missing_database', message)
    return result
