"""Run the miner, then compare occurrence groups against a separate manifest."""
from __future__ import annotations
from itertools import combinations
import json
from pathlib import Path
import platform
import sys
from mine_patterns import mine, dump_json

HERE = Path(__file__).resolve().parent


def occurrence_key(docid, roots):
    return docid + ':' + ','.join(sorted(roots))


def pairs_in_groups(groups):
    pairs = set()
    for members in groups:
        for a, b in combinations(sorted(set(members)), 2):
            if a.split(':')[0] != b.split(':')[0]:
                pairs.add((a, b))
    return pairs


def expected_groups(expected):
    return [{occurrence_key(x['document_id'], x['claim_ids']) for x in g['occurrences']} for g in expected['groups']]


def assess(result, expected):
    truth_groups = expected_groups(expected)
    predicted_groups = [{o['key'] for o in p['occurrences']} for p in result['patterns'] if p['support_count'] >= 2]
    truth = pairs_in_groups(truth_groups)
    pred = pairs_in_groups(predicted_groups)
    tp, fp, fn = len(truth & pred), len(pred - truth), len(truth - pred)
    group_sets = {frozenset(x) for x in predicted_groups}
    membership = {o['key']: p['pattern_id'] for p in result['patterns'] for o in p['occurrences']}
    checks = []
    for c in expected['contrast_checks']:
        def key(side):
            docid, names = c[side]
            return occurrence_key(docid, [expected['aliases'][docid][x] for x in names])
        left, right = key('left'), key('right')
        actual = left in membership and right in membership and membership[left] == membership[right]
        checks.append({'name': c['name'], 'expected_same': c['same'], 'actual_same': actual, 'pass': actual == c['same']})
    names = []
    for g, members in zip(expected['groups'], truth_groups):
        found = [p for p in result['patterns'] if {o['key'] for o in p['occurrences']} == members]
        names.append({'expected_id': g['id'], 'description': g['description'],
                      'found_pattern_id': found[0]['pattern_id'] if found else None,
                      'support_count': len({x['document_id'] for x in g['occurrences']})})
    return {'definition': 'Pair = two fragment occurrences from different documents, not two whole documents. Ground truth was author-assigned for synthetic data.',
            'ground_truth_positive_pairs': len(truth), 'predicted_positive_pairs': len(pred),
            'true_positive': tp, 'false_positive': fp, 'false_negative': fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'exact_expected_groups': sum(frozenset(x) in group_sets for x in truth_groups),
            'expected_groups_total': len(truth_groups), 'group_matches': names,
            'contrast_checks': checks, 'false_positive_pairs': sorted(pred - truth),
            'false_negative_pairs': sorted(truth - pred)}


def run():
    documents = json.loads((HERE / 'graphs.json').read_text(encoding='utf-8'))
    expected = json.loads((HERE / 'expected_patterns.json').read_text(encoding='utf-8'))
    results = {}
    # No tuning loop: all profiles use the same annotations, closure rules,
    # and unknown-meaning filter, except no_conditions deliberately drops conditions.
    for profile in ('strict', 'no_concepts', 'no_polarity', 'no_conditions', 'shape_only', 'entity_kind'):
        result = mine(documents, profile=profile)
        dump_json(HERE / 'results' / f'{profile}.json', result)
        evaluation = assess(result, expected)
        results[profile] = {'stats': result['stats'], 'evaluation': evaluation}
        print(f'{profile}: recurrent={result["stats"]["recurring_patterns"]}, '
              f'TP={evaluation["true_positive"]}, FP={evaluation["false_positive"]}, '
              f'FN={evaluation["false_negative"]}, seconds={result["stats"]["elapsed_seconds_including_validation"]:.6f}')
    report = {'python': sys.version, 'platform': platform.platform(),
              'annotation_provenance': 'Author-assigned structured graphs; no Bedrock or external LLM extraction was run.',
              'timing_scope': 'Single local run per profile, including graph-contract validation, excluding annotation authoring, file I/O, report generation, and any LLM inference.',
              'runs': results}
    dump_json(HERE / 'results' / 'evaluation.json', report)
    return report


if __name__ == '__main__':
    run()
