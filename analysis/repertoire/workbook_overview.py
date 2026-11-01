"""Sample information and native charts backed by a bounded preview table."""
from __future__ import annotations

from collections import defaultdict
from .palette import MATCH_COLORS,STAGE_COLORS,mix


def build_overview(book,exports,*,catalog=None):
    from math import ceil
    from unicodedata import east_asian_width
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.chart import BarChart,Reference
    from openpyxl.chart.text import RichText
    from openpyxl.drawing.text import RichTextProperties,Paragraph,ParagraphProperties,CharacterProperties
    from openpyxl.styles import Alignment,Border,Font,PatternFill,Side
    from openpyxl.worksheet.worksheet import Worksheet

    sheet=book.create_sheet('总览');data=book.create_sheet('图表数据');data.sheet_state='hidden'
    sheet.sheet_view.showGridLines=False;sheet.sheet_view.zoomScale=85
    sheet.sheet_properties.tabColor='3C718F'
    sheet.freeze_panes='C5'
    for col,width in (('A',16),('B',48),('C',12),('D',80)):
        sheet.column_dimensions[col].width=width
    # Write-only sheets serialize a row as soon as it is appended.
    # Set heights beforehand so KPI text is not clipped by the default height.
    for row,height in ((1,24),(2,16),(3,10),(4,28)):
        sheet.row_dimensions[row].height=height
    sheet.merged_cells.add('A1:D2')
    title=[]
    for i in range(4):
        c=WriteOnlyCell(sheet,'样本信息' if i==0 else None)
        c.fill=PatternFill('solid',fgColor='243E54');c.font=Font(name='Calibri',size=20,bold=True,color='FFFFFF')
        c.alignment=Alignment(vertical='center');title.append(c)
    sheet.append(title);sheet.append([]);sheet.append([])
    samples=sorted({(r['sample'],r['chain']) for r in exports.get('sample_summary',[])})
    assignments=(catalog or {}).get('assignments',{})
    categories={'IGH':'Heavy','IGK':'Light','IGL':'Light',**{c:'TCR' for c in ('TRA','TRB','TRD','TRG')}}
    headers=[]
    for label in ('类别','完整样本名','链','引物组 / 对应方式'):
        c=WriteOnlyCell(sheet,label);c.fill=PatternFill('solid',fgColor='243E54')
        c.font=Font(name='Calibri',size=11,color='FFFFFF',bold=True)
        c.alignment=Alignment(horizontal='center',vertical='center');headers.append(c)
    sheet.append(headers)
    previous=None;block=-1
    for row_number,(sample,chain) in enumerate(samples,5):
        primers=assignments.get(sample,[])
        groups=sorted({str(p['group']).strip() for p in primers if p.get('group') and str(p['group']).strip()})
        group='、'.join(groups) if groups else '未提供组名' if primers else '未配置'
        text=[categories.get(chain,'—'),sample,chain,group]
        # Size wrapped sample/group text before streaming the row to the file.
        units=lambda value:sum(2 if east_asian_width(c) in {'W','F'} else 1 for c in str(value))
        sheet.row_dimensions[row_number].height=max(24,15*max(ceil(units(sample)/45),ceil(units(group)/75))+8)
        changed=sample!=previous
        if changed:block+=1;previous=sample
        cells=[]
        for column,value in enumerate(text):
            if isinstance(value,str) and value.startswith(('=','+','-','@')):value="'"+value
            c=WriteOnlyCell(sheet,value)
            c.font=Font(name='Calibri',size=11,color='263C4E',bold=column==1)
            c.fill=PatternFill('solid',fgColor='EDF3F8' if block%2 else 'FFFFFF')
            if column==0:
                color={'Heavy':'#D57856','Light':'#419A8B','TCR':'#567FB5'}.get(value,'#667685')
                c.fill=PatternFill('solid',fgColor=mix(color,.90).removeprefix('#'))
                c.font=Font(name='Calibri',size=11,color={'Heavy':'9B4F35','Light':'286D61','TCR':'385B88'}.get(value,'53616D'),bold=True)
            c.alignment=Alignment(horizontal='center' if column==2 else 'left',vertical='center',wrap_text=True)
            if changed:c.border=Border(top=Side(style='thin',color='A9BAC8'))
            cells.append(c)
        sheet.append(cells)
    if samples:sheet.auto_filter.ref=f'A4:D{4+len(samples)}'
    grouped=defaultdict(lambda:defaultdict(set))
    for row in exports.get('stage_volume_summary',[]):
        if not row['chain'] and row['stage'] in {'raw','fastp','merged'}:grouped[row['sample']][row['stage']].add(row['count'])
    names=sorted(grouped);preview=names[:12]
    hint='Raw reads include R1 + R2; each Merged read represents one assembled pair.'
    if len(names)>12:hint+='  图表预览前12个样本，所有样本均保留在核查表。'
    note=WriteOnlyCell(sheet,hint);note.font=Font(name='Calibri',size=10,color='667888');note.alignment=Alignment(wrap_text=True,vertical='center')
    note_row=5+len(samples);chart_row=note_row+2
    sheet.row_dimensions[note_row].height=30
    sheet.append([note]);sheet.merged_cells.add(f'A{note_row}:D{note_row}')
    sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=Worksheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0;sheet.print_options.horizontalCentered=True
    sheet.print_area=f'A1:D{chart_row+23}'

    def style(chart,colors):
        chart.width=14;chart.height=10;chart.legend.position='b'
        chart.visible_cells_only=False;chart.display_blanks='gap'
        chart.x_axis.delete=False;chart.y_axis.delete=False
        chart.x_axis.axPos='b';chart.y_axis.axPos='l'
        chart.x_axis.tickLblPos='nextTo';chart.y_axis.tickLblPos='nextTo'
        chart.x_axis.tickLblSkip=1
        chart.x_axis.txPr=RichText(bodyPr=RichTextProperties(rot=-2700000),p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties(sz=900)))])
        chart.legend.overlay=False;chart.title.overlay=False
        chart.y_axis.majorGridlines=None
        for series,color in zip(chart.series,colors):
            series.graphicalProperties.solidFill=color
            series.graphicalProperties.line.noFill=True
        return chart

    data.append(['Sample','Raw reads (R1+R2)','Clean reads (R1+R2)','Merged reads'])
    for sample in preview:
        values=[]
        for stage in ('raw','fastp','merged'):
            observed=grouped[sample][stage];values.append(next(iter(observed)) if len(observed)==1 else None)
        data.append([sample,*values])
    if preview:
        chart=BarChart();chart.type='col';chart.grouping='clustered';chart.title='Read counts';chart.y_axis.numFmt='#,##0'
        chart.add_data(Reference(data,min_col=2,max_col=4,min_row=1,max_row=len(preview)+1),titles_from_data=True)
        chart.set_categories(Reference(data,min_col=1,min_row=2,max_row=len(preview)+1))
        sheet.add_chart(style(chart,[STAGE_COLORS[s].removeprefix('#') for s in ('raw','fastp','merged')]),f'A{chart_row}')
    assessed={}
    for row in exports.get('matching_total_summary',[]):
        assessed.setdefault((row['sample'],row['unit']),{})[row['match_class']]=row['percent']
    keys=sorted(assessed)[:12]
    # Never mix raw-read matching with merged-read matching in a single sample bar.
    repeats={sample:sum(s==sample for s,_ in assessed) for sample,_ in keys}
    offset=len(preview)+4
    for _ in range(2):data.append([])
    data.append(['Sample','Unique','Multiple','Unmatched'])
    for sample,unit in keys:
        label=sample+(' · '+unit if repeats[sample]>1 else '')
        data.append([label,*[assessed[(sample,unit)].get(k) for k in ('unique','multiple','unmatched')]])
    if keys:
        chart=BarChart();chart.type='col';chart.grouping='stacked';chart.overlap=100;chart.title='Primer matching'
        chart.y_axis.scaling.min=0;chart.y_axis.scaling.max=100;chart.y_axis.numFmt='0"%"'
        chart.add_data(Reference(data,min_col=2,max_col=4,min_row=offset,max_row=offset+len(keys)),titles_from_data=True)
        chart.set_categories(Reference(data,min_col=1,min_row=offset+1,max_row=offset+len(keys)))
        sheet.add_chart(style(chart,[MATCH_COLORS[s].removeprefix('#') for s in ('unique','multiple','unmatched')]),f'D{chart_row}')
    if len(assessed)>12:
        for _ in range(22):sheet.append([])
        sheet.append(['Primer matching图预览前12组，完整数据见引物匹配总览。'])
    return dict(samples=len({sample for sample,_ in samples}),sample_rows=len(samples),
                charts=int(bool(preview))+int(bool(keys)),preview_limit=12)
