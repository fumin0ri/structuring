"""Reproduce 20 synthetic texts and author-assigned structured annotations.

This is NOT an automatic text extractor. The annotations below were authored for
the experiment using the current prompt's schema. The mining program receives
only graphs.json, never this source or the expected-pattern manifest.
"""
import random
from pathlib import Path
from mine_patterns import dump_json

HERE = Path(__file__).resolve().parent


class Annotation:
    def __init__(self, docid, domain, sentences):
        self.docid, self.domain = docid, domain
        self.sentences = sentences
        self.text = ''.join(sentences)
        self.nodes, self.edges, self.named = [], [], {}
        self.candidates = []

    def node(self, name, typ, surface, ev, **fields):
        nodeid = f'n{len(self.nodes)+1}'
        n = dict(id=nodeid, type=typ, surface=surface, evidence_ids=[f'ev{i}' for i in ev], **fields)
        self.nodes.append(n)
        self.named[name] = nodeid
        return name

    def entity(self, name, surface, kind, ev):
        return self.node(name, 'ENTITY', surface, ev, concept_id=None, entity_kind=kind, dictionary_entry_id=None)

    def edge(self, source, role, target, ev):
        self.edges.append(dict(id=f'r{len(self.edges)+1}', **{'from': self.named[source]}, role=role,
                               to=self.named[target], evidence_ids=[f'ev{i}' for i in ev]))

    def action(self, name, surface, operation, target, ev, context='CONDITIONAL'):
        c = 'core:action.' + operation
        self.node(name, 'ACTION', surface, ev, concept_id=c, dictionary_entry_id=c,
                  polarity='POSITIVE', modality='ASSERTED', context=context)
        self.edge(name, 'TARGET', target, ev)

    def quantity(self, name, surface, concept, bearer, ev, dimension='TIME', unit=None):
        c = 'core:quantity.' + concept if concept else None
        self.node(name, 'QUANTITY', surface, ev, concept_id=c, dictionary_entry_id=c, dimension=dimension, unit=unit)
        self.edge(name, 'BEARER', bearer, ev)

    def change(self, name, surface, direction, quantity, ev, polarity='POSITIVE'):
        self.node(name, 'CHANGE', surface, ev, direction=direction, polarity=polarity,
                  modality='ASSERTED', context='CONDITIONAL')
        self.edge(name, 'QUANTITY', quantity, ev)

    def comparison(self, name, surface, quantity, op, value, unit, ev):
        self.node(name, 'STATE', surface, ev, form='COMPARISON', concept_id=None, dictionary_entry_id=None,
                  comparator=op, value=value, value_unit=unit, polarity='POSITIVE', modality='ASSERTED', context='CONDITIONAL')
        self.edge(name, 'SUBJECT', quantity, ev)

    def delayed(self, name, surface, subject, ev):
        self.node(name, 'STATE', surface, ev, form='QUALITATIVE', concept_id='core:state.delayed',
                  dictionary_entry_id='core:state.delayed', comparator=None, value=None, value_unit=None,
                  polarity='POSITIVE', modality='ASSERTED', context='CONDITIONAL')
        self.edge(name, 'SUBJECT', subject, ev)

    def claim(self, name, sentence, source, target, ev, predicate='CAUSES', modality='POSSIBLE', polarity='POSITIVE', condition=None):
        self.node(name, 'CLAIM', sentence, ev, predicate=predicate, modality=modality,
                  polarity=polarity, context='GENERAL', match_status='READY')
        self.edge(name, 'FROM', source, ev)
        self.edge(name, 'TO', target, ev)
        if condition:
            self.edge(name, 'CONDITION', condition, ev)

    def finish(self):
        # Rename and permute to ensure the matcher cannot exploit shared IDs/order.
        rng = random.Random(int(self.docid[1:]) * 79)
        newids = [f'n{i+1}' for i in range(len(self.nodes))]
        rng.shuffle(newids)
        renaming = dict(zip([n['id'] for n in self.nodes], newids))
        for n in self.nodes:
            n['id'] = renaming[n['id']]
        for e in self.edges:
            e['from'], e['to'] = renaming[e['from']], renaming[e['to']]
        for item in self.candidates:
            item['node_ids'] = [renaming[n] for n in item['node_ids']]
        rng.shuffle(self.nodes)
        rng.shuffle(self.edges)
        aliases = {k: renaming[v] for k, v in self.named.items()}
        doc = dict(schema_version='knowhow-graph-0.1', dictionary_versions={'core': 'core-0.1', 'additional': 'none'},
                   document_id=self.docid, text=self.text, nodes=self.nodes, relations=self.edges,
                   evidence=[{'id': f'ev{i+1}', 'quote': s} for i, s in enumerate(self.sentences)],
                   qualifiers=[], unrepresented=[], dictionary_candidates=self.candidates, issues=[])
        raw = dict(document_id=self.docid, domain=self.domain, text=self.text)
        return raw, doc, aliases


