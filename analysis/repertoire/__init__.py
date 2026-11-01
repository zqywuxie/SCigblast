"""Versioned QC and primer analyses for existing and newly generated AIRR results."""
VERSION = 'repertoire-evidence-v11-vjc-isotype-workspace'
FIGURE_VERSION = 'repertoire-figures-v18-semantic-palette'

TABLE_PATHS = {
    'stage_volume_summary': '04_data_changes/stage_volume_summary.csv',
    'stage_summary': '04_data_changes/stage_summary.csv',
    'matching_summary': '05_details/matching_details.csv',
    'matching_total_summary': '01_overview/primer_matching_summary.csv',
    'isotype_summary': '01_overview/isotype_summary.csv',
    'sample_gene_usage': '02_sample_genes/sample_gene_usage.csv',
    'reference_gene_observation': '02_sample_genes/reference_gene_observation.csv',
    'unobserved_reference_genes': '02_sample_genes/unobserved_reference_genes.csv',
    'primer_unobserved_reference_genes': '03_primer_genes/unobserved_reference_genes.csv',
    'primer_gene_usage': '03_primer_genes/primer_gene_usage.csv',
    'primer_counts': '03_primer_genes/primer_counts.csv',
    'sequence_family_matching': '05_details/sequence_family_matching.csv',
    'primer_preparation_summary': '05_details/primer_preparation_summary.csv',
    'primer_gene_counts': '05_details/primer_gene_counts.csv',
    'gene_coverage': '05_details/gene_coverage.csv',
    'filter_summary': '05_details/filter_summary.csv',
    'sample_summary': 'sample_summary.csv',
    'preprocessing_metrics': '05_details/preprocessing_metrics.csv',
}

FIGURE_CATEGORIES = {
    'stages': '04_data_changes',
    'stage_overview': '04_data_changes',
    'matching': '01_overview',
    'isotype': '01_overview',
    'primers': '03_primer_genes',
    'genes': '02_sample_genes',
    'sample_genes': '02_sample_genes',
    'primer_genes': '03_primer_genes',
    'heatmap': '03_primer_genes',
    'composition': '03_primer_genes',
}
