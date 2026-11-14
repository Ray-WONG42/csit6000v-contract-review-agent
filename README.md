# Automated Contract Review Agent for Auditors

CSIT6000V Advanced AI Computing and Systems — group project.

An agent that reviews English commercial contracts for corporate audit teams and accounting firms in Hong Kong. An auditor drops a contract into a monitored shared-drive folder (or uploads it on the web). The agent reviews it and emails a structured, evidence-grounded risk table. The output supports preliminary human review. It does not decide legal enforceability or statutory compliance.

Full proposal: [Contract_Review_Agent_Proposal.pdf](Contract_Review_Agent_Proposal.pdf).

## Team

| Name | Student ID |
| --- | --- |
| WANG Rui | 21322126 |
| XUE Yaosheng | 21350795 |
| JIA Hansen | 21322009 |
| ZHANG Shihan | 21317896 |

## What the prototype covers

Text-based PDF and plain-text English contracts. Each review has eight rows:

| Category | What is recorded |
| --- | --- |
| Termination | Category, whether supporting text was found, paragraph IDs, one verbatim supporting sentence, potentially missing information, and a suggested question for the counterparty |
| Liability caps | same |
| Governing law | same |
| Automatic renewal | same |
| Audit rights | same |
| Assignment restrictions | same |
| Insurance | same |
| Liquidated damages | same |

If no supporting text is found, the row states “No corresponding source text found.” That is an extraction outcome, not proof that the provision is absent.

## Intended workflow

1. A local service watches the shared folder and starts a job after a new file finishes copying. Web upload uses the same ingestion path. Content hashes skip duplicate jobs.
2. A parser assigns stable paragraph identifiers and the review is split into eight category tasks.
3. For each category, candidate paragraphs are retrieved with category descriptions, synonyms, and BM25. Only those candidates go to the course TACC endpoint (Qwen3.8-27B) for structured extraction.
4. An evidence check confirms the response format and that quotations appear in the referenced paragraphs. The agent may pull adjacent paragraphs and retry extraction once. Unresolved cases stay marked as unresolved.
5. The service stores the risk table and emails it from a dedicated test mailbox to configured university addresses.

The web app has a chat-style review view (upload, status, paragraph segmentation, highlighted evidence, risk table) and a history view that reloads stored results without calling the model again.

Model inference runs on TACC. File monitoring, storage, and email run in the local service. Endpoint details, credentials, and the model id are configuration, not source.

## Research question

Whether locating relevant paragraphs before extraction can keep or improve clause detection while cutting token use and review latency, compared with sending the full contract in one request.

## Evaluation

Held-out CUAD contracts, limited to the eight categories above. Development contracts stay separate from evaluation contracts. The baseline sends the whole contract in one request and asks for the same eight-row output, with the same model, decoding settings, schema, and validation rules.

Primary metrics: clause hit rate (token-overlap F1 threshold 0.5 against a gold span), mean span F1, per-category results, false-positive rate on negative pairs, total tokens including retries, and time from ingestion to the stored risk table.

## Out of scope

Model training, WhatsApp integration, and an external legal knowledge base.

## References

1. Hendrycks, D., Burns, C., Chen, A., and Ball, S. (2021). CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review. NeurIPS. https://github.com/The-Atticus-Project/cuad
2. The Atticus Project. CUAD category descriptions. https://github.com/The-Atticus-Project/cuad/blob/main/category_descriptions.csv