def chain(docid, domain, target, kind, activity, work, variant='normal', extra=False):
    s1 = f'{target}の追加によって、{activity}にかかる時間が増えることがある。'
    s2 = f'{activity}にかかる時間の増加によって、{work}に使える時間が減ることがある。'
    result_concept, result_surface, decrease_surface = 'available_duration', f'{work}に使える時間', f'{work}に使える時間が減る'
    mode = 'POSSIBLE'
    if variant == 'required':
        s2 = f'{activity}にかかる時間の増加によって、{work}に必要な時間が減ることがある。'
        result_concept, result_surface, decrease_surface = 'required_duration', f'{work}に必要な時間', f'{work}に必要な時間が減る'
    if variant == 'negative':
        s2 = f'{activity}にかかる時間の増加は、{work}に使える時間の減少を引き起こさない。'
        decrease_surface = f'{work}に使える時間の減少'
    if variant == 'temporal':
        s2 = f'{activity}にかかる時間が増えた後に、{work}に使える時間が減ることがある。'
    if variant == 'asserted':
        s1 = f'{target}の追加によって、{activity}にかかる時間が増える。'
        s2 = f'{activity}にかかる時間の増加によって、{work}に使える時間が減る。'
        mode = 'ASSERTED'
    sentences = [s1, s2] + (['また、担当者は受付の様子を観察する。'] if extra else [])
    a = Annotation(docid, domain, sentences)
    a.entity('target', target, kind, [1])
    a.action('add', f'{target}の追加', 'add', 'target', [1])
    a.entity('activity', activity, 'ACTIVITY', [1, 2])
    a.quantity('duration', f'{activity}にかかる時間', 'activity_duration', 'activity', [1, 2])
    a.change('increase', f'{activity}にかかる時間が増える', 'INCREASE', 'duration', [1, 2])
    a.entity('work', work, 'ACTIVITY', [2])
    a.quantity('available', result_surface, result_concept, 'work', [2])
    a.change('decrease', decrease_surface, 'DECREASE', 'available', [2])
    a.claim('c1', s1, 'add', 'increase', [1], modality=mode)
    a.claim('c2', s2, 'increase', 'decrease', [2],
            predicate='PRECEDES' if variant == 'temporal' else 'CAUSES',
            polarity='NEGATIVE' if variant == 'negative' else 'POSITIVE',
            modality='ASSERTED' if variant == 'negative' else mode)
    if extra:
        a.entity('observer', '担当者', 'PERSON', [3])
        a.entity('scene', '受付の様子', 'ABSTRACT', [3])
        a.action('observe', '観察する', 'observe', 'scene', [3], context='GENERAL')
        a.edge('observe', 'AGENT', 'observer', [3])
    return a


def conditional(docid, domain, counted, limit, activity, work):
    s = f'{counted}件数が{limit}件以上の場合、{activity}にかかる時間の増加によって、{work}に使える時間が減ることがある。'
    a = Annotation(docid, domain, [s])
    a.entity('counted', counted, 'ACTIVITY', [1])
    a.quantity('count', f'{counted}件数', 'count', 'counted', [1], dimension='COUNT', unit='件')
    a.comparison('condition', f'{counted}件数が{limit}件以上', 'count', 'GE', limit, '件', [1])
    a.entity('activity', activity, 'ACTIVITY', [1])
    a.quantity('duration', f'{activity}にかかる時間', 'activity_duration', 'activity', [1])
    a.change('increase', f'{activity}にかかる時間の増加', 'INCREASE', 'duration', [1])
    a.entity('work', work, 'ACTIVITY', [1])
    a.quantity('available', f'{work}に使える時間', 'available_duration', 'work', [1])
    a.change('decrease', f'{work}に使える時間が減る', 'DECREASE', 'available', [1])
    a.claim('c1', s, 'increase', 'decrease', [1], condition='condition')
    return a


