"""Per-gene distinct CDR3 usage, without bringing read-level keys into RAM."""
from .performance import measured


@measured('gene_cdr3_summary')
def enrich(connection):
    connection.execute('PRAGMA temp_store=FILE')
    postprocessed = bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name='composition_records'").fetchone())
    records = 'composition_records' if postprocessed else 'usage_records'
    calls = 'composition_calls' if postprocessed else 'usage_calls'
    sample = ' AND r.sample_record=1' if postprocessed else ''
    primer = ' AND r.sample_record=0' if postprocessed else ''
    # AA fields are separate namespaces: a junction-only legacy input must never
    # be silently presented as CDR3aa. Within each gene, exact AA keys deduplicate
    # across reads/lanes. Across genes, memberships remain distinct (as in the
    # Isotype Unique CDR3 composition). Only classified gene memberships enter
    # the denominator; missing/ambiguous AA counts remain separate diagnostics.
    connection.executescript(f'''
      CREATE TABLE usage_aa_counts AS
        SELECT r.group_id,'' AS primer,c.segment,r.aa_field,c.gene,c.assignment,
          count(DISTINCT r.aa_value) AS count
        FROM {records} r JOIN {calls} c ON c.record_id=r.id
        WHERE r.eligible=1 AND r.aa_valid=1 AND r.aa_field='cdr3_aa'{sample}
        GROUP BY r.group_id,c.segment,r.aa_field,c.gene,c.assignment
        UNION ALL
        SELECT r.group_id,r.primer,c.segment,r.aa_field,c.gene,c.assignment,
          count(DISTINCT r.aa_value) AS count
        FROM {records} r JOIN {calls} c ON c.record_id=r.id
        WHERE r.eligible=1 AND r.aa_valid=1 AND r.aa_field='cdr3_aa' AND r.primer<>''{primer}
        GROUP BY r.group_id,r.primer,c.segment,r.aa_field,c.gene,c.assignment;
      CREATE INDEX usage_aa_selection ON usage_aa_counts(group_id,primer,segment,aa_field,gene,assignment);
      CREATE TABLE usage_aa_other_counts AS
        SELECT r.group_id,'' AS primer,c.segment,r.aa_field,c.assignment,
          count(DISTINCT r.aa_value) AS count
        FROM {records} r JOIN {calls} c ON c.record_id=r.id
        WHERE r.eligible=1 AND r.aa_valid=1 AND r.aa_field='cdr3_aa' AND c.assignment<>'unique'{sample}
        GROUP BY r.group_id,c.segment,r.aa_field,c.assignment
        UNION ALL
        SELECT r.group_id,r.primer,c.segment,r.aa_field,c.assignment,
          count(DISTINCT r.aa_value) AS count
        FROM {records} r JOIN {calls} c ON c.record_id=r.id
        WHERE r.eligible=1 AND r.aa_valid=1 AND r.aa_field='cdr3_aa' AND r.primer<>'' AND c.assignment<>'unique'{primer}
        GROUP BY r.group_id,r.primer,c.segment,r.aa_field,c.assignment;
      CREATE TABLE usage_aa_denominators AS
        SELECT group_id,primer,segment,aa_field,sum(count) AS total
        FROM usage_aa_counts WHERE assignment='unique'
        GROUP BY group_id,primer,segment,aa_field;
      CREATE INDEX usage_aa_denominator_selection ON usage_aa_denominators(group_id,primer,segment,aa_field);
      DROP INDEX gene_usage_selection;
      ALTER TABLE gene_usage RENAME TO gene_expression_usage;
      CREATE TABLE gene_usage AS
        SELECT g.*,'expression' AS metric,'' AS aa_field FROM gene_expression_usage g
        UNION ALL
        SELECT g.group_id,g.sample,g.chain,g.reference_id,g.reference_scope,g.primer,g.segment,g.gene,
          coalesce(u.count,0) AS unique_count,coalesce(a.count,0) AS ambiguous_count,
          coalesce(d.total,0) AS denominator,
          CASE WHEN d.total>0 THEN 100.0*coalesce(u.count,0)/d.total END AS percent,
          g.reference_catalog,g.status,'unique_cdr3' AS metric,f.aa_field
        FROM gene_expression_usage g
        JOIN (SELECT DISTINCT group_id,aa_field FROM {records} WHERE eligible=1 AND aa_field='cdr3_aa') f USING(group_id)
        LEFT JOIN usage_aa_counts u ON u.group_id=g.group_id AND u.primer=g.primer
          AND u.segment=g.segment AND u.gene=g.gene AND u.aa_field=f.aa_field AND u.assignment='unique'
        LEFT JOIN usage_aa_counts a ON a.group_id=g.group_id AND a.primer=g.primer
          AND a.segment=g.segment AND a.gene=g.gene AND a.aa_field=f.aa_field AND a.assignment='ambiguous'
        LEFT JOIN usage_aa_denominators d ON d.group_id=g.group_id AND d.primer=g.primer
          AND d.segment=g.segment AND d.aa_field=f.aa_field;
      DROP TABLE gene_expression_usage;
      CREATE INDEX gene_usage_selection ON gene_usage(group_id,primer,segment,metric,aa_field,unique_count DESC,gene);
    ''')


def rebase_classified(connection):
    """Recalculate denominators from existing counts, without touching read lineage."""
    connection.execute('''CREATE TEMP TABLE classified_expression_totals AS
        SELECT group_id,primer,segment,sum(unique_count) AS total
        FROM gene_usage WHERE metric='expression' GROUP BY group_id,primer,segment''')
    connection.execute('''CREATE INDEX temp.classified_expression_key ON
        classified_expression_totals(group_id,primer,segment)''')
    connection.execute('''UPDATE gene_usage SET denominator=coalesce((
        SELECT total FROM classified_expression_totals d WHERE d.group_id=gene_usage.group_id
        AND d.primer=gene_usage.primer AND d.segment=gene_usage.segment),0) WHERE metric='expression' ''')
    connection.execute('DELETE FROM usage_aa_denominators')
    connection.execute('''INSERT INTO usage_aa_denominators
        SELECT group_id,primer,segment,aa_field,sum(count) FROM usage_aa_counts
        WHERE assignment='unique' GROUP BY group_id,primer,segment,aa_field''')
    connection.execute('''UPDATE gene_usage SET denominator=coalesce((
        SELECT total FROM usage_aa_denominators d WHERE d.group_id=gene_usage.group_id
        AND d.primer=gene_usage.primer AND d.segment=gene_usage.segment
        AND d.aa_field=gene_usage.aa_field),0) WHERE metric='unique_cdr3' ''')
    connection.execute('UPDATE gene_usage SET percent=CASE WHEN denominator>0 THEN 100.0*unique_count/denominator END')
    connection.execute('DROP TABLE classified_expression_totals')


def label(metric, aa_field=''):
    if metric=='expression':return '% Expression'
    return '% Unique CDR3' if aa_field=='cdr3_aa' else '% Unique Junction' if aa_field=='junction_aa' else 'Unique AA'
