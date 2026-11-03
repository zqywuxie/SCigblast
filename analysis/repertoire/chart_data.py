"""Public chart contract. Only already-computed aggregates leave the server."""
from __future__ import annotations

import csv

VERSION = 'public-report-charts-v2-context-units'
# Explicit allowlist: no paths, sequence IDs, primer sequences or private settings.
FIELDS = frozenset('group_id reference_catalog sample chain primer stage category match_class segment gene isotype level metric aa_field count unique_count ambiguous_count denominator percent matched_reads total_reads match_percent mapped_reads mapping_percent retained_reads unverified_reads label unit previous_count reduction retention_percent status assigned_group tested_group original_unique_percent unique_percent multiple_percent unmatched_percent threshold_percent value mean_shm_percent shared_cdr3_percent total_receptor_rna total_unique_cdr3s'.split())
TABLES = {
    'dual_matching_summary', 'matching_total_summary', 'primer_group_comparisons',
    'isotype_summary', 'sample_gene_usage', 'primer_gene_usage', 'primer_mapping_summary',
    'reference_gene_observation', 'primer_reference_gene_observation',
    'unobserved_reference_genes', 'primer_unobserved_reference_genes',
    'stage_volume_summary', 'sample_output_summary', 'diversity_summary',
    'bcell_metrics', 'isotype_distribution',
}
NUMERIC = frozenset('reference_catalog count unique_count ambiguous_count denominator percent matched_reads total_reads match_percent mapped_reads mapping_percent retained_reads unverified_reads previous_count reduction retention_percent original_unique_percent unique_percent multiple_percent unmatched_percent threshold_percent value mean_shm_percent shared_cdr3_percent total_receptor_rna total_unique_cdr3s'.split())
FIELDS=FIELDS|{'multiple_count','unmatched_count'}
FIELDS=FIELDS|{'scenario_id','base_group_id','primer_group'}
TABLES=TABLES|{'scenario_matching_summary','scenario_primer_mapping_summary','scenario_primer_gene_usage','scenario_primer_reference_gene_observation'}
NUMERIC=NUMERIC|{'multiple_count','unmatched_count'}


def supports(figure,tables):
    if figure.get('scenario_id'):
        source='scenario_primer_mapping_summary' if figure['kind'] in {'primers','primer_mapping'} else 'scenario_primer_gene_usage'
        required={'scenario_id','group_id','sample','chain','primer','percent','metric','segment','gene','unique_count','denominator'} if source.endswith('gene_usage') else {'scenario_id','sample','chain','stage','primer','matched_reads','total_reads','match_percent','mapping_percent'}
        return source in tables and required.issubset(tables[source])
    gene={'group_id','sample','chain','primer','segment','gene','unique_count','metric','percent','denominator'}
    if figure.get('metric')=='unique_cdr3':gene=gene|{'aa_field'}
    required={'matching':[('dual_matching_summary',{'sample','stage','match_class','count','percent','denominator'}),('matching_total_summary',{'sample','match_class','count','percent','denominator'})],
        'isotype':[('isotype_summary',{'sample','group_id','isotype','metric','level','count','percent','denominator','aa_field'})],
        'sample_genes':[('sample_gene_usage',gene)],'primer_genes':[('primer_gene_usage',gene)],'composition':[('primer_gene_usage',gene)],
        'primers':[('primer_mapping_summary',{'sample','chain','stage','primer','matched_reads','total_reads','match_percent'})],
        'primer_mapping':[('primer_mapping_summary',{'sample','chain','stage','primer','matched_reads','mapped_reads','mapping_percent'})],
        'sample_outputs':[('sample_output_summary',{'sample','chain','total_reads','total_receptor_rna','total_unique_cdr3s'})],
        'stage_overview':[('stage_volume_summary',{'sample','chain','stage','count'})],
        'diversity':[('diversity_summary',{'sample','chain','group_id','metric','value'})],
        'diversity_comparison':[('diversity_summary',{'sample','chain','group_id','metric','value'})],
        'bcell_metrics':[('bcell_metrics',{'sample','group_id','isotype','mean_shm_percent','shared_cdr3_percent'})],
        'bcell_metrics_comparison':[('bcell_metrics',{'sample','group_id','isotype','mean_shm_percent','shared_cdr3_percent'})],
        'isotype_distribution':[('isotype_distribution',{'sample','group_id','isotype','metric','count','percent','denominator'})]}
    return any(name in tables and columns.issubset(tables[name]) for name,columns in required.get(figure['kind'],[]))


def prepare(data, resolve):
    """Validate all data files up front, then let the HTML writer stream rows."""
    tables=[]
    for key in sorted(TABLES):
        name=data.get('exports',{}).get(key)
        if not name:continue
        path=resolve(name)
        with path.open(encoding='utf-8-sig',newline='') as handle:
            fields=[f for f in csv.DictReader(handle).fieldnames or [] if f in FIELDS]
        tables.append(dict(id=key,path=path,columns=fields))
    return tables


