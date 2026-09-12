"""Mine small argument-complete structures from knowhow-graph-0.1.

Python 3.10+, standard library only. No text parsing, LLM call, or ground-truth
access occurs in this module. Entity substitution is an explicit abstraction;
matches are structural candidates, not validated transferable principles.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import time

SCOPED = {'ACTION', 'STATE', 'CHANGE', 'CLAIM'}
SCOPE_FIELDS = {'polarity', 'modality', 'context'}
COMMON = {'id', 'type', 'surface', 'evidence_ids'}
EXTRA = {
    'ENTITY': {'concept_id', 'entity_kind', 'dictionary_entry_id'},
    'ACTION': {'concept_id', 'dictionary_entry_id'},
    'QUANTITY': {'concept_id', 'dimension', 'unit', 'dictionary_entry_id'},
    'CHANGE': {'direction'},
    'STATE': {'form', 'concept_id', 'dictionary_entry_id', 'comparator', 'value', 'value_unit'},
    'CLAIM': {'predicate', 'match_status'},
    'LOGIC': {'operator'},
}
TERMS = {'ACTION', 'STATE', 'CHANGE', 'LOGIC'}
ROLES = {
    'AGENT': ({'ACTION'}, {'ENTITY'}),
    'TARGET': ({'ACTION'}, {'ENTITY', 'QUANTITY'}),
    'ORIGIN': ({'ACTION'}, {'ENTITY'}),
    'DESTINATION': ({'ACTION'}, {'ENTITY'}),
    'BEARER': ({'QUANTITY'}, {'ENTITY'}),
    'QUANTITY': ({'CHANGE'}, {'QUANTITY'}),
    'SUBJECT': ({'STATE'}, {'ENTITY', 'QUANTITY'}),
    'FROM': ({'CLAIM'}, TERMS), 'TO': ({'CLAIM'}, TERMS),
    'CONDITION': ({'CLAIM'}, TERMS), 'MEMBER': ({'LOGIC'}, TERMS),
}
CORE = {
    **{f'core:action.{s}': 'ACTION' for s in
       'add remove move split combine observe measure compare adjust wait start stop'.split()},
    **{f'core:quantity.{s}': 'QUANTITY' for s in
       'count activity_duration required_duration available_duration waiting_duration distance mass temperature monetary_amount proportion'.split()},
    'core:state.delayed': 'STATE',
}
DIMENSIONS = dict(zip(
    'count activity_duration required_duration available_duration waiting_duration distance mass temperature monetary_amount proportion'.split(),
    'COUNT TIME TIME TIME TIME LENGTH MASS TEMPERATURE MONEY RATIO'.split()))
ENUMS = {
    'polarity': {'POSITIVE', 'NEGATIVE'},
    'modality': {'ASSERTED', 'POSSIBLE', 'TENDENCY', 'UNSPECIFIED'},
    'context': {'EPISODE', 'GENERAL', 'CONDITIONAL', 'RECOMMENDATION', 'GOAL', 'UNSPECIFIED'},
    'entity_kind': set('PERSON GROUP PHYSICAL_OBJECT INFORMATION ACTIVITY SYSTEM PLACE SUBSTANCE CATEGORY ABSTRACT UNKNOWN'.split()),
    'dimension': set('TIME COUNT LENGTH MASS TEMPERATURE MONEY RATIO OTHER UNKNOWN'.split()),
    'direction': {'INCREASE', 'DECREASE', 'OTHER'},
    'form': {'QUALITATIVE', 'COMPARISON'},
    'predicate': {'CAUSES', 'SIGNALS', 'REQUIRES', 'PREVENTS', 'PRECEDES'},
    'match_status': {'READY', 'NEEDS_REVIEW'},
    'operator': {'AND', 'OR', 'NOT'},
}
PROFILES = ('strict', 'no_concepts', 'no_polarity', 'no_conditions', 'shape_only', 'entity_kind')


def dump_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_document(doc, dictionary=None):
    """Validate the contract used by this miner, not arbitrary JSON Schema."""
    docid = doc.get('document_id', '?')
    require(doc.get('schema_version') == 'knowhow-graph-0.1', f'{docid}: wrong schema version')
    require(doc.get('dictionary_versions', {}).get('core') == 'core-0.1', f'{docid}: wrong core dictionary')
    registry = dict(CORE)
    if dictionary:
        require(doc['dictionary_versions']['additional'] == dictionary['version'], f'{docid}: additional dictionary mismatch')
        for entry in dictionary['concepts']:
            require(entry['id'].startswith('local:'), f'{docid}: additional IDs must start with local:')
            require(entry['id'] not in registry, f'{docid}: duplicate dictionary ID')
            registry[entry['id']] = entry['node_type']
    else:
        require(doc['dictionary_versions']['additional'] == 'none', f'{docid}: supply --dictionary')
    text = doc['text']
    nodes = {n['id']: n for n in doc['nodes']}
    evidence = {e['id']: e['quote'] for e in doc['evidence']}
    require(len(nodes) == len(doc['nodes']), f'{docid}: duplicate node ID')
    require(len(evidence) == len(doc['evidence']), f'{docid}: duplicate evidence ID')
    require(all(q and q in text for q in evidence.values()), f'{docid}: evidence not in original text')
    for n in nodes.values():
        t = n['type']
        require(t in EXTRA, f'{docid}: unknown node type {t}')
        fields = COMMON | EXTRA[t] | (SCOPE_FIELDS if t in SCOPED else set())
        require(set(n) == fields, f'{docid}/{n["id"]}: fields differ: {set(n) ^ fields}')
        require(re.fullmatch(r'n[1-9][0-9]*', n['id']), f'{docid}: invalid node ID')
        require(n['surface'] and n['surface'] in text, f'{docid}/{n["id"]}: surface not in text')
        require(n['evidence_ids'] and all(e in evidence for e in n['evidence_ids']), f'{docid}: missing node evidence')
        require(any(n['surface'] in evidence[e] for e in n['evidence_ids']), f'{docid}: surface not covered by evidence')
        for key, values in ENUMS.items():
            if key in n:
                require(n[key] in values, f'{docid}: invalid {key}')
        if 'concept_id' in n:
            require(n['concept_id'] == n['dictionary_entry_id'], f'{docid}: inconsistent dictionary reference')
            if n['concept_id'] is not None:
                require(registry.get(n['concept_id']) == t, f'{docid}: unknown or wrong-type concept {n["concept_id"]}')
        if t == 'QUANTITY' and n['concept_id'] in CORE:
            require(n['dimension'] == DIMENSIONS[n['concept_id'].split('.')[-1]], f'{docid}: inconsistent dimension')
        if t == 'STATE':
            if n['form'] == 'QUALITATIVE':
                require(all(n[k] is None for k in ('comparator', 'value', 'value_unit')), f'{docid}: malformed qualitative state')
            else:
                require(n['concept_id'] is None and n['comparator'] in {'EQ', 'NE', 'LT', 'LE', 'GT', 'GE'}, f'{docid}: malformed comparison')
                require(isinstance(n['value'], (str, int, float)) and not isinstance(n['value'], bool), f'{docid}: invalid comparison value')
    outgoing = defaultdict(list)
    seen, edge_ids = set(), set()
    for e in doc['relations']:
        require(set(e) == {'id', 'from', 'role', 'to', 'evidence_ids'}, f'{docid}: invalid edge fields')
        require(e['id'] not in edge_ids, f'{docid}: duplicate edge ID')
        edge_ids.add(e['id'])
        require(e['from'] in nodes and e['to'] in nodes, f'{docid}: dangling edge')
        require(e['role'] in ROLES, f'{docid}: unknown role')
        source_types, target_types = ROLES[e['role']]
        require(nodes[e['from']]['type'] in source_types and nodes[e['to']]['type'] in target_types, f'{docid}: invalid endpoint types')
        key = (e['from'], e['role'], e['to'])
        require(key not in seen, f'{docid}: duplicate edge')
        seen.add(key)
        require(e['evidence_ids'] and all(x in evidence for x in e['evidence_ids']), f'{docid}: missing edge evidence')
        outgoing[e['from']].append(e)
    for n in nodes.values():
        edges = outgoing[n['id']]
        counts = Counter(e['role'] for e in edges)
        t = n['type']
        if t == 'CLAIM':
            require(counts['FROM'] == counts['TO'] == 1 and counts['CONDITION'] <= 1, f'{docid}: CLAIM cardinality')
        if t == 'CHANGE':
            require(counts['QUANTITY'] == 1, f'{docid}: CHANGE cardinality')
        if t == 'QUANTITY':
            require(counts['BEARER'] <= 1, f'{docid}: QUANTITY cardinality')
        if t == 'STATE':
            require(counts['SUBJECT'] <= 1, f'{docid}: STATE cardinality')
            if n['form'] == 'COMPARISON':
                require(all(nodes[e['to']]['type'] == 'QUANTITY' for e in edges), f'{docid}: comparison subject type')
        if t == 'LOGIC':
            require(counts['MEMBER'] == 1 if n['operator'] == 'NOT' else counts['MEMBER'] >= 2, f'{docid}: LOGIC cardinality')
    visited, active = set(), set()
    def visit(v):
        require(v not in active, f'{docid}: cyclic LOGIC')
        if v in visited:
            return
        active.add(v)
        for e in outgoing[v]:
            if nodes[e['to']]['type'] == 'LOGIC':
                visit(e['to'])
        active.remove(v)
        visited.add(v)
    for n in nodes.values():
        if n['type'] == 'LOGIC':
            visit(n['id'])
    for q in doc['qualifiers']:
        require(all(x in nodes or x in edge_ids for x in q['applies_to']), f'{docid}: qualifier target missing')
        require(all(e in evidence for e in q['evidence_ids']), f'{docid}: qualifier evidence missing')
    for key, ref_field in [('issues', 'related_node_ids'), ('unrepresented', 'related_node_ids'), ('dictionary_candidates', 'node_ids')]:
        for item in doc[key]:
            require(all(x in nodes for x in item[ref_field]), f'{docid}: {key} reference missing')
    return nodes, outgoing


def argument_closure(roots, outgoing, include_conditions=True):
    visited, edge_ids = set(), set()
    pending = list(roots)
    while pending:
        n = pending.pop()
        if n in visited:
            continue
        visited.add(n)
        for e in outgoing.get(n, []):
            if e['role'] == 'CONDITION' and not include_conditions:
                continue
            edge_ids.add(e['id'])
            pending.append(e['to'])
    return visited, edge_ids


def blockers(doc, node_ids, edge_ids, nodes, outgoing):
    reasons = []
    for nid in sorted(node_ids):
        n = nodes[nid]
        t = n['type']
        if t in {'ACTION', 'QUANTITY'} or (t == 'STATE' and n['form'] == 'QUALITATIVE'):
            if n['concept_id'] is None:
                reasons.append(f'{nid}: unregistered meaning')
        if t in SCOPED and (n['context'] == 'UNSPECIFIED' or n['modality'] == 'UNSPECIFIED'):
            reasons.append(f'{nid}: unspecified scope')
        if t == 'CHANGE' and n['direction'] == 'OTHER':
            reasons.append(f'{nid}: unsupported change')
        if t == 'CLAIM' and n['match_status'] != 'READY':
            reasons.append(f'{nid}: needs review')
        if t in {'QUANTITY', 'STATE'}:
            role = 'BEARER' if t == 'QUANTITY' else 'SUBJECT'
            if not any(e['role'] == role for e in outgoing[nid]):
                reasons.append(f'{nid}: missing {role}')
    for q in doc['qualifiers']:
        if set(q['applies_to']) & (node_ids | edge_ids):
            reasons.append(f'{q["id"]}: unresolved qualifier')
    for key in ('unrepresented', 'issues'):
        for item in doc[key]:
            related = set(item['related_node_ids'])
            if not related or related & node_ids:
                reasons.append(f'{key}: unresolved statement')
    return reasons


def node_label(n, profile):
    t = n['type']
    if t == 'ENTITY':
        # Explicit abstraction. Original names, kinds, concepts stay in occurrences.
        return (t, n['entity_kind']) if profile == 'entity_kind' else (t,)
    if profile == 'shape_only':
        return (t,)
    result = {'type': t}
    fields = {
        'ACTION': ['concept_id'], 'QUANTITY': ['concept_id', 'dimension', 'unit'],
        'CHANGE': ['direction'], 'STATE': ['form', 'concept_id', 'comparator', 'value', 'value_unit'],
        'CLAIM': ['predicate'], 'LOGIC': ['operator'],
    }[t] + (sorted(SCOPE_FIELDS) if t in SCOPED else [])
    for field in fields:
        if field == 'concept_id' and profile == 'no_concepts':
            continue
        if field == 'polarity' and profile == 'no_polarity':
            continue
        result[field] = n[field]
    return tuple(sorted((key, json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in result.items()))


@dataclass
class Fragment:
    document_id: str
    roots: tuple
    nodes: dict
    edges: list
    labels: dict

    @property
    def occurrence_key(self):
        return self.document_id + ':' + ','.join(self.roots)


def fragments_for(doc, nodes, outgoing, max_claims=2, profile='strict'):
    claims = sorted(n['id'] for n in nodes.values() if n['type'] == 'CLAIM')
    closures = {c: argument_closure([c], outgoing) for c in claims}
    roots_list = [(c,) for c in claims]
    if max_claims >= 2:
        roots_list += [(a, b) for a, b in combinations(claims, 2) if closures[a][0] & closures[b][0]]
    for roots in roots_list:
        full_nodes, full_edges = argument_closure(roots, outgoing)
        why = blockers(doc, full_nodes, full_edges, nodes, outgoing)
        if why:
            yield None, {'document_id': doc['document_id'], 'roots': list(roots), 'reasons': why}
            continue
        ids, edge_ids = argument_closure(roots, outgoing, profile != 'no_conditions')
        subset = {nid: nodes[nid] for nid in ids}
        yield Fragment(doc['document_id'], roots, subset,
                       [e for e in doc['relations'] if e['id'] in edge_ids],
                       {nid: node_label(n, profile) for nid, n in subset.items()}), None


def signature(f):
    """A necessary invariant only; never used to declare a match."""
    return (len(f.nodes), len(f.edges),
            tuple(sorted(Counter(f.labels.values()).items(), key=repr)),
            tuple(sorted(Counter(e['role'] for e in f.edges).items())))


def adjacency(f):
    edges = defaultdict(Counter)
    neighbors = {n: Counter() for n in f.nodes}
    for e in f.edges:
        u, v, role = e['from'], e['to'], e['role']
        edges[u, v][role] += 1
        neighbors[u][('out', role, f.labels[v])] += 1
        neighbors[v][('in', role, f.labels[u])] += 1
    return edges, neighbors


def exact_mapping(a, b):
    """Return an exact directed attributed multigraph bijection a -> b, or None.

    Complete backtracking with label/neighborhood pruning; no timeout or heuristic
    acceptance. Exponential worst case: intended for small extracted fragments.
    """
    if signature(a) != signature(b):
        return None
    ea, na = adjacency(a)
    eb, nb = adjacency(b)
    choices = {u: [v for v in sorted(b.nodes) if a.labels[u] == b.labels[v] and na[u] == nb[v]] for u in a.nodes}
    if any(not options for options in choices.values()):
        return None
    mapping, used = {}, set()
    def compatible(u, v):
        if ea[u, u] != eb[v, v]:
            return False
        return all(ea[u, x] == eb[v, y] and ea[x, u] == eb[y, v] for x, y in mapping.items())
    def search():
        if len(mapping) == len(a.nodes):
            return dict(mapping)
        remaining = {u: [v for v in choices[u] if v not in used and compatible(u, v)] for u in a.nodes if u not in mapping}
        u = min(remaining, key=lambda n: (len(remaining[n]), n))
        for v in remaining[u]:
            mapping[u] = v
            used.add(v)
            result = search()
            if result is not None:
                return result
            del mapping[u]
            used.remove(v)
        return None
    return search()


def occurrence(f, representative, mapping, doc):
    # Representative IDs refer to nodes in representative_document, not global IDs.
    target_edges = {(e['from'], e['role'], e['to']): e for e in f.edges}
    edge_map = {e['id']: target_edges[mapping[e['from']], e['role'], mapping[e['to']]]['id'] for e in representative.edges}
    ev_ids = {x for n in f.nodes.values() for x in n['evidence_ids']} | {x for e in f.edges for x in e['evidence_ids']}
    return {
        'key': f.occurrence_key, 'document_id': f.document_id, 'claim_ids': list(f.roots),
        'node_mapping': dict(sorted(mapping.items())), 'edge_mapping': edge_map,
        'entity_substitutions': [{'pattern_node': u, 'source_node': v,
            'source_surface': f.nodes[v]['surface'], 'source_kind': f.nodes[v]['entity_kind'],
            'source_concept_id': f.nodes[v]['concept_id']} for u, v in sorted(mapping.items()) if f.nodes[v]['type'] == 'ENTITY'],
        'evidence': [e for e in doc['evidence'] if e['id'] in ev_ids],
    }


def mine(documents, profile='strict', max_claims=2, min_support=2, dictionary=None):
    require(profile in PROFILES, 'unknown profile')
    require(max_claims in (1, 2), 'this prototype supports max_claims=1 or 2')
    require(min_support >= 1, 'min_support must be positive')
    start = time.perf_counter()
    ids = [d['document_id'] for d in documents]
    require(len(ids) == len(set(ids)), 'duplicate document IDs')
    versions = {json.dumps(d['dictionary_versions'], sort_keys=True) for d in documents}
    require(len(versions) <= 1, 'mixed dictionary versions')
    buckets, patterns, skipped = defaultdict(list), [], []
    tests = accepted = total = 0
    for doc in documents:
        nodes, outgoing = validate_document(doc, dictionary)
        for f, reason in fragments_for(doc, nodes, outgoing, max_claims, profile):
            total += 1
            if reason:
                skipped.append(reason)
                continue
            accepted += 1
            key = signature(f)
            match = None
            for p in buckets[key]:
                tests += 1
                mapping = exact_mapping(p['_representative'], f)
                if mapping is not None:
                    match = p
                    break
            if match is None:
                match = {'pattern_id': f'P{len(patterns)+1:03d}', '_representative': f,
                         'claim_count': len(f.roots), 'representative_document': f.document_id,
                         'representative_claim_ids': list(f.roots),
                         'nodes': [f.nodes[n] for n in sorted(f.nodes)], 'relations': f.edges,
                         'match_labels': {n: f.labels[n] for n in sorted(f.nodes)}, 'occurrences': []}
                patterns.append(match)
                buckets[key].append(match)
                mapping = {n: n for n in f.nodes}
            match['occurrences'].append(occurrence(f, match['_representative'], mapping, doc))
    for p in patterns:
        del p['_representative']
        p['supporting_documents'] = sorted({o['document_id'] for o in p['occurrences']})
        p['support_count'] = len(p['supporting_documents'])
        p['occurrence_count'] = len(p['occurrences'])
    recurring = [p for p in patterns if p['support_count'] >= min_support]
    digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {'profile': profile, 'entity_policy': 'match entity_kind' if profile == 'entity_kind' else 'ENTITY as distinct variables; original kinds recorded',
            'max_claims': max_claims, 'min_support': min_support, 'input_sha256': digest,
            'stats': {'documents': len(documents), 'fragments_total': total, 'fragments_accepted': accepted,
                      'fragments_deferred': len(skipped), 'distinct_patterns': len(patterns),
                      'recurring_patterns': len(recurring), 'exact_isomorphism_calls': tests,
                      'elapsed_seconds_including_validation': time.perf_counter() - start},
            'patterns': patterns, 'recurring_pattern_ids': [p['pattern_id'] for p in recurring], 'deferred': skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='JSON array of knowhow-graph-0.1 documents')
    parser.add_argument('--output', type=Path, default=Path('patterns.json'))
    parser.add_argument('--profile', choices=PROFILES, default='strict')
    parser.add_argument('--max-claims', type=int, choices=(1, 2), default=2)
    parser.add_argument('--min-support', type=int, default=2)
    parser.add_argument('--dictionary', type=Path)
    args = parser.parse_args()
    documents = json.loads(args.input.read_text(encoding='utf-8-sig'))
    dictionary = json.loads(args.dictionary.read_text(encoding='utf-8-sig')) if args.dictionary else None
    result = mine(documents, args.profile, args.max_claims, args.min_support, dictionary)
    dump_json(args.output, result)
    print(json.dumps(result['stats'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
