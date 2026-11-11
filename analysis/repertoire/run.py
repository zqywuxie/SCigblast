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
from .catalog import build_catalog
from .io import digest, version, write_json, write_table
from .render import render
from .sources import discover, verify
from scigblast.stage_metrics import collect, summarize_volume
from .usage import connect as usage_connection
from .performance import Performance


def execute(request, output):
    if request.get('analysis_kind')=='primer_check':
        from .primer_check import execute as check_primers
        return check_primers(request, output)
    from .statistics_checkpoint import checkpoint_for
    root=Path(request['input_root']).resolve()
    items=request.get('items') or discover(root)
    if not items:raise ValueError('未发现可用的单链 AIRR 结果')
    verify(items,root)
    if Path(output).exists():raise ValueError('派生分析输出目录已存在；请使用新的版本目录')
    with checkpoint_for(request,output,items,root) as checkpoint:
        return _execute(dict(request,items=items),output,checkpoint)


def _execute(request, output, checkpoint):
    output = Path(output)
    if output.exists():
        raise ValueError('派生分析输出目录已存在；请使用新的版本目录')
    root = Path(request['input_root']).resolve()
    items = request.get('items') or discover(root)
    if not items:
        raise ValueError('未发现可用的单链 AIRR 结果')
    verify(items,root)
    output.parent.mkdir(parents=True,exist_ok=True)
    staging = output.parent/('.'+output.name+'.'+uuid.uuid4().hex)
    staging.mkdir()
    performance = Performance(request.get('performance_path') or staging/'.performance.json')
    performance.monitor(lambda: staging)
    performance.data.update(items=len(items),statistics_reuse=bool(request.get('statistics_source')))
    def progress(done,total,message,phase='statistics'):
        if request.get('progress_path'):
            write_json(request['progress_path'],dict(done=done,total=total,message=message,phase=phase))
        print(f'[REPORT] {done}/{total} {message}',flush=True)
    try:
        recovered=checkpoint.load()
        cached = request.get('statistics_source')
        if recovered is not None:
            staging.rmdir();staging=checkpoint.folder
            checkpoint.prepare_retry()
            data=recovered;catalog=data.get('catalog');folder=staging
            cached={'root':str(folder)}
            data['figure_primers']=request.get('figure_primers',[])
            data['figure_groups']=request.get('figure_groups',[])
            performance.data['statistics_checkpoint_reused']=True
            progress(len(items),len(items),'已复用完整统计，继续生成报告',phase='figures')
        elif cached:
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
            from .private_cache import copy_snapshot
            performance.data['statistics_snapshot_method']=performance.call('reuse_statistics',copy_snapshot,index_path,staging/'details.sqlite')
            if data_path := manifest.get('scenario_statistics_version'):
                scenario_path=safe_file(folder/'scenario_details.sqlite',folder)
                if version(scenario_path)!=data_path:
                    raise ValueError('Suspected-primer statistics changed; regenerate the report')
                performance.call('reuse_scenario_statistics',copy_snapshot,scenario_path,staging/'scenario_details.sqlite')
            catalog=data.get('catalog')
            data['figure_primers']=request.get('figure_primers',[])
            data['figure_groups']=request.get('figure_groups',[])
        else:
            catalog = None
            if request.get('primer'):
                config = request['primer']
                from .catalog import chains_by_sample
                scope=chains_by_sample(items)
                catalog = build_catalog(config.get('submission_path'),config['primer_path'],config.get('columns',{}),sorted(scope),scope)
            else:
                frozen=[]
                for item in items:
                    if item.get('trace'):
                        marker=json.loads(Path(item['trace']).read_text(encoding='utf-8'))
                        if marker.get('primer_preparation'):frozen.append(marker['primer_preparation']['catalog'])
                if len(frozen)==len(items) and len({value['version'] for value in frozen})==1:
                    catalog=frozen[0]
            checked=None
            if request.get('primer_check_root'):
                from .primer_check import load as load_primer_check
                checked=performance.call('reuse_primer_check',load_primer_check,request['primer_check_root'],root,items,catalog,
                    request.get('allowed_input_roots',[str(root)]))
            if catalog and request.get('figure_primers'):
                catalog=json.loads(json.dumps(catalog))
                catalog['config']['figure_primers']=request['figure_primers']
                catalog['version']=digest([catalog['assignments'],catalog.get('groups',{}),catalog['config']])
            receipts=dict(checkpoint.receipts)
            # Gene lineage always uses mapping inputs. Raw is assessed in a
            # separate table, so selecting raw matching cannot change gene ownership.
            if catalog:
                catalog=json.loads(json.dumps(catalog))
                catalog['config']['observation_stage']='mapping'
                catalog['version']=digest([catalog['assignments'],catalog.get('groups',{}),catalog['config']])
            from .compute_jobs import statistics as compute_statistics
            data,usage = performance.call('sample_statistics',compute_statistics,items,root,staging,catalog,progress,
                performance=performance.data,comparison=request.get('comparison_reference'))
            data.update(version=VERSION,stage_summary_scope='sample',gene_segments=['V','J','C'],
                        stages=collect(root,items),catalog=catalog,usage=usage)
            with closing(usage_connection(staging/'details.sqlite')) as connection:
                traced={i['id'] for i in items if i.get('trace')}
                for sample in data['samples']:
                    sample['mapped_reads']=connection.execute('SELECT count(*) FROM usage_records WHERE source_item=? AND eligible=1',(sample['id'],)).fetchone()[0] if sample['id'] in traced else None
                    sample['retained_reads']=connection.execute("SELECT count(*) FROM observations WHERE sample_key=? AND chain=? AND filter_reason IN ('retained','retained_history','imported_result')",(sample['sample_key'],sample['chain'])).fetchone()[0]
            from .primer_metrics import build_raw as primer_metrics
            metrics=checked['metrics'] if checked else performance.call('primer_raw_matching',primer_metrics,items,root,staging,catalog,
                allowed_roots=request.get('allowed_input_roots',[str(root)]),progress=progress)
            data.update(primer_mapping_summary=metrics['rows'],dual_matching_summary=metrics['matching'],
                primer_stage_availability=metrics['availability'],primer_matching_source='raw_R1',
                primer_raw_sources=metrics['sources'])
            data['matching_totals']=metrics['matching']
            data['matching_total_summary']=metrics['matching']
            data['match_counts']=metrics['chain_matching']
            from .alternatives import compare_raw
            raw_review=checked['review'] if checked else performance.call('alternative_raw_groups',compare_raw,items,root,catalog,metrics['matching'],
                allowed_roots=request.get('allowed_input_roots',[str(root)]),progress=progress)
            data.update(primer_group_reviews=raw_review['reviews'],primer_group_comparisons=raw_review['comparisons'],primer_scenarios=[])
            data['figure_primers']=request.get('figure_primers') or (catalog or {}).get('config',{}).get('figure_primers',[])
            data['stage_volumes']=summarize_volume(data['stages'],data['samples'])
            if any(version(path)!=signature for path,signature in receipts.items()):
                raise ValueError('阶段报告在统计期间发生变化，请重新扫描')
        if cached and recovered is None:
            # Stage receipts are inexpensive metadata. Refresh them when
            # repainting an old report so corrected identity joins do not
            # require re-reading FASTQ/AIRR or recomputing gene ownership.
            data['stages']=performance.call('stage_metrics_refresh',collect,root,items)
            data['stage_volumes']=summarize_volume(data['stages'],data['samples'])
        # Observation diagnostics include failed queries. They retain counts but
        # must not expose the former input-normalized percentages as gene usage.
        data['gene_counts']=[{key:value for key,value in row.items() if key not in
            {'denominator','percent','state_denominator','state_percent'}} for row in data['gene_counts']]
        exports = {'stage_volume_summary':data['stage_volumes'],'stage_summary':data['stages'],'sample_summary':data['samples'],'primer_counts':data['primer_counts'],
            'primer_gene_counts':data['gene_counts'],'gene_coverage':data['gene_coverage'],
            'matching_summary':data['match_counts'],'matching_total_summary':data['matching_total_summary'],
            'filter_summary':data['filter_counts']}
        if data.get('primer_matching_source')=='raw_R1':exports.pop('primer_counts',None)
        exports['primer_group_comparisons']=data.get('primer_group_comparisons', [])
        exports['primer_mapping_summary']=data.get('primer_mapping_summary',[])
        exports['dual_matching_summary']=data.get('dual_matching_summary',[])
        from .scenario_analysis import EXPORTS as SCENARIO_EXPORTS
        for name in SCENARIO_EXPORTS:
            if data.get('primer_scenarios'):exports[name]=data.get(name,[])
        exports['primer_stage_availability']=data.get('primer_stage_availability',[])
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
        if not cached:
            from .output_metrics import build as output_metrics
            data['sample_output_summary']=output_metrics(data,items,root)
        exports['sample_output_summary']=data.get('sample_output_summary',[])
        from .presentation import shared_inputs
        exports['sample_input_summary']=shared_inputs(data)
        if not cached:
            from .repertoire_metrics import build as build_repertoire_metrics
            data.update(performance.call('repertoire_metrics',build_repertoire_metrics,data, staging/'details.sqlite', root,cache_root=output.parent/'.statistics_cache'))
        exports['diversity_summary']=data.get('diversity_summary',[])
        exports['bcell_metrics']=data.get('bcell_metrics',[])
        exports['isotype_distribution']=data.get('isotype_distribution',[])
        from .isotype import count_unit
        data['usage']['isotypes']=[dict(row,unit=count_unit(row.get('metric'),row.get('subset')=='postprocessed')) for row in data['usage']['isotypes']]
        exports['isotype_summary']=data['usage']['isotypes']
        from .presentation import annotation_coverage
        if not cached or 'annotation_coverage' not in data:
            with closing(usage_connection(staging/'details.sqlite')) as connection:
                data['annotation_coverage']=performance.call('annotation_coverage',annotation_coverage,connection)
        exports['annotation_coverage']=data['annotation_coverage']
        exports['sequence_family_matching']=data['family_counts']
        exports['primer_preparation_summary']=data['trim_counts']
        for name,rows in exports.items():
            columns = list(rows[0]) if rows else ['sample','count']
            if name=='primer_group_comparisons':
                from .alternatives import COMPARISON_COLUMNS
                columns=COMPARISON_COLUMNS
            performance.call('csv:'+name,write_table,staging/TABLE_PATHS[name],rows,columns)
        # These potentially large tables are streamed from the private index.
        sql_exports={
            'sample_gene_usage':"SELECT * FROM gene_usage WHERE primer='' AND unique_count>0 ORDER BY sample,chain,group_id,segment,gene,metric,aa_field",
            'reference_gene_observation':"SELECT * FROM gene_usage WHERE primer='' AND metric='expression' ORDER BY sample,chain,group_id,segment,gene",
            'primer_reference_gene_observation':"SELECT * FROM gene_usage WHERE primer<>'' AND metric='expression' ORDER BY sample,chain,group_id,primer,segment,gene",
            'primer_gene_usage':"SELECT * FROM gene_usage WHERE primer<>'' AND unique_count>0 ORDER BY sample,chain,group_id,primer,segment,gene,metric,aa_field",
            'unobserved_reference_genes':"SELECT * FROM gene_usage WHERE primer='' AND metric='expression' AND reference_catalog=1 AND status='zero_observed' ORDER BY sample,chain,group_id,segment,gene",
            'primer_unobserved_reference_genes':"SELECT * FROM gene_usage WHERE primer<>'' AND metric='expression' AND reference_catalog=1 AND status='zero_observed' ORDER BY sample,chain,group_id,primer,segment,gene",
        }
        with closing(usage_connection(staging/'details.sqlite')) as connection:
            for name,sql in sql_exports.items():
                cursor=connection.execute(sql)
                performance.call('csv:'+name,write_table,staging/TABLE_PATHS[name],(dict(r) for r in cursor),[field[0] for field in cursor.description])
        from .report_rules import for_report
        data['statistic_rules']=for_report(data)
        data['task_name']=str(request.get('task_name') or data.get('task_name') or root.name)
        data['exports']={name:TABLE_PATHS[name] for name in exports}
        from .workbook_overview import sample_rows
        data['sample_overview']=sample_rows(data['samples'],data.get('catalog'))
        if not cached:
            from datetime import datetime,timezone
            data['statistics_created_at']=datetime.now(timezone.utc).isoformat()
        if recovered is None:
            verify(items,root)
            from .storage_audit import inspect as inspect_storage
            performance.data['statistics_storage']=[inspect_storage(staging/name) for name in
                ('details.sqlite','scenario_details.sqlite') if (staging/name).is_file()]
            with performance.phase('statistics_checkpoint'):
                staging=checkpoint.seal(staging,data)
                if not request.get('performance_path'):performance.path=staging/'.performance.json'
            folder=staging if not cached else folder
        # Keep the private phase receipt attached to the moved work directory.
        if not request.get('performance_path'):performance.path=staging/'.performance.json'
        from .workbook import write_workbook
        data['workbook']=performance.call('workbook',write_workbook,staging/'report.xlsx',exports,sql_exports,staging/'details.sqlite',catalog=data.get('catalog'))
        progress(len(items),len(items),'正在生成汇报图片')
        data['figure_version']=FIGURE_VERSION
        data['stage_overview_format']='compact-counts-and-changes-v2'
        data['figures']=performance.call('figures',render,data,staging,top_n=min(50,max(5,int(request.get('top_n',20)))),cache_root=folder if cached else None,shared_cache=output.parent/'.figure_cache',
            figure_progress=lambda done,total,title:progress(done,total,f'绘制基因与 Isotype PNG {done}/{total} · {title}',phase='figures'))
        if data.get('primer_scenarios'):
            from .scenario_analysis import render as render_scenarios
            data['figures'].extend(performance.call('suspected_figures',render_scenarios,data,staging,cache_root=folder if cached else None))
        verify(items,root)
        if any(version(path)!=expected for path,expected in checkpoint.receipts.items()):
            raise ValueError('报告来源在生成期间发生变化，请重新扫描')
        if not cached and any(version(path)!=signature for path,signature in receipts.items()):
            raise ValueError('阶段或 preprocessing 指标在统计期间变化，请重新生成')
        data['exports'].update({name:TABLE_PATHS[name] for name in sql_exports if name in TABLE_PATHS})
        from .report_html import write as write_html
        data['html_report']=performance.call('html',write_html,staging/'report.html',data,staging,run_id=request.get('run_id',''))
        # Keep the first Web response independent of primer × reference table size.
        overview={key:value for key,value in data.items() if key not in {'gene_counts','gene_coverage','filter_counts','family_counts','trim_counts','catalog','preprocessing_metrics'}}
        overview['catalog']={'assignments':{sample:[{'name':p['name'],'group':p.get('group','')} for p in values] for sample,values in (catalog or {}).get('assignments',{}).items()}}
        overview['catalog']['chain_assignments']={sample:{chain:[{'name':p['name'],'group':p.get('group','')} for p in values] for chain,values in chains.items()} for sample,chains in (catalog or {}).get('chain_assignments',{}).items()}
        write_json(staging/'summary.json',data)
        write_json(staging/'overview.json',overview)
        manifest=dict(version=VERSION,figure_version=FIGURE_VERSION,run_id=request.get('run_id'),input_root=str(root),items=items,
            matching_rule='5prime-full-IUPAC-exact; read-N-unmatched; no-RC/no-offset',
            primer_group_review='assigned unique rate below threshold; other groups compared on identical original-query denominator; suspected group analysis stored separately, original assignment unchanged',
            gene_candidates='single highest-scoring IgBLAST alignment per region; candidate analysis disabled; historical multi-calls preserved',
            primer=catalog,unit='chain detail observations; matching totals deduplicated by archived source and ordinal; legacy final-only rows remain chain-specific',
            files=[p.relative_to(staging).as_posix() for p in staging.rglob('*') if p.is_file() and p.suffix!='.sqlite' and not any(part.startswith('.') for part in p.relative_to(staging).parts)])
        manifest['file_versions']={name:version(staging/name) for name in manifest['files']}
        manifest['statistics_version']=version(staging/'details.sqlite')
        if (staging/'scenario_details.sqlite').is_file():
            manifest['scenario_statistics_version']=version(staging/'scenario_details.sqlite')
        write_json(staging/'manifest.json',manifest)
        performance.finish('succeeded')
        # Completion receipts are private; never list them in public downloads.
        os.replace(staging,output)
        (output/'.checkpoint.json').unlink(missing_ok=True)
        (output/'.statistics.json').unlink(missing_ok=True)
        progress(len(items),len(items),'汇总与图片生成完成')
        return dict(figures=len(data['figures']),samples=len(data['samples']))
    except BaseException:
        performance.finish('failed')
        raise
    finally:
        if staging.exists() and not checkpoint.retained(staging):
            if staging.is_symlink() or staging.resolve().parent!=output.parent.resolve():
                raise ValueError('Unsafe report staging directory')
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
