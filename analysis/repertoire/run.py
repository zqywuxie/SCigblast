"""CLI entry for derived statistics and report-ready figures; never runs IgBLAST."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import uuid
from contextlib import closing

from . import TABLE_PATHS, VERSION, FIGURE_VERSION
from .aggregate import aggregate
from .catalog import build_catalog
from .io import digest, version, write_json, write_table
from .render import render
from .sources import discover, files_under, verify
from pipeline_stage_metrics import collect, summarize_volume
from .usage import build as build_usage, connect as usage_connection, USAGE_COLUMNS


def execute(request, output):
    output = Path(output)
    if output.exists():
        raise ValueError('派生分析输出目录已存在；请使用新的版本目录')
    output.parent.mkdir(parents=True,exist_ok=True)
    staging = output.parent/('.'+output.name+'.'+uuid.uuid4().hex)
    staging.mkdir()
    root = Path(request['input_root']).resolve()
    items = request.get('items') or discover(root)
    if not items:
        raise ValueError('未发现可用的单链 AIRR 结果')
    verify(items,root)
    def progress(done,total,message,phase='statistics'):
        if request.get('progress_path'):
            write_json(request['progress_path'],dict(done=done,total=total,message=message,phase=phase))
        print(f'[REPORT] {done}/{total} {message}',flush=True)
    try:
        cached = request.get('statistics_source')
        if cached:
            from .io import safe_file
            folder = Path(cached['root']).resolve()
            summary_path = safe_file(folder/'summary.json',folder)
            index_path = safe_file(folder/'details.sqlite',folder)
            if version(summary_path) != cached['summary_version'] or version(index_path) != cached['index_version']:
                raise ValueError('已统计数据发生变化，请重新生成')
            manifest=json.loads(safe_file(folder/'manifest.json',folder).read_text(encoding='utf-8'))
            if manifest.get('statistics_version')!=version(index_path):
                raise ValueError('统计索引与完成版本不一致，请重新生成')
            data=json.loads(summary_path.read_text(encoding='utf-8'))
            if data.get('version') != VERSION or not data.get('usage'):
                raise ValueError('该历史分析需要先升级统计，才能仅重绘图片')
            shutil.copy2(index_path,staging/'details.sqlite')
            catalog=data.get('catalog')
            data['figure_primers']=request.get('figure_primers',[])
            data['figure_groups']=request.get('figure_groups',[])
        else:
            catalog = None
            if request.get('primer'):
                config = request['primer']
                catalog = build_catalog(config['submission_path'],config['primer_path'],config.get('columns',{}),sorted({item['sample_label'] for item in items}))
            else:
                frozen=[]
                for item in items:
                    if item.get('trace'):
                        marker=json.loads(Path(item['trace']).read_text(encoding='utf-8'))
                        if marker.get('primer_preparation'):frozen.append(marker['primer_preparation']['catalog'])
                if len(frozen)==len(items) and len({value['version'] for value in frozen})==1:
                    catalog=frozen[0]
            if catalog and request.get('figure_primers'):
                catalog=json.loads(json.dumps(catalog))
                catalog['config']['figure_primers']=request['figure_primers']
                catalog['version']=digest([catalog['assignments'],catalog['config']])
            receipts={str(path):version(path) for path in files_under(root) if path.name in {'fastp_summary.csv','clean_summary.csv','clean_counts.csv','pandaseq_summary.csv'} or path.suffix=='.json' or path.name=='Datapoint.csv'}
            data = aggregate(items,root,staging,catalog,progress)
            data.update(version=VERSION,stage_summary_scope='sample',gene_segments=['V','J','C'],stages=collect(root,items),catalog=catalog)
            data['usage']=build_usage(items,root,staging,catalog,progress,comparison=request.get('comparison_reference'))
            data['figure_primers']=request.get('figure_primers') or (catalog or {}).get('config',{}).get('figure_primers',[])
            data['stage_volumes']=summarize_volume(data['stages'],data['samples'])
            if any(version(path)!=signature for path,signature in receipts.items()):
                raise ValueError('阶段报告在统计期间发生变化，请重新扫描')
        exports = {'stage_volume_summary':data['stage_volumes'],'stage_summary':data['stages'],'sample_summary':data['samples'],'primer_counts':data['primer_counts'],
            'primer_gene_counts':data['gene_counts'],'gene_coverage':data['gene_coverage'],
            'matching_summary':data['match_counts'],'matching_total_summary':data['matching_total_summary'],
            'filter_summary':data['filter_counts']}
        if not cached:
            from .io import table,safe_file
            candidates={}
            for item in items:
                if not item.get('summary_path'):continue
                parent=Path(item['summary_path']).parent
                for mapping,preprocessing in (('05.igblastn_out','06.preprocessing'),('07.igblastn_out','08.preprocessing')):
                    if parent.parent.name==mapping:
                        path=parent.parent.parent/preprocessing/parent.name/'Datapoint.csv'
                    elif parent.name==mapping:
                        path=parent.parent/preprocessing/'Datapoint.csv'
                    else:continue
                    if path.is_file() and path.resolve().is_relative_to(root):
                        candidates.setdefault(safe_file(path,root),set()).add(item['sample_label'])
            datapoints=[]
            for path,labels in sorted(candidates.items()):
                relative=path.relative_to(root)
                for record in table(path):
                    if record.get('sample') in labels:
                        for key,value in list(record.items()):
                            if key not in {'sample','batch'}:
                                try:
                                    number=float(value)
                                    record[key]=int(number) if number.is_integer() else number if __import__('math').isfinite(number) else None
                                except (TypeError,ValueError):pass
                        datapoints.append(dict(record,source=str(relative),scope='original_pipeline_selection',count_basis='see_processing_manifest'))
            data['preprocessing_metrics']=datapoints
        if data.get('preprocessing_metrics'):exports['preprocessing_metrics']=data['preprocessing_metrics']
        exports['isotype_summary']=data['usage']['isotypes']
        exports['sequence_family_matching']=data['family_counts']
        exports['primer_preparation_summary']=data['trim_counts']
        for name,rows in exports.items():
            columns = list(rows[0]) if rows else ['sample','count']
            write_table(staging/TABLE_PATHS[name],rows,columns)
        # These potentially large tables are streamed from the private index.
        sql_exports={
            'sample_gene_usage':"SELECT * FROM gene_usage WHERE primer='' AND unique_count>0 ORDER BY sample,chain,group_id,segment,unique_count DESC,gene",
            'reference_gene_observation':"SELECT * FROM gene_usage WHERE primer='' ORDER BY sample,chain,group_id,segment,gene",
            'primer_gene_usage':"SELECT * FROM gene_usage WHERE primer<>'' AND unique_count>0 ORDER BY sample,chain,group_id,primer,segment,unique_count DESC,gene",
            'unobserved_reference_genes':"SELECT * FROM gene_usage WHERE primer='' AND reference_catalog=1 AND status='zero_observed' ORDER BY sample,chain,group_id,segment,gene",
            'primer_unobserved_reference_genes':"SELECT * FROM gene_usage WHERE primer<>'' AND reference_catalog=1 AND status='zero_observed' ORDER BY sample,chain,group_id,primer,segment,gene",
        }
        with closing(usage_connection(staging/'details.sqlite')) as connection:
            for name,sql in sql_exports.items():
                write_table(staging/TABLE_PATHS[name],(dict(r) for r in connection.execute(sql)),USAGE_COLUMNS)
        data['exports']={name:TABLE_PATHS[name] for name in exports}
        from .workbook import write_workbook
        data['workbook']=write_workbook(staging/'report.xlsx',exports,sql_exports,staging/'details.sqlite',catalog=data.get('catalog'))
        progress(len(items),len(items),'正在生成汇报图片')
        data['figure_version']=FIGURE_VERSION
        data['stage_overview_format']='compact-counts-and-changes-v2'
        data['figures']=render(data,staging,top_n=min(50,max(5,int(request.get('top_n',20)))),
            figure_progress=lambda done,total,title:progress(done,total,f'绘制基因与 Isotype PNG {done}/{total} · {title}',phase='figures'))
        verify(items,root)
        if not cached and any(version(path)!=signature for path,signature in receipts.items()):
            raise ValueError('阶段或 preprocessing 指标在统计期间变化，请重新生成')
        data['exports'].update({name:TABLE_PATHS[name] for name in sql_exports if name in TABLE_PATHS})
        # Keep the first Web response independent of primer × reference table size.
        overview={key:value for key,value in data.items() if key not in {'gene_counts','gene_coverage','filter_counts','family_counts','trim_counts','catalog','preprocessing_metrics'}}
        overview['catalog']={'assignments':{sample:[{'name':p['name'],'group':p.get('group','')} for p in values] for sample,values in (catalog or {}).get('assignments',{}).items()}}
        write_json(staging/'summary.json',data)
        write_json(staging/'overview.json',overview)
        manifest=dict(version=VERSION,figure_version=FIGURE_VERSION,run_id=request.get('run_id'),input_root=str(root),items=items,
            matching_rule='5prime-full-IUPAC-exact; read-N-unmatched; no-RC/no-offset',
            primer=catalog,unit='chain detail observations; matching totals deduplicated by archived source and ordinal; legacy final-only rows remain chain-specific',
            files=[p.relative_to(staging).as_posix() for p in staging.rglob('*') if p.is_file() and p.suffix!='.sqlite' and not any(part.startswith('.') for part in p.relative_to(staging).parts)])
        manifest['file_versions']={name:version(staging/name) for name in manifest['files']}
        manifest['statistics_version']=version(staging/'details.sqlite')
        write_json(staging/'manifest.json',manifest)
        os.replace(staging,output)
        progress(len(items),len(items),'汇总与图片生成完成')
        return dict(figures=len(data['figures']),samples=len(data['samples']))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    def stop(_number,_frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM,stop)
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--request',type=Path)
    source.add_argument('--input',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    request=json.loads(args.request.read_text(encoding='utf-8')) if args.request else dict(input_root=str(args.input.resolve()))
    execute(request,args.output)


if __name__=='__main__':
    main()
