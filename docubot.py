"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob

# --- Retrieval guardrail configuration -----------------------------------
# Refusal message returned when there is no meaningful evidence to answer.
NO_ANSWER = "I do not know based on these docs."

# Minimum number of meaningful (non-stopword) query-word matches a chunk
# must have to count as evidence. Raise this to make DocuBot stricter.
MIN_EVIDENCE_SCORE = 1

# Common function words that carry no topical meaning. Excluded from scoring
# so a query like "how does the ... work" can't match on noise alone.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "at", "to", "of",
    "for", "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "how", "what", "when", "where", "why", "which", "who", "this",
    "that", "these", "those", "with", "as", "by", "it", "its", "from", "can",
    "will", "i", "you", "we", "they", "my", "our",
}
# -------------------------------------------------------------------------

class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Split documents into paragraph-sized chunks (the retrieval unit)
        self.chunks = self.build_chunks(self.documents)  # List of (filename, chunk_text)

        # Build a retrieval index over chunks (implemented in Phase 1)
        self.index = self.build_index(self.chunks)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    def build_chunks(self, documents):
        """
        Split each document into paragraph-sized chunks on blank lines.

        This is what turns retrieval from "return the whole file" into
        "return the relevant passage." Each chunk keeps its source filename
        so provenance survives.

        Returns a list of tuples: (filename, chunk_text)
        """
        MIN_CHUNK_LEN = 20  # drop lone headings / tiny fragments
        chunks = []
        for filename, text in documents:
            for para in text.split("\n\n"):
                para = para.strip()
                if len(para) >= MIN_CHUNK_LEN:
                    chunks.append((filename, para))
        return chunks

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, chunks):
        """
        Build a tiny inverted index mapping lowercase words to the chunks
        they appear in. Chunks are identified by their position in
        self.chunks, so a token points at specific passages, not whole files.

        Example structure:
        {
            "token": {3, 17},      # chunk indices
            "database": {42}
        }

        Keep this simple: split on whitespace, lowercase tokens.
        """
        index = {}
        for i, (_, text) in enumerate(chunks):
            for token in set(text.lower().split()):   # set() -> list each chunk once per word
                index.setdefault(token, set()).add(i)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        TODO (Phase 1):
        Return a simple relevance score for how well the text matches the query.

        Suggested baseline:
        - Convert query into lowercase words
        - Count how many appear in the text
        - Return the count as the score
        """
        # Only meaningful query words count -- stopwords are ignored so a
        # match on "the"/"how"/"does" alone never looks like real evidence.
        query_tokens = {
            t for t in query.lower().split() if t not in STOPWORDS
        }
        doc_tokens = text.lower().split()
        return sum(1 for t in doc_tokens if t in query_tokens)

    def retrieve(self, query, top_k=3):
        """
        TODO (Phase 1):
        Use the index and scoring function to select top_k relevant document snippets.

        Return a list of (filename, text) sorted by score descending.
        """
        query_tokens = query.lower().split()

        # Index as filter: chunks containing ANY query word.
        candidates = set()
        for t in query_tokens:
            candidates.update(self.index.get(t, set()))

        # Score candidate chunks, keeping only those with meaningful evidence.
        # A chunk that matched only on stopwords scores 0 and is dropped here,
        # so retrieve() never returns non-evidence to the answer layer.
        scored = [
            (self.chunks[i][0], self.chunks[i][1],
             self.score_document(query, self.chunks[i][1]))
            for i in candidates
        ]
        scored = [row for row in scored if row[2] >= MIN_EVIDENCE_SCORE]

        scored.sort(key=lambda row: row[2], reverse=True)
        return [(filename, text) for filename, text, _ in scored[:top_k]]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return NO_ANSWER

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return NO_ANSWER

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
