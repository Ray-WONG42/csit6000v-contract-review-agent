"""Build a per-contract FAISS index and return stable source paragraph IDs."""
from engine import custom_definitions, scope_plan


def vector_matches(paragraphs, skill, instruction, model):
    import faiss
    import numpy as np

    if not paragraphs:
        return {}

    definitions = custom_definitions(skill, instruction)
    categories, clarify = scope_plan(skill, instruction, model, definitions)
    if clarify or not categories:
        return {}

    def encode(texts):
        vectors = np.array(model.embed(texts), dtype=np.float32, order="C")
        if vectors.ndim != 2 or vectors.shape[0] != len(texts) or not vectors.shape[1]:
            raise ValueError("向量形状不正确。")
        norms = np.linalg.norm(vectors, axis=1)
        if not np.all(np.isfinite(norms) & (norms > 0)):
            raise ValueError("向量包含无效数据。")
        faiss.normalize_L2(vectors)
        return vectors

    queries = encode([". ".join(definitions[key][1]) for key in categories])
    index = faiss.IndexFlatIP(queries.shape[1])
    # Bound embedding input independently of the review model's context window.
    # Every fragment maps back to the unchanged source paragraph for quotation checks.
    fragments = [(p["id"], p["text"][offset:offset + 700])
                 for p in paragraphs for offset in range(0, len(p["text"]), 600)]
    if not fragments:
        return {}
    batch_size = 16
    for start in range(0, len(fragments), batch_size):
        vectors = encode([text for _, text in fragments[start:start + batch_size]])
        if vectors.shape[1] != index.d:
            raise ValueError("查询向量与合同段落向量的维度不一致。")
        index.add(vectors)

    _, matches = index.search(queries, min(16, len(fragments)))
    return {key: list(dict.fromkeys(fragments[i][0] for i in indexes if i >= 0))[:4]
            for key, indexes in zip(categories, matches)}
