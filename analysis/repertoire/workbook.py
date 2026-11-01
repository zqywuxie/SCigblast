"""Stream one experiment-check workbook from the published statistics."""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path

from .usage import connect,USAGE_COLUMNS
from .palette import MATCH_COLORS,STAGE_COLORS,GENE_SEGMENT_COLORS,mix,workbook_accent

LABELS={
 'sample_summary':('样本与分析范围','输入文件、样本、链与记录口径'),
 'stage_volume_summary':('数据量变化','原始输入、中间阶段数量和变化；单位分别列出'),
 'matching_total_summary':('引物匹配总览','按样本统计唯一、多重、未匹配及未评估记录'),
 'primer_counts':('引物计数','完整引物名称、统计输入和筛选后保留计数'),
 'isotype_summary':('Isotype组成','记录丰度和 Unique CDR3；主类与亚类分列'),
 'sample_gene_usage':('样本已匹配基因','只含唯一归属数量大于0的 V/J/C 基因'),
 'unobserved_reference_genes':('样本未观察基因','当次或明确选定比较参考中未观察到的基因'),
 'primer_gene_usage':('引物已匹配基因','逐引物已有明确注释的 V/J/C 组成'),
 'primer_unobserved_reference_genes':('引物未观察基因','逐引物未观察的参考基因；分母0须结合引物计数判断'),
 'preprocessing_metrics':('原流程样本指标','原流程 AIRR preprocessing 的 Datapoint；保留原选择范围与权重口径'),
 'filter_summary':('注释与筛选状态','各筛选条件与排除原因'),
 'stage_summary':('阶段原始指标','原阶段报告的技术明细'),
 'matching_summary':('匹配分类明细','样本链级匹配细节'),
 'primer_gene_counts':('引物注释明细','匹配与处理状态细节'),
 'gene_coverage':('参考覆盖明细','历史兼容的参考观察细节'),
 'sequence_family_matching':('序列家族明细','共享序列及其匹配分类'),
 'primer_preparation_summary':('引物处理明细','剪切和保留原序列分类'),
}
HEADERS={'sample':'样本','sample_id':'样本','chain':'链','primer':'引物全名','segment':'基因区','gene':'基因',
 'unique_count':'唯一归属记录数','ambiguous_count':'候选记录数','denominator':'分母','percent':'比例（%）',
 'reference_id':'参考版本','reference_scope':'参考口径','reference_catalog':'属于参考清单','status':'状态',
 'match_class':'匹配类别','count':'数量','unit':'单位','stage':'阶段','label':'阶段名称','previous_count':'前阶段数量',
 'reduction':'减少量','retention_percent':'保留或合并率（%）','source':'来源','level':'Isotype粒度','metric':'指标',
 'isotype':'Isotype','aa_field':'AA字段'}
ORDER=['sample_summary','stage_volume_summary','matching_total_summary','primer_counts','isotype_summary',
       'sample_gene_usage','unobserved_reference_genes','primer_gene_usage','primer_unobserved_reference_genes',
       'preprocessing_metrics']
VERSION='experiment-check-workbook-v11-semantic-palette'

