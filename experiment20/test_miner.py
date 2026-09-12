"""Integrity, adversarial, and independent brute-force checks for the prototype."""
import copy
from collections import Counter
from itertools import permutations
import json
from pathlib import Path
import random
import unittest

from mine_patterns import (Fragment, exact_mapping, fragments_for, mine, signature,
                          validate_document)
from evaluate import assess

HERE = Path(__file__).resolve().parent
DOCS = json.loads((HERE / 'graphs.json').read_text(encoding='utf-8'))
EXPECTED = json.loads((HERE / 'expected_patterns.json').read_text(encoding='utf-8'))


def membership(result):
    return {frozenset(o['key'] for o in p['occurrences']) for p in result['patterns']}


def relabel(doc, seed):
    d = copy.deepcopy(doc)
    rng = random.Random(seed)
    old = [n['id'] for n in d['nodes']]
    fresh = [f'n{i+101}' for i in range(len(old))]
    rng.shuffle(fresh)
    m = dict(zip(old, fresh))
    for n in d['nodes']:
        n['id'] = m[n['id']]
    for e in d['relations']:
        e['from'], e['to'] = m[e['from']], m[e['to']]
    for q in d['qualifiers']:
        q['applies_to'] = [m.get(x, x) for x in q['applies_to']]
    for key, field in [('dictionary_candidates', 'node_ids'), ('unrepresented', 'related_node_ids'), ('issues', 'related_node_ids')]:
        for item in d[key]:
            item[field] = [m[x] for x in item[field]]
    rng.shuffle(d['nodes'])
    rng.shuffle(d['relations'])
    return d, m


def fragment(doc, root_name='c1'):
    nodes, out = validate_document(doc)
    root = EXPECTED['aliases'][doc['document_id']][root_name]
    return next(f for f, _ in fragments_for(doc, nodes, out) if f and f.roots == (root,))


def brute_mapping(a, b):
    """Independent exhaustive permutation oracle; no miner pruning/signature."""
    if len(a.nodes) != len(b.nodes):
        return False
    u = list(a.nodes)
    wanted = Counter((e['from'], e['role'], e['to']) for e in b.edges)
    for targets in permutations(b.nodes):
        m = dict(zip(u, targets))
        if all(a.labels[x] == b.labels[m[x]] for x in u):
            got = Counter((m[e['from']], e['role'], m[e['to']]) for e in a.edges)
            if got == wanted:
                return True
    return False


