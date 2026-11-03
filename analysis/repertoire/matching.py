"""Sample-wise primer composition from records already deduplicated across chains."""
from collections import Counter, defaultdict
import math

CATEGORIES = ('unique', 'multiple', 'unmatched')


def review_settings(config):
    enabled = config.get('try_alternative_groups', True)
    if not isinstance(enabled, bool):
        raise ValueError('其他引物组测试开关须为布尔值')
    value = config.get('primer_match_threshold', 50)
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        raise ValueError('引物唯一匹配率阈值须为 0–100 的数字') from None
    if isinstance(value, bool) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError('引物唯一匹配率阈值须为 0–100 的数字')
    return enabled, threshold


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