FIELDS={
 'sample_summary':[('sample','样本'),('chain','链'),('pair','数据配对'),('observations','统计数量'),('unit','计数单位')],
 'stage_volume_summary':[('sample','样本'),('chain','链'),('label','阶段'),('count','数量'),('unit','计数单位'),('reduction','减少量'),('retention_percent','保留或合并率（%）')],
 'primer_counts':[('sample','样本'),('chain','链'),('primer','引物全名'),('subset','数据范围'),('count','数量')],
 'isotype_summary':[('sample','样本'),('level','分类层级'),('isotype','Isotype'),('metric','统计指标'),('count','Count'),('percent','Percentage (%)')],
 'sample_gene_usage':[('sample','样本'),('gene','基因'),('unique_count','Reads'),('percent','% Expression'),('chain','链'),('segment','基因区')],
 'primer_gene_usage':[('sample','样本'),('primer','引物全名'),('gene','基因'),('unique_count','Reads'),('percent','% Expression'),('chain','链'),('segment','基因区')],
 'unobserved_reference_genes':[('sample','样本'),('gene','基因'),('_hint','核查提示'),('chain','链'),('segment','基因区')],
 'primer_unobserved_reference_genes':[('sample','样本'),('primer','引物全名'),('gene','基因'),('_hint','核查提示'),('chain','链'),('segment','基因区')],
}
UNITS={'queries':'reads','read ends':'reads','read pairs':'read pairs','AIRR rows':'reads'}
STAGES={'raw':'Raw reads (R1+R2)','fastp':'Clean reads (Fastp)','clean':'Header-cleaned reads',
        'merge_input':'Merge input (read pairs)','merged':'Merged reads','mapping_input':'IgBLAST input reads','mapping_output':'Retained reads'}


def simple_rows(name,records,*,batches=None):
    """Project lab-facing columns without modifying source statistics."""
    fields=FIELDS[name]
    for row in records:
        result={label:row.get(key) for key,label in fields}
        if name=='primer_counts':result['数据范围']={'all':'统计输入','retained':'筛选后保留'}.get(row.get('subset'),row.get('subset'))
        if '计数单位' in result:result['计数单位']=UNITS.get(row.get('unit'),row.get('unit'))
        if name=='stage_volume_summary':result['阶段']=STAGES.get(row.get('stage'),row.get('label'))
        if name=='isotype_summary':
            result['分类层级']={'main':'主类','subclass':'亚类'}.get(row.get('level'),row.get('level'))
            result['统计指标']='% Expression' if row.get('metric')=='abundance' else '% uCDR3' if row.get('aa_field')=='cdr3_aa' else '% uJunction' if row.get('aa_field')=='junction_aa' else 'Unique AA (unknown field)'
        if '_hint' in dict(fields):
            result['核查提示']='暂无有效唯一注释' if not row.get('denominator') else ''
        if row.get('group_id') and batches and (row.get('sample'),row.get('chain')) in batches:
            result['结果批次']=batches[(row['sample'],row['chain'])][row['group_id']]
        if row.get('reference_scope')=='comparison_current':result['核查提示']='；'.join(filter(None,[result.get('核查提示'),'与当前参考比较']))
        yield result


def matching_rows(records):
    grouped={}
    for row in records:
        key=(row['sample'],row['unit'])
        result=grouped.setdefault(key,{'样本':row['sample'],'计数单位':UNITS.get(row['unit'],row['unit']),
            '统计数量':row['denominator'],'唯一匹配率（%）':None,'多重匹配率（%）':None,'未匹配率（%）':None,'未评估数':row.get('unassessed_count',0)})
        label={'unique':'唯一匹配率（%）','multiple':'多重匹配率（%）','unmatched':'未匹配率（%）'}.get(row['match_class'])
        if label:result[label]=row['percent']
    yield from grouped.values()


def ordered_isotypes(records):
    """Keep each Isotype's metrics adjacent, ranked by Expression per context."""
    records=list(records)
    def context(row):
        return tuple(str(row.get(key) or '') for key in
                     ('sample','chain','group_id','reference_id','reference_scope','subset','level'))
    abundance={}
    for row in records:
        if row.get('metric')=='abundance':
            abundance[context(row)+(str(row.get('isotype') or ''),)]=row.get('count') or 0
    def key(row):
        scope=context(row);isotype=str(row.get('isotype') or '')
        level=({'main':0,'subclass':1}.get(scope[-1],2),scope[-1])
        return (*scope[:-1],level,-abundance.get(scope+(isotype,),-1),isotype,
                0 if row.get('metric')=='abundance' else 1,
                str(row.get('aa_field') or ''),str(row.get('metric') or ''))
    return sorted(records,key=key)