class MinerTests(unittest.TestCase):
    def test_all_contracts(self):
        for d in DOCS:
            with self.subTest(document=d['document_id']):
                validate_document(d)

    def test_seven_expected_groups(self):
        r = mine(DOCS)
        e = assess(r, EXPECTED)
        self.assertEqual((e['exact_expected_groups'], e['false_positive'], e['false_negative']), (7, 0, 0))
        self.assertTrue(all(c['pass'] for c in e['contrast_checks']))

    def test_unknown_quantity_is_deferred(self):
        r = mine(DOCS)
        self.assertEqual([x['document_id'] for x in r['deferred']], ['K12'])
        self.assertIn('unregistered meaning', r['deferred'][0]['reasons'][0])

    def test_document_order_invariance(self):
        self.assertEqual(membership(mine(DOCS)), membership(mine(list(reversed(DOCS)))))

    def test_id_and_array_order_invariance(self):
        changed, expected = [], copy.deepcopy(EXPECTED)
        for ix, doc in enumerate(DOCS):
            d, m = relabel(doc, ix)
            changed.append(d)
            for g in expected['groups']:
                for o in g['occurrences']:
                    if o['document_id'] == doc['document_id']:
                        o['claim_ids'] = sorted(m[x] for x in o['claim_ids'])
            expected['aliases'][doc['document_id']] = {k: m[v] for k, v in expected['aliases'][doc['document_id']].items()}
        e = assess(mine(changed), expected)
        self.assertEqual((e['exact_expected_groups'], e['false_positive'], e['false_negative']), (7, 0, 0))

    def test_mapping_preserves_every_edge_and_node(self):
        docs = {d['document_id']: d for d in DOCS}
        for p in mine(DOCS)['patterns']:
            for o in p['occurrences']:
                m = o['node_mapping']
                self.assertEqual(len(m), len(set(m.values())))
                source = {e['id']: e for e in docs[o['document_id']]['relations']}
                for e in p['relations']:
                    mapped = source[o['edge_mapping'][e['id']]]
                    self.assertEqual((m[e['from']], e['role'], m[e['to']]), (mapped['from'], mapped['role'], mapped['to']))

    def test_disconnected_background_does_not_break_partial_match(self):
        r = mine([DOCS[0], DOCS[3]])
        self.assertEqual(r['stats']['recurring_patterns'], 3)
        self.assertEqual(r['stats']['fragments_total'], 6)

    def test_duplicate_occurrences_do_not_increase_document_support(self):
        d = copy.deepcopy(DOCS[0])
        second, _ = relabel(d, 3)
        for ix, e in enumerate(second['relations']):
            e['id'] = f'r{101+ix}'
        d['nodes'] += second['nodes']
        d['relations'] += second['relations']
        r = mine([d])
        self.assertEqual(r['stats']['recurring_patterns'], 0)
        self.assertTrue(all(p['occurrence_count'] == 2 and p['support_count'] == 1 for p in r['patterns']))

    def test_qualifier_blocks_only_affected_fragments(self):
        d = copy.deepcopy(DOCS[0])
        n = EXPECTED['aliases']['K01']['available']
        d['qualifiers'] = [{'id': 'q1', 'applies_to': [n], 'kind': 'SCOPE', 'text': '仮の未解決範囲',
                            'evidence_ids': ['ev2'], 'reason': 'test-only unresolved-scope marker'}]
        r = mine([d])
        self.assertEqual((r['stats']['fragments_accepted'], r['stats']['fragments_deferred']), (1, 2))

    def test_dangling_reference_rejected(self):
        d = copy.deepcopy(DOCS[0])
        d['relations'][0]['to'] = 'n99999'
        with self.assertRaises(ValueError):
            mine([d])

    def test_bad_quote_rejected(self):
        d = copy.deepcopy(DOCS[0])
        d['evidence'][0]['quote'] = 'not in original'
        with self.assertRaises(ValueError):
            mine([d])

    def test_and_requires_all_members(self):
        d = copy.deepcopy(DOCS[18])
        f = fragment(d)
        self.assertEqual(sum(e['role'] == 'MEMBER' for e in f.edges), 2)
        member = next(e for e in d['relations'] if e['role'] == 'MEMBER')
        d['relations'].remove(member)
        with self.assertRaises(ValueError):
            mine([d])

    def test_and_or_are_different(self):
        f = fragment(DOCS[18])
        g = copy.deepcopy(f)
        logic = next(n for n in g.nodes if g.nodes[n]['type'] == 'LOGIC')
        g.labels[logic] = tuple((k, '"OR"' if k == 'operator' else v) for k, v in g.labels[logic])
        self.assertIsNone(exact_mapping(f, g))

    def test_single_attribute_changes_are_not_matches(self):
        f = fragment(DOCS[0], 'c2')
        for field, newvalue in [('polarity', '"NEGATIVE"'), ('modality', '"ASSERTED"'), ('predicate', '"PRECEDES"'), ('context', '"EPISODE"')]:
            with self.subTest(field=field):
                g = copy.deepcopy(f)
                root = g.roots[0]
                g.labels[root] = tuple((k, newvalue if k == field else v) for k, v in g.labels[root])
                self.assertIsNone(exact_mapping(f, g))

    def test_quantity_meanings_differ(self):
        self.assertIsNone(exact_mapping(fragment(DOCS[0], 'c2'), fragment(DOCS[4], 'c2')))

    def test_condition_value_is_retained(self):
        self.assertIsNone(exact_mapping(fragment(DOCS[8]), fragment(DOCS[9])))
        self.assertIsNotNone(exact_mapping(fragment(DOCS[8]), fragment(DOCS[10])))

    def test_coreference_is_not_erased(self):
        f = fragment(DOCS[15])
        g = copy.deepcopy(f)
        q = next(n for n in g.nodes if g.nodes[n]['type'] == 'QUANTITY')
        bearer = next(e for e in g.edges if e['from'] == q and e['role'] == 'BEARER')
        original = bearer['to']
        g.nodes['another'] = copy.deepcopy(g.nodes[original])
        g.labels['another'] = g.labels[original]
        bearer['to'] = 'another'
        self.assertIsNone(exact_mapping(f, g))

    def test_equal_signature_does_not_mean_isomorphic(self):
        def cyclic(lengths):
            nodes, edges, start = {}, [], 0
            for size in lengths:
                ids = [str(i + start) for i in range(size)]
                nodes.update({n: {} for n in ids})
                edges += [{'from': ids[i], 'to': ids[(i+1) % size], 'role': 'R'} for i in range(size)]
                start += size
            return Fragment('test', (), nodes, edges, {n: ('L',) for n in nodes})
        a, b = cyclic([6]), cyclic([3, 3])
        self.assertEqual(signature(a), signature(b))
        self.assertIsNone(exact_mapping(a, b))

    def test_matcher_against_independent_permutation_oracle(self):
        rng = random.Random(62026)
        for ix in range(80):
            size = rng.randint(2, 6)
            ids = [str(i) for i in range(size)]
            a = Fragment('A', (), {n: {} for n in ids}, [], {n: (rng.choice('XY'),) for n in ids})
            for u in ids:
                for v in ids:
                    if rng.random() < .20:
                        a.edges.append({'from': u, 'to': v, 'role': rng.choice('RS')})
            if ix % 2:
                shuffled = ids[:]
                rng.shuffle(shuffled)
                m = dict(zip(ids, shuffled))
                b = Fragment('B', (), {m[n]: {} for n in ids},
                             [{'from': m[e['from']], 'to': m[e['to']], 'role': e['role']} for e in a.edges],
                             {m[n]: label for n, label in a.labels.items()})
            else:
                b = copy.deepcopy(a)
                if b.edges:
                    b.edges[0]['to'] = rng.choice(ids)
                else:
                    b.labels[ids[0]] = ('Z',)
            with self.subTest(example=ix):
                self.assertEqual(exact_mapping(a, b) is not None, brute_mapping(a, b))


if __name__ == '__main__':
    unittest.main(verbosity=2)