def rows(source):
    with source['path'].open(encoding='utf-8-sig',newline='') as handle:
        for row in csv.DictReader(handle):
            if row.get('aa_field')=='junction_aa':continue
            if source['id']=='isotype_summary' and row.get('level')=='subclass':continue
            if source['id']=='isotype_summary':
                from .isotype import count_unit
                row['unit']=count_unit(row.get('metric'),row.get('subset')=='postprocessed')
            result=[]
            for field in source['columns']:
                value=row.get(field,'')
                if field in NUMERIC:
                    try:
                        value=float(value) if value!='' else None
                        if value is not None:
                            import math
                            value=(int(value) if value.is_integer() else value) if math.isfinite(value) else None
                    except (ValueError,TypeError):value=None
                result.append(value)
            yield result


def public_metadata(data):
    from .palette import MATCH_COLORS,ISOTYPE_COLORS,GENE_SEGMENT_COLORS,STAGE_COLORS
    groups=data.get('usage',{}).get('groups',[])
    return dict(schema=VERSION,statistics_version=data.get('version',''),
        figure_version=data.get('figure_version',''),
        groups=[dict({k:g.get(k) for k in ('group_id','sample','chain','primers','aa_fields','reference_scope')},
                     reference_id=g.get('reference_id',''),
                     reference_segments={segment:dict(available=bool(info.get('available')),sha256=info.get('sha256'))
                         for segment,info in g.get('reference',{}).items() if segment in {'V','J','C'}})
                for g in groups],
        composition_weight='copy' if data.get('usage',{}).get('rules',{}).get('composition_source')=='published_airr_preprocessing' else 'reads',
        # Composition provenance is exposed separately without internal paths.
        colors=dict(matching=MATCH_COLORS,isotype=ISOTYPE_COLORS,segment=GENE_SEGMENT_COLORS,stage=STAGE_COLORS),
        primer_scenarios=[{k:p.get(k) for k in ('scenario_id','base_group_id','sample','chain','primer_group','status','analysis_basis','assigned_copies','unresolved_copies')} for p in data.get('primer_scenarios',[])],
        primer_catalog=[dict(sample=g['sample'],chain=g['chain'],primer=p) for g in groups for p in g.get('primers',[])]+[
            dict(sample=g['sample'],chain=g['chain'],primer=p,scenario_id=g['scenario_id']) for g in data.get('primer_scenarios',[]) for p in g['primers']])


def virtual_figures(tables,existing):
    """Interactive coverage is independent of which primers had PNGs rendered."""
    from collections import Counter
    import hashlib
    fields=('kind','sample','chain','group_id','primer','segment','metric','aa_field','page')
    present={tuple(f.get(k,'') for k in fields) for f in existing}
    for source in tables:
        if source['id']=='sample_output_summary' and supports({'kind':'sample_outputs'},{source['id']:set(source['columns'])}):
            count=sum(1 for _ in rows(source));pages=(count+11)//12
            for page in range(1,pages+1):
                if any(f['kind']=='sample_outputs' and not f.get('chain') and f.get('page',1)==page for f in existing):continue
                stem='all_samples__sample_output_comparison'+(f'_{page}' if pages>1 else '')
                yield dict(id='chart-outputs-all-'+str(page),kind='sample_outputs',sample='',chain='',group_id='',primer='',segment='',metric='',aa_field='',page=page,view='outputs',interactive=True,stage='',preparation='',title='Sample output comparison · All samples',filename=stem+'.png')
        if source['id'] not in {'primer_gene_usage','sample_gene_usage','scenario_primer_gene_usage'}:continue
        counts=Counter();scenario_meta={}
        kind='sample_genes' if source['id']=='sample_gene_usage' else 'primer_genes'
        for values in rows(source):
            row=dict(zip(source['columns'],values))
            if row.get('scenario_id'):scenario_meta[row['group_id']]={k:row[k] for k in ('scenario_id','base_group_id','primer_group')}
            if (row.get('unique_count') or 0)<=0:continue
            if row.get('metric')=='unique_cdr3' and row.get('aa_field')!='cdr3_aa':continue
            counts[tuple(row.get(k,'') for k in fields[1:-1])]+=1
        for context,count in sorted(counts.items()):
            for page in range(1,(count+24)//25+1):
                identity=(kind,*context,page)
                if identity in present:continue
                f=dict(zip(fields,identity))
                digest=hashlib.sha256(repr(identity).encode()).hexdigest()[:20]
                f.update(id='chart-'+digest,title=f['segment']+' gene usage',filename=f['sample']+'__'+f['chain']+'__'+(f['primer']+'__' if f['primer'] else '')+f['segment']+'__'+f['metric']+'__'+str(page)+'.png',view='primer' if kind=='primer_genes' else 'sample',interactive=True,stage='',preparation='')
                f.update(scenario_meta.get(f['group_id'],{}))
                yield f
