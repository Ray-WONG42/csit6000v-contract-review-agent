from app.retrieval import vector_matches
from engine import DEFAULT_SKILL, Model


def test_long_paragraph_embedding_keeps_tail_and_original_ids():
    class Embeddings(Model):
        def __init__(self):
            super().__init__(lambda *_: None, 'tacc')

        def embed(self, texts):
            assert all(len(text) <= 700 for text in texts)
            return [[1., 0.] if 'PAYMENT' in text else [0., 1.] for text in texts]

    paragraphs = [{'id': 'P0001', 'text': 'Irrelevant.'},
                  {'id': 'P0002', 'text': 'x' * 1500 + ' PAYMENT after forty-five days.'}]
    hits = vector_matches(paragraphs, DEFAULT_SKILL, 'PAYMENT deadline?', Embeddings())
    assert hits['custom_question'][0] == 'P0002'
    assert len(hits['custom_question']) == len(set(hits['custom_question']))


def test_empty_contract_does_not_call_embedding_service():
    class NoEmbedding:
        def embed(self, texts):
            raise AssertionError('Empty contract made an embedding request')
    assert vector_matches([], {}, '', NoEmbedding()) == {}