def signal(docid, domain, activity, process, predicate='SIGNALS'):
    verb = '示す' if predicate == 'SIGNALS' else '引き起こす'
    s = f'{activity}にかかる時間の増加は、{process}の遅れを{verb}ことがある。'
    a = Annotation(docid, domain, [s])
    a.entity('activity', activity, 'ACTIVITY', [1])
    a.quantity('duration', f'{activity}にかかる時間', 'activity_duration', 'activity', [1])
    a.change('increase', f'{activity}にかかる時間の増加', 'INCREASE', 'duration', [1])
    a.entity('process', process, 'ACTIVITY', [1])
    a.delayed('delayed', f'{process}の遅れ', 'process', [1])
    a.claim('c1', s, 'increase', 'delayed', [1], predicate=predicate)
    return a


def requires(docid, domain, machine, comparator='LE'):
    word = '以下' if comparator == 'LE' else '以上'
    s = f'{machine}を始動するには、{machine}の温度が80度{word}であることが必要である。'
    a = Annotation(docid, domain, [s])
    a.entity('machine', machine, 'PHYSICAL_OBJECT', [1])
    a.action('start', f'{machine}を始動する', 'start', 'machine', [1])
    a.quantity('temperature', f'{machine}の温度', 'temperature', 'machine', [1], dimension='TEMPERATURE', unit='度')
    a.comparison('state', f'{machine}の温度が80度{word}', 'temperature', comparator, 80, '度', [1])
    a.claim('c1', s, 'start', 'state', [1], predicate='REQUIRES', modality='ASSERTED')
    return a


def conjunction(docid, domain, first, second, work):
    s = f'{first}にかかる時間の増加と、{second}にかかる時間の増加が共同の原因となり、{work}に使える時間が減ることがある。'
    a = Annotation(docid, domain, [s])
    for ix, activity in enumerate([first, second]):
        a.entity(f'a{ix}', activity, 'ACTIVITY', [1])
        a.quantity(f'q{ix}', f'{activity}にかかる時間', 'activity_duration', f'a{ix}', [1])
        a.change(f'ch{ix}', f'{activity}にかかる時間の増加', 'INCREASE', f'q{ix}', [1])
    a.node('and', 'LOGIC', f'{first}にかかる時間の増加と、{second}にかかる時間の増加', [1], operator='AND')
    a.edge('and', 'MEMBER', 'ch0', [1])
    a.edge('and', 'MEMBER', 'ch1', [1])
    a.entity('work', work, 'ACTIVITY', [1])
    a.quantity('available', f'{work}に使える時間', 'available_duration', 'work', [1])
    a.change('decrease', f'{work}に使える時間が減る', 'DECREASE', 'available', [1])
    a.claim('c1', s, 'and', 'decrease', [1])
    return a


