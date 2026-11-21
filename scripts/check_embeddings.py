"""Verify local embeddings and cross-language retrieval; never calls TACC or chat APIs."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings
from app.retrieval import vector_matches
from engine import DEFAULT_SKILL, Model


def main():
    model = Model(lambda *_: None, 'api')
    vectors = model.embed(['Payment is due in forty-five days.', '收到发票后的付款期限。'])
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
    paragraphs = [
        {'id': 'P0001', 'text': 'This agreement is governed by the laws of England.'},
        {'id': 'P0002', 'text': 'Invoices must be paid within forty-five days after receipt.'},
        {'id': 'P0003', 'text': 'Confidential information must not be disclosed.'},
    ]
    hits = vector_matches(paragraphs, DEFAULT_SKILL, '收到发票后多久付款？', model)
    assert hits['custom_question'][0] == 'P0002', hits
    print(json.dumps({'embedding_model': settings.ollama_embedding_model,
                      'dimensions': len(vectors[0]), 'cross_language_retrieval': True,
                      'top_paragraph': hits['custom_question'][0], 'tacc_called': False}))


if __name__ == '__main__':
    main()
