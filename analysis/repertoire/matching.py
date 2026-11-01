"""Sample-wise primer composition from records already deduplicated across chains."""
from collections import Counter, defaultdict

CATEGORIES = ('unique', 'multiple', 'unmatched')


def summarize_matching(rows):
    groups = defaultdict(Counter)
    for row in rows:
        groups[(row['sample'], row['unit'])][row['match_class']] += row['count']
    result = []
    for (sample, unit), counts in sorted(groups.items()):
        denominator = sum(counts[key] for key in CATEGORIES)
        for category in CATEGORIES:
            result.append(dict(sample=sample, unit=unit, match_class=category,
                count=counts[category], denominator=denominator,
                percent=counts[category]*100/denominator if denominator else None,
                unassessed_count=sum(counts.values())-denominator))
    return result