def build():
    cases = [
        chain('K01', 'ソフトウェア開発', '人員', 'GROUP', '説明・調整', '実作業'),
        chain('K02', '製造', '製品種類', 'CATEGORY', '段取り替え', '製造'),
        chain('K03', '予約サービス', '予約枠', 'ABSTRACT', '予約確認', '接客'),
        chain('K04', '申請審査', '書類様式', 'INFORMATION', '照合', '審査', extra=True),
        chain('K05', '文書処理', '参考書類', 'INFORMATION', '照合', '審査', variant='required'),
        chain('K06', '文書処理・因果否定', '帳票', 'INFORMATION', '照合', '審査', variant='negative'),
        chain('K07', '文書処理・時間順序', '確認項目', 'INFORMATION', '照合', '審査', variant='temporal'),
        chain('K08', '文書処理・断定', '記録項目', 'INFORMATION', '照合', '審査', variant='asserted'),
        conditional('K09', '受付', '受付', 10, '照合', '審査'),
        conditional('K10', '受注製造', '受注', 20, '段取り替え', '製造'),
        conditional('K11', '予約サービス', '予約', 10, '予約確認', '接客'),
    ]
    s = '手順の追加によって、作業のしやすさが増すことがある。'
    unknown = Annotation('K12', '作業手順・未登録概念', [s])
    unknown.entity('target', '手順', 'INFORMATION', [1])
    unknown.action('add', '手順の追加', 'add', 'target', [1])
    unknown.entity('work', '作業', 'ACTIVITY', [1])
    unknown.quantity('ease', '作業のしやすさ', None, 'work', [1], dimension='OTHER')
    unknown.change('increase', '作業のしやすさが増す', 'INCREASE', 'ease', [1])
    unknown.claim('c1', s, 'add', 'increase', [1])
    unknown.candidates.append({'node_ids': [unknown.named['ease']], 'surface': '作業のしやすさ',
                              'proposed_definition': '作業を行いやすい程度', 'reason': 'core-0.1に対応する量の概念がない'})
    cases.append(unknown)
    cases += [
        signal('K13', '窓口運営', '問い合わせの応答', '窓口業務'),
        signal('K14', '物流', '搬送', '出荷作業'),
        signal('K15', '審査・因果対照', '確認', '審査', predicate='CAUSES'),
        requires('K16', '装置運転', '装置'),
        requires('K17', '炉の運転', '炉'),
        requires('K18', 'ポンプ運転・比較演算子対照', 'ポンプ', comparator='GE'),
        conjunction('K19', '事務処理', '応答', '確認', '処理'),
        conjunction('K20', '出荷', '搬送', '荷役', '出荷'),
    ]
    outputs = [a.finish() for a in cases]
    raws = [x[0] for x in outputs]
    docs = [x[1] for x in outputs]
    aliases = {x[0]['document_id']: x[2] for x in outputs}
    groups = [
        ('G1', '追加 → 活動にかかる時間の増加', [f'K{i:02d}' for i in range(1, 8)], ['c1']),
        ('G2', '活動にかかる時間の増加 → 利用可能時間の減少', [f'K{i:02d}' for i in range(1, 5)], ['c2']),
        ('G3', '追加 → 活動時間増加 → 利用可能時間減少の連鎖', [f'K{i:02d}' for i in range(1, 5)], ['c1', 'c2']),
        ('G4', '件数10以上の条件付きで、活動時間増加 → 利用可能時間減少', ['K09', 'K11'], ['c1']),
        ('G5', '活動時間の増加が、業務の遅れの手がかりになる', ['K13', 'K14'], ['c1']),
        ('G6', '始動には、同じ対象の温度が80度以下であることが必要', ['K16', 'K17'], ['c1']),
        ('G7', '二つの活動時間がともに増加 → 利用可能時間減少', ['K19', 'K20'], ['c1']),
    ]
    expected = {'provenance': '事前に作成した人工データ上の期待グループ。探索器はこのファイルを読まない。',
                'groups': [{'id': gid, 'description': description, 'occurrences': [
                    {'document_id': d, 'claim_ids': sorted(aliases[d][a] for a in roots)} for d in members]
                           } for gid, description, members, roots in groups],
                'deferred_documents': ['K12'], 'aliases': aliases,
                'contrast_checks': [
                    {'name': '量の意味', 'left': ['K01', ['c2']], 'right': ['K05', ['c2']], 'same': False},
                    {'name': '因果の否定', 'left': ['K01', ['c2']], 'right': ['K06', ['c2']], 'same': False},
                    {'name': '因果と時間順序', 'left': ['K01', ['c2']], 'right': ['K07', ['c2']], 'same': False},
                    {'name': '可能性と断定', 'left': ['K01', ['c2']], 'right': ['K08', ['c2']], 'same': False},
                    {'name': '条件の有無', 'left': ['K01', ['c2']], 'right': ['K09', ['c1']], 'same': False},
                    {'name': '条件値10と20', 'left': ['K09', ['c1']], 'right': ['K10', ['c1']], 'same': False},
                    {'name': '同じ条件値10', 'left': ['K09', ['c1']], 'right': ['K11', ['c1']], 'same': True},
                    {'name': '兆候と因果', 'left': ['K13', ['c1']], 'right': ['K15', ['c1']], 'same': False},
                    {'name': '比較演算子以下と以上', 'left': ['K16', ['c1']], 'right': ['K18', ['c1']], 'same': False},
                    {'name': '共同条件と単独原因', 'left': ['K01', ['c2']], 'right': ['K19', ['c1']], 'same': False},
                ]}
    dump_json(HERE / 'corpus.json', raws)
    dump_json(HERE / 'graphs.json', docs)
    dump_json(HERE / 'expected_patterns.json', expected)
    lines = ['# 仮想ノウハウ20件', '', 'すべて今回の動作確認用に作成した仮想の記述です。事実として検証したノウハウではありません。', '']
    for r in raws:
        lines += [f'## {r["document_id"]}：{r["domain"]}', '', r['text'], '']
    (HERE / '仮想ノウハウ20件.md').write_text('\n'.join(lines), encoding='utf-8')
    return docs, expected


if __name__ == '__main__':
    graphs, _ = build()
    print(f'Wrote {len(graphs)} synthetic documents and author-assigned annotations.')
