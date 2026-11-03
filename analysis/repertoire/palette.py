"""Shared semantic colors for report PNGs and experiment-check workbooks."""
from __future__ import annotations

import hashlib
import re

MATCH_COLORS={'unique':'#3979B8','multiple':'#E3A34D','unmatched':'#B3BBC5'}
STAGE_COLORS={'raw':'#567FB5','raw_r1':'#567FB5','raw_r2':'#567FB5','fastp':'#419A8B','fastp_r1':'#419A8B','fastp_r2':'#419A8B','clean':'#419A8B','merge_input':'#419A8B','merged':'#8A78B5',
              'mapping_input':'#AD925D','mapping_output':'#CE8351'}
STAGE_INK={'raw':'#345D8B','raw_r1':'#345D8B','raw_r2':'#345D8B','fastp':'#276F64','fastp_r1':'#276F64','fastp_r2':'#276F64','clean':'#276F64','merged':'#66528B',
           'mapping_input':'#79653E','mapping_output':'#98582F'}
ISOTYPE_COLORS={'IgM':'#7A88C4','IgD':'#62A398','IgA':'#D57856','IgG':'#4477AA','IgE':'#D4AA49'}
GENE_SEGMENT_COLORS={'V':'#C24D51','J':'#4C72B0','C':'#7C6C96'}
CATEGORY_COLORS=('#3979B8','#4E9B87','#DB8B47','#8A76B4','#C96773','#72A7BC','#B79B50','#879791')
_IGG=('#4477AA','#7EA5CC','#2C537F','#B2C9DD','#5B86AA','#94B5CE','#386387','#C6D8E5')


def mix(color,white):
    rgb=[int(color[i:i+2],16) for i in (1,3,5)]
    return '#'+''.join(f'{round(v*(1-white)+255*white):02X}' for v in rgb)


def text_on(color):
    def luminance(value):
        rgb=[int(value[i:i+2],16)/255 for i in (1,3,5)]
        linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
        return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
    light=luminance(color);dark=luminance('#233343')
    if (light+.05)/(dark+.05)>=4.5:return '#233343'
    if 1.05/(light+.05)>=4.5:return 'white'
    return '#111111' if (light+.05)/(luminance('#111111')+.05)>=4.5 else '#000000'


def isotype_color(label):
    main=label[:3]
    if label==main:return ISOTYPE_COLORS.get(main,CATEGORY_COLORS[0])
    if main=='IgG':
        number=re.match(r'(\d+)',label[3:])
        index=(int(number[1])-1) if number else 0
        suffix=re.search(r'-(\d+)$',label)
        if suffix:index+=int(suffix[1])-1
        return _IGG[index%len(_IGG)]
    number=re.match(r'(\d+)',label[3:])
    return mix(ISOTYPE_COLORS.get(main,CATEGORY_COLORS[0]),min(.5,.15*(int(number[1])-1))) if number else ISOTYPE_COLORS.get(main,CATEGORY_COLORS[0])


def constant_color(gene):
    gene=gene.split('*')[0].upper()
    match=re.fullmatch(r'IGH([MDAGE])(\d+(?:-\d+)?)?',gene)
    if match:return isotype_color('Ig'+match[1]+(match[2] or ''))
    for prefix,color in [('IGKC','#419A8B'),('IGLC','#8A76B4'),('TRAC','#CF766B'),
                         ('TRBC','#4F83BC'),('TRDC','#BF9745'),('TRGC','#8B6CA8')]:
        if gene.startswith(prefix):
            number=re.search(r'(\d+)$',gene)
            return mix(color,min(.45,.25*(int(number[1])-1))) if number else color
    return CATEGORY_COLORS[int(hashlib.sha256(gene.encode()).hexdigest()[:8],16)%len(CATEGORY_COLORS)]


def workbook_accent(name):
    if name=='stage_volume_summary':return '#2F7B72'
    if name in {'matching_total_summary','matching_summary'}:return '#326C9D'
    if name in {'primer_counts','primer_gene_usage'}:return '#477E70'
    if name in {'isotype_summary','preprocessing_metrics'}:return '#735D92'
    if name in {'unobserved_reference_genes','primer_unobserved_reference_genes'}:return '#667685'
    return '#3E5D7A'