def preprocessing_rows(records):
    known={'umi_counts':('Expression','原流程计数',1),'uCDR3':('Unique CDR3','种',1),'Percent':('链占比','%',100),
        'D50':('D50','%',1),'Largest_clone_percent':('最大克隆比例','%',100),'Shannon_index':('Shannon','',1),
        'Pielou_evenness':('Pielou','',1),'Gini_index':('Gini','',1),'Simpson_index':('Simpson','',1),
        'Simpson_evenness':('Simpson均匀度','',1),'V_SHM_ratio':('V区SHM','%',100),'TT_SHM_ratio':('全长SHM','%',100)}
    for row in records:
        for key,value in row.items():
            chain,_,suffix=key.partition('_')
            if chain not in {'IGH','IGK','IGL','TRA','TRB','TRD','TRG'} or suffix not in known or value in ('',None):continue
            label,unit,multiplier=known[suffix]
            try:
                number=float(value)*multiplier
                if not __import__('math').isfinite(number):continue
            except (ValueError,TypeError):continue
            yield {'样本':row.get('sample'),'数据批次':row.get('batch'),'链':chain,'指标':label,'数值':number,'单位':unit}


def write_workbook(destination,exports,sql_exports,index,*,catalog=None):
    # The Web runtime already uses openpyxl; keep its bounded-memory exporter.
    import openpyxl
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Alignment,Border,Font,PatternFill,Side
    from openpyxl.formatting.rule import DataBarRule,CellIsRule
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter
    from .workbook_overview import build_overview
    book=openpyxl.Workbook(write_only=True)
    overview=build_overview(book,exports,catalog=catalog)
    navigation=book.create_sheet('核查目录')
    links=[]
    def cell(sheet,value,*,header=False,number=False,accent='#243E54'):
        if isinstance(value,(list,dict)):value=json.dumps(value,ensure_ascii=False)
        if isinstance(value,str) and value.startswith(('=','+','-','@')):value="'"+value
        c=WriteOnlyCell(sheet,value=value)
        if header:
            c.fill=PatternFill('solid',fgColor=accent.removeprefix('#'));c.font=Font(name='Calibri',size=11,color='FFFFFF',bold=True)
            c.alignment=Alignment(vertical='center',wrap_text=True)
        elif number:c.number_format='#,##0.####'
        return c
    def write(name,records,columns,*,technical=False):
        title,description=LABELS.get(name,(name[:28],name))
        if technical:title=('技术_'+title)[:28]
        accent=workbook_accent(name)
        review=not technical and name in {'sample_gene_usage','primer_gene_usage','unobserved_reference_genes','primer_unobserved_reference_genes'}
        priority=['sample','sample_id','chain','primer','segment','gene','unique_count','ambiguous_count','count','percent','denominator','status'] if review else []
        columns=[key for key in priority if key in columns]+[key for key in columns if key not in priority]
        optional={'链','基因区','核查结果','备注'} if review else set()
        displayed=[*[key for key in columns if key not in optional],
                   *[key for key in columns if key in optional],*(['核查结果','备注'] if review else [])]
        sheet=None;page=0;count=0;page_rows=0
        previous_sample=object();sample_block=-1
        sample_rule=Side(style='medium',color=mix(accent,.45).removeprefix('#'))
        def new_sheet():
            nonlocal page
            page+=1
            label=title+('_'+str(page) if page>1 else '')
            result=book.create_sheet(label)
            if technical:result.sheet_state='hidden'
            result.freeze_panes=('C2' if '引物全名' in columns else 'B2') if review else 'A2'
            result.sheet_view.showGridLines=False
            result.sheet_view.zoomScale=90
            result.sheet_properties.tabColor=accent.removeprefix('#') if not technical else 'BAC2C9'
            for i,key in enumerate(displayed,1):
                result.column_dimensions[get_column_letter(i)].width=50 if key in {'primer','引物全名'} else 27 if key in {'sample','sample_id','gene','reference_scope','status','样本','基因','指标','核查提示'} else 40 if key in {'note','source','备注'} else 17
                if key in {'id','group_id','sample_key'} or key in optional:
                    result.column_dimensions[get_column_letter(i)].hidden=True
            result.row_dimensions[1].height=34
            result.append([cell(result,(HEADERS.get(key,key)+'\n'+key) if technical and key in HEADERS else key,header=True,accent=accent) for key in displayed])
            return result
        def finish():
            sheet.auto_filter.ref=f'A1:{get_column_letter(len(displayed))}{page_rows+1}'
            if not technical and page_rows:
                for i,key in enumerate(displayed,1):
                    column=get_column_letter(i)
                    if '%' in key:
                        color=next((MATCH_COLORS[state] for token,state in [('唯一匹配','unique'),('多重匹配','multiple'),('未匹配','unmatched')] if token in key),accent)
                        sheet.conditional_formatting.add(f'{column}2:{column}{page_rows+1}',DataBarRule(start_type='num',start_value=0,end_type='num',end_value=100,color=mix(color,.42).removeprefix('#')))
                    if key=='核查结果':
                        validation=DataValidation(type='list',formula1='"未核查,已确认,需复查"',allow_blank=True)
                        validation.add(f'{column}2:{column}{page_rows+1}');sheet.data_validations.append(validation)
                        sheet.conditional_formatting.add(f'{column}2:{column}{page_rows+1}',CellIsRule(operator='equal',formula=['"需复查"'],fill=PatternFill('solid',fgColor='FFF0D6')))
            links.append((sheet.title,page_rows,description,technical))
        sheet=new_sheet()
        for row in records:
            if page_rows==1048575:
                finish();sheet=new_sheet();page_rows=0
                previous_sample=object();sample_block=-1
            sample=row.get('样本',row.get('sample',row.get('sample_id')))
            grouped=not technical and sample not in (None,'')
            sample_start=grouped and sample!=previous_sample
            if sample_start:
                sample_block+=1;previous_sample=sample
                sheet.row_dimensions[page_rows+2].height=26
            values=[]
            for key in displayed:
                value=row.get(key);c=cell(sheet,value,number=isinstance(value,(int,float)))
                if not technical:
                    c.font=Font(name='Calibri',size=11,color='263C4E')
                    c.alignment=Alignment(vertical='center',wrap_text=isinstance(value,str))
                    if grouped:
                        c.fill=PatternFill('solid',fgColor=mix(accent,.94).removeprefix('#') if sample_block%2 else 'FFFFFF')
                        if sample_start:c.border=Border(top=sample_rule)
                        if key in {'样本','sample','sample_id'}:
                            c.fill=PatternFill('solid',fgColor=mix(accent,.84 if sample_block%2 else .91).removeprefix('#'))
                            c.font=Font(name='Calibri',size=11,color=accent.removeprefix('#'),bold=True)
                    elif page_rows%2==0:c.fill=PatternFill('solid',fgColor='F4F7FA')
                    if key in {'核查结果','备注'}:c.fill=PatternFill('solid',fgColor='EEF2F5')
                    segment=row.get('基因区')
                    if (key=='基因区' or (review and key=='基因')) and segment in {'V','J','C'}:
                        c.fill=PatternFill('solid',fgColor=mix(GENE_SEGMENT_COLORS[segment],.90).removeprefix('#'))
                    if name=='stage_volume_summary' and key in {'阶段','数量'}:
                        color=next((STAGE_COLORS[stage] for stage,label in STAGES.items() if label==row.get('阶段')),accent)
                        c.fill=PatternFill('solid',fgColor=mix(color,.87).removeprefix('#'))
                    if isinstance(value,(int,float)):
                        c.number_format='0.00##"%"' if '%' in key else '#,##0' if float(value).is_integer() else '#,##0.####'
                values.append(c)
            sheet.append(values)
            count+=1;page_rows+=1
        finish()
    names=[k for k in ORDER if k in exports or k in sql_exports]+[k for k in [*exports,*sql_exports] if k not in ORDER]
    # CSV/legacy inputs may still carry tables that are not part of the workbook.
    names=[name for name in names if name not in {'ambiguous_gene_observation','reference_gene_observation'}]
    with closing(connect(index)) as connection:
        variants={}
        has_groups=bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_groups'").fetchone())
        group_sql='SELECT sample,chain,id FROM usage_groups ORDER BY sample,chain,id' if has_groups else 'SELECT DISTINCT sample,chain,group_id FROM gene_usage ORDER BY sample,chain,group_id'
        for sample,chain,gid in connection.execute(group_sql):
            variants.setdefault((sample,chain),{})[gid]=len(variants.get((sample,chain),{}))+1
        batches={key:value for key,value in variants.items() if len(value)>1}
        comparison_sql="SELECT 1 FROM usage_groups WHERE json_extract(reference_json,'$._scope')='comparison_current' LIMIT 1" if has_groups else "SELECT 1 FROM gene_usage WHERE reference_scope='comparison_current' LIMIT 1"
        comparison=bool(connection.execute(comparison_sql).fetchone())
        for name in names:
            if name in sql_exports:records=(dict(r) for r in connection.execute(sql_exports[name]));columns=list(USAGE_COLUMNS)
            else:records=exports[name];columns=list(records[0]) if records else ['sample','count']
            if name in FIELDS:
                if name=='isotype_summary':records=ordered_isotypes(records)
                columns=[label for _,label in FIELDS[name]]
                if name in {'unobserved_reference_genes','primer_unobserved_reference_genes'}:
                    scope="primer=''" if name=='unobserved_reference_genes' else "primer<>''"
                    if not connection.execute("SELECT 1 FROM gene_usage WHERE "+scope+" AND status='zero_observed' AND (denominator=0 OR reference_scope='comparison_current') LIMIT 1").fetchone():columns.remove('核查提示')
                if name in {'sample_gene_usage','primer_gene_usage','unobserved_reference_genes','primer_unobserved_reference_genes','isotype_summary'}:
                    if batches:columns.append('结果批次')
                    if comparison and '核查提示' not in columns:columns.append('核查提示')
                write(name,simple_rows(name,records,batches=batches),columns)
            elif name=='matching_total_summary':
                columns=['样本','计数单位','统计数量','唯一匹配率（%）','多重匹配率（%）','未匹配率（%）']
                if any(row.get('unassessed_count') for row in records):columns.append('未评估数')
                write(name,matching_rows(records),columns)
            elif name=='preprocessing_metrics':
                write(name,preprocessing_rows(records),['样本','数据批次','链','指标','数值','单位'])
                write(name,records,list(records[0]) if records else ['sample','count'],technical=True)
            else:
                write(name,records,columns,technical=True)
    navigation.column_dimensions['A'].width=28;navigation.column_dimensions['B'].width=15;navigation.column_dimensions['C'].width=90
    navigation.freeze_panes='A5';navigation.sheet_view.showGridLines=False
    navigation.sheet_properties.tabColor='3C718F'
    navigation.append(['实验核查工作簿'])
    navigation.append(['说明','', '基因表Reads为明确基因归属数量；未观察不表示基因在生物学上不存在。'])
    navigation.append(['明细','', '链、基因区、核查结果和备注列默认隐藏，可取消隐藏；完整技术字段保留在CSV，原流程指标沿用原有口径。'])
    navigation.append([cell(navigation,v,header=True) for v in ['Sheet（点击进入）','记录数','核查内容']])
    for title,count,description,technical in links:
        if technical:continue
        link=WriteOnlyCell(navigation,value=title);link.hyperlink="#'"+title.replace("'","''")+"'!A1";link.font=Font(color='325F8C',underline='single')
        navigation.append([link,count,description])
    book.save(Path(destination))
    return dict(version=VERSION,sheets=['总览','图表数据',*[title for title,_,_,_ in links]],visible_sheets=['总览',*[title for title,_,_,hidden in links if not hidden]],rows={title:count for title,count,_,_ in links},overview=overview)
