# DocuBot Model Card

This model card is a short reflection on the DocuBot system, filled out after implementing retrieval and experimenting with all three modes:

1. Naive LLM over full docs
2. Retrieval only
3. RAG (retrieval plus LLM)

---

## 1. System Overview

**What is DocuBot trying to do?**
DocuBot answers developer questions about a small project documentation set (the `docs/` folder). Its goal is to give answers that are *grounded in those specific docs* rather than in the model's general knowledge, and to refuse when the docs do not contain enough evidence to answer. It exists to show why retrieval matters for keeping an LLM honest.

**What inputs does DocuBot take?**
- A natural-language user question
- The `.md` / `.txt` files in the `docs/` folder (loaded and split into paragraph chunks at startup)
- Environment variables (`GEMINI_API_KEY` to enable the LLM modes)

**What outputs does DocuBot produce?**
- **Mode 1 (Naive):** a free-form LLM answer generated from the question alone (no docs used)
- **Mode 2 (Retrieval only):** the raw retrieved snippets, labeled by source filename
- **Mode 3 (RAG):** an LLM answer written using only the retrieved snippets, with a file citation, or an explicit "I do not know" refusal

---

## 2. Retrieval Design

**How does your retrieval system work?**

- **Index:** Each document is split into paragraph-sized chunks on blank lines (chunks under 20 characters are dropped so lone headings don't pollute results). An inverted index maps each lowercase word to the set of chunks that contain it. Tokenization is a simple lowercase whitespace split.
- **Scoring:** `score_document` counts how many *meaningful* query words appear in a chunk. Stopwords (`the`, `how`, `does`, ...) are excluded, so a match on function words alone never looks like evidence.
- **Top snippets:** `retrieve` unions all chunks containing any query word, keeps only those scoring at or above `MIN_EVIDENCE_SCORE` (1), sorts by score descending, and returns the top 3 as `(filename, chunk)` pairs.

**What tradeoffs did you make?**
- **Simplicity over understanding:** it's a fast, transparent, dependency-free lexical matcher — but it only matches *exact* words. No stemming (`default` ≠ `defaults`), no synonyms (`lifetime` vs `expires`), no semantic similarity.
- **Precision guardrail over recall:** dropping stopwords and requiring a meaningful match reduces false hits, but it also means a real answer can be missed if the question and the docs use different wording.
- **Occurrence-based scoring:** a chunk that repeats a common word can outrank a more relevant chunk, and the ranking has no notion of which words are important (no TF-IDF).

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**
- **Naive LLM mode:** calls Gemini with the bare question. It **ignores the docs entirely** (the corpus text is passed in but discarded in the prompt), so the answer comes purely from the model's training.
- **Retrieval only mode:** never calls the LLM. It returns raw snippets from the search logic.
- **RAG mode:** first runs retrieval, then calls Gemini with the retrieved snippets and strict instructions to answer only from them.

**What instructions do you give the LLM to keep it grounded?**
The RAG prompt tells the model to: use only the information in the snippets; not invent functions, endpoints, or config values; reply exactly "I do not know based on the docs I have." when the snippets are insufficient; and mention which files it relied on when it does answer.

---

## 4. Experiments and Comparisons

Same query wording used across all three modes.

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
|------|---------------------------------|--------------------------------------|---------------------------|-------|
| What function generates access tokens? | **Harmful.** Confident, well-formatted, but invented (`jwt.sign`, `createCustomToken`, etc.). Never names the real function. | **Helpful.** Top snippet is the exact AUTH.md line naming `generate_access_token` in `auth_utils.py`, plus 2 less-relevant snippets. | **Best.** "`generate_access_token` in `auth_utils.py`" and cites AUTH.md. Clean and grounded. | Clearest win for RAG. Naive can't know a project-private name, so it fills the gap with plausible fiction. |
| What columns does the users table have? | **Harmful.** Listed generic Laravel/Django schemas — confident but for the wrong project. | **Harmful/misleading.** Retrieved the **projects** table, not the users table, with no signal it's the wrong one. | **Safe refusal.** "I do not know based on the docs I have." | Retrieval fetched the wrong table; RAG correctly refused rather than pass off projects columns as users columns. Right call, wrong reason. |
| What is the default token lifetime? | **Harmful.** Menu of platform defaults ("1 hour / 3600s"); sounds authoritative, coincidentally near the doc value, but not grounded in these docs. | **Unhelpful.** Returned generic token paragraphs; **missed** the chunk that says "Defaults to 3600 seconds". | **Safe refusal.** "I do not know based on the docs I have." | The answer *is* in AUTH.md but retrieval missed it: `default` ≠ `defaults`, and `lifetime` is trapped inside the token `TOKEN_LIFETIME_SECONDS`. |

**What patterns did you notice?**
- **Naive LLM looks impressive but untrustworthy** whenever the question is about *this project's specifics* (a private function name, this app's env vars, this app's default values). It produces fluent, well-structured answers about *some* system — just not ours.
- **Retrieval only is clearly better** when the exact term appears in the docs (the function-name query): it surfaces the source text verbatim with no invention. But it is hard to interpret and can silently retrieve the wrong thing (the projects table).
- **RAG is clearly better than both** when retrieval succeeds (function name) — it's concise, correct, and cited. Just as importantly, when retrieval *fails*, RAG refuses instead of hallucinating, which is safer than Naive's confident guessing.

---

## 5. Failure Cases and Guardrails

**Concrete failure cases observed:**

**Failure case 1 — Answer exists but retrieval misses it (token lifetime).**
- Question: "What is the default token lifetime?"
- What happened: AUTH.md states the token "Defaults to 3600 seconds," but retrieval never surfaced that chunk, so RAG refused.
- Why: the lexical matcher has no stemming (`default` ≠ `defaults`) and `lifetime` only exists inside the single token `TOKEN_LIFETIME_SECONDS`.
- What should have happened: retrieval should have found the environment-variable chunk and RAG should have answered "3600 seconds, from AUTH.md."

**Failure case 2 — Retrieval fetches the wrong-but-similar content (users table).**
- Question: "What columns does the users table have?"
- What happened: retrieval returned the **projects** table; RAG refused.
- Why: "columns" and "table" match the database doc generally, but the specific *users* table chunk didn't rank in the top 3; a nearly identical projects-table chunk did.
- What should have happened: retrieval should have returned the users table. The RAG refusal was the correct safe response given bad input, but the ideal outcome was the real column list.

**When should DocuBot say "I do not know based on the docs I have"?**
- When the docs genuinely do not cover the topic (no meaningful evidence retrieved).
- When the retrieved snippets are about a *related but different* thing (e.g. projects table when asked about users) and do not actually answer the question — refusing beats guessing.

**What guardrails did you implement?**
- **Evidence threshold:** `retrieve` drops chunks scoring below `MIN_EVIDENCE_SCORE`, so stopword-only matches never count as evidence.
- **Empty-evidence refusal:** both answer modes return a fixed "I do not know" message when retrieval yields nothing.
- **Prompt-level grounding:** the RAG prompt forbids inventing details and requires an explicit refusal + file citation.
- **Snippet limit:** at most `top_k=3` chunks are passed to the LLM, keeping context focused.

---

## 6. Limitations and Future Improvements

**Current limitations**
1. **Lexical-only matching.** Exact word matches only — no stemming or synonyms, so `default`/`defaults` and `lifetime`/`expires` gaps silently hide answers that exist.
2. **Weak ranking.** Scoring counts word occurrences with no importance weighting, so a near-duplicate chunk (projects table) can outrank the correct one (users table).
3. **Coarse chunking + small `top_k`.** Paragraph splitting plus a 3-snippet cap means the right passage can fall just outside the results, and answers spread across chunks may be missed.

**Future improvements**
1. **Add stemming/normalization** (or a lightweight semantic/embedding retriever) so wording differences stop hiding real answers.
2. **Better ranking** — TF-IDF or rare-word weighting so distinctive terms (`users`, `lifetime`) count more than common ones.
3. **Disambiguate similar chunks** — e.g. include section/heading context with each chunk so "users table" and "projects table" are distinguishable at retrieval time.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**
The Naive mode answers confidently about the wrong system entirely (generic framework schemas, invented function names). A developer who trusts it could wire up nonexistent functions or wrong env vars. Even RAG can mislead if retrieval fetches a similar-but-wrong snippet — and silent retrieval misses can make a documented answer look nonexistent, leading someone to reinvent or misconfigure something that was actually specified.

**What instructions would you give real developers who want to use DocuBot safely?**
- Trust RAG and Retrieval-only answers (which cite/show sources); treat Naive-mode output as an ungrounded guess.
- Always verify a claimed function name, endpoint, or config value against the cited file before acting on it.
- Treat "I do not know" as "check the docs yourself" — the answer may exist but have been missed by lexical retrieval, not be truly absent.
