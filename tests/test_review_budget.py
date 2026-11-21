import pytest
from engine import ContextBudgetExceeded, Model, RULES, model_rows


def test_oversized_candidates_are_all_read_and_tail_evidence_survives():
    seen = set()
    class Bounded(Model):
        def __init__(self):
            super().__init__(lambda *_: None, 'tacc')
        def call(self, system, payload, phase):
            paragraphs = payload['paragraphs']
            if len(paragraphs) > 2:
                raise ContextBudgetExceeded('synthetic limit')
            seen.update(p['id'] for p in paragraphs)
            evidence = [{'paragraph_id': p['id'], 'quote': p['text']}
                        for p in paragraphs if p['id'] == 'P0006']
            return {'rows': [{'category': 'termination', 'status': 'found' if evidence else 'not_found',
                              'evidence': evidence, 'finding': 'Termination requires notice.' if evidence else ''}]}
    paragraphs = [{'id': f'P{i:04}', 'text': 'Termination requires notice.' if i == 6 else 'Other terms.', 'page': None}
                  for i in range(1, 7)]
    row = model_rows(Bounded(), ['termination'], {}, '', paragraphs, 'termination')['termination']
    assert seen == {p['id'] for p in paragraphs}
    assert row['status'] == 'found'
    assert row['evidence'][0]['paragraph_id'] == 'P0006'


def test_unfittable_single_paragraph_is_reported_without_truncation():
    class NeverFits(Model):
        def call(self, *args):
            raise ContextBudgetExceeded('metadata too large')
    with pytest.raises(ContextBudgetExceeded):
        model_rows(NeverFits(lambda *_: None, 'tacc'), ['termination'], {}, '',
                   [{'id': 'P0001', 'text': 'Original text', 'page': None}], 'termination')


def test_split_response_cannot_quote_an_unread_sibling_paragraph():
    class WrongQuote(Model):
        def call(self, system, payload, phase):
            if len(payload['paragraphs']) > 1:
                raise ContextBudgetExceeded('synthetic limit')
            return {'rows': [{'category': 'termination', 'status': 'found',
                              'evidence': [{'paragraph_id': 'P0002', 'quote': 'Second.'}]}]}
    with pytest.raises(ValueError, match='未被实际读取'):
        model_rows(WrongQuote(lambda *_: None, 'tacc'), ['termination'], {}, '',
                   [{'id': 'P0001', 'text': 'First.', 'page': None},
                    {'id': 'P0002', 'text': 'Second.', 'page': None}], 'termination')
