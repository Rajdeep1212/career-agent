# Semantic matching for Career Agent — design note (3 Oct 2026)

> Status: design input for SEM1 (docs/ROADMAP_QUEUE.md). One point below is superseded by a later
> decision recorded in docs/RUNBOOK.md and docs/eval/env_check.md: no PyTorch. The embedding backend is
> FastEmbed on ONNX Runtime with BAAI/bge-small-en-v1.5, not sentence-transformers with EmbeddingGemma.
> The rest of the note stands as written.

Rajdeep's idea: embed the CV and the job descriptions, pull keywords from both with an LLM, do
semantic search instead of fixed role prompts, and rank by similarity. Verdict: right direction,
with three corrections. Evidence and plan below.

## 1. Corrections

**a. The embedding model must run locally.**
CLAUDE.md: hosted models never see the CV file or text extracted from it. A hosted embedding API
would break that rule — an embedding request sends the raw text. Use a local model in the job-agent
env via sentence-transformers:
- EmbeddingGemma (308M, Google, Sept 2025): built for on-device, 768-dim with Matryoshka truncation
  to 512/256/128, ~2k token context, strong MTEB for its size. Apache-style Gemma licence.
- Alternatives: bge-small-en-v1.5 (33M, very fast), e5-base-v2, all-MiniLM-L6-v2 (fastest, weakest).
At ~2,500 indexed jobs, vectors fit in memory; numpy dot products or sqlite-vec are enough. No pgvector.

**b. Keep BM25. Hybrid beats either side alone.**
"Semantic Candidate–Job Matching" (arXiv 2609.23307, 2026, staffing-industry data) runs exactly the
proposed pipeline: LLM-normalise both sides into structured English, dense vectors + BM25 keyword
ranking, Reciprocal Rank Fusion, then geography and business-rule filters in SQL. Their largest single
gain came from the structured normalisation before embedding, not from swapping embedding models
(EmbeddingGemma base beat the MPNet baseline 88% vs 78% of candidates above the LLM-judge relevance
threshold; domain fine-tuning added only +1.7 points at the strictest threshold). Fine-tuning also
inflated the model's own similarity scores without a matching relevance gain — a warning against
trusting a self-reported similarity number.

**c. Similarity is not eligibility.**
Cosine similarity ranks a "Senior AI Engineer, 8+ years" as highly similar to a fresher CV: same words,
wrong job. Gates run first (freshness, graduation year, experience, seniority, location), then ranking.
Embeddings replace the ranker, never the gates.

## 2. The ladder

1. Gate: freshness rule, then three-way eligibility. Excluded jobs never reach retrieval.
2. Normalise: requirement cards for jobs (Q10) and the target profile for the candidate — both into the
   same structured shape (role family, must-have skills, nice-to-have, seniority, location, evidence).
3. Retrieve: BM25 over the card text + dense vectors over the card text, fused with RRF.
4. Rerank: a cross-encoder over the top ~50 only (bge-reranker-base or similar, local). Reranking is the
   biggest single accuracy lever in 2026 RAG write-ups, and it is too slow to run over everything.
5. Explain: quote the sentence of the job description that produced each matched requirement.
6. Score: keep fit / reachable fit / chance band. The similarity number is an input, never the headline.

## 3. "No need to name a role"

CareerBERT (Expert Systems with Applications, 2025; arXiv 2503.02056) is the published version of this
idea: SBERT over jobGBERT, resumes and ESCO occupations in one shared embedding space, job centroids
built by averaging real EURES advert embeddings and blending them with ESCO descriptions. MRR@100 0.328
vs 0.302 for ada-002; human evaluation MAP@20 0.711, MRR@20 0.861 over 5 resumes and 10 HR experts.
It recommends ESCO occupations, not adverts — which is exactly the "the system tells me which roles I
fit" behaviour, and it fits the ESCO normalisation already planned in M3.
Limits to copy into the design: cold start on short CVs, adjacent-role confusion (Data Scientist vs
Data Warehouse Developer), German-market training, and the authors' own framing as decision support.
So: suggest role families, let the user override, and always show why.

## 4. Google's new RAG

"Agentic RAG" on the Gemini Enterprise Agent Platform (Google Research blog, 5 June 2026). Not a new
index type — a change of control flow: an Orchestrator, a Planner that routes to the right corpus, a
Query Rewriter, a Search Fanout Agent that issues several searches at once, and a Sufficient Context
Agent that inspects the retrieved snippets and the draft answer, names what is missing, and triggers
more searches. Reported 90.1% accuracy on FramesQA cross-corpus (824 multi-hop queries, 2,676 docs) and
up to 34% better factuality than single-shot RAG.
It is a Google Cloud product, so not something to adopt. The transferable patterns, all free:
- query fan-out: expand one CV into several searches rather than one role string;
- a sufficiency check: if the shortlist is thin or the evidence weak, search again with different terms
  instead of returning a weak list;
- verification before answering: this is already the Verifier agent in the M4 plan.

## 5. Order of work

Nothing above can be judged without labels, so Q3 stays next. Then:
- Q11 (JD keyword engine) gives the vocabulary for the cards.
- Q10 (requirement cards + ESCO) gives the structured text both sides get embedded from.
- New item, after labels exist: local embedding index + hybrid retrieval + reranker, measured against the
  current heuristic ranker on the labelled set with NDCG@10 and Recall@20. Ship only if it wins.

## Sources
- Semantic Candidate–Job Matching, arXiv 2609.23307 (2026)
- CareerBERT, arXiv 2503.02056 / Expert Systems with Applications (2025)
- EmbeddingGemma, Google DeepMind / Hugging Face (Sept 2025)
- Agentic RAG, Google Research blog, 5 June 2026
