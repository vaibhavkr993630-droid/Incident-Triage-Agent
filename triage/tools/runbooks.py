"""search_runbooks: find the team's guidance for a hypothesis, with BM25 over runbook sections.

Why BM25 (lexical ranking) and not embeddings:
- The corpus is about a dozen short documents. There is nothing for semantic search to scale.
- Queries are full of exact technical terms: metric names (db_pool_checked_out), log
  messages ("payment provider timed out"), status codes, exception names. Lexical matching
  is strong on exactly these, and an embedding model can blur them.
- No model download, no API quota, deterministic results, trivial to test.
For a large or prose-heavy corpus the next step would be hybrid search (BM25 + embeddings).

Chunking: one chunk per "## " section. Each chunk is prefixed with its runbook's title, so a
section like "How to confirm" stays findable even though its heading says nothing specific.
Results return the best section of each of the top runbooks.

Runbooks advise; they do not decide. The agent still confirms everything with telemetry.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from triage.tools.base import Tool, ToolContext, fit_list

RUNBOOK_DIR = Path(__file__).resolve().parents[2] / "runbooks"
MAX_SECTION_CHARS = 1500
K1, B = 1.5, 0.75  # standard BM25 parameters
TITLE_WEIGHT = 2  # title words count twice: the title says what a runbook is about

_STOPWORD_TEXT = """
a an and are as at be but by can do does for from has have if in into is it its may not of
on or that the their then there these this to was were what when where which while who will
with you your than so no all any each
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())
_WORD = re.compile(r"[a-z0-9][a-z0-9_]*")


def tokenize(text: str) -> list[str]:
    """Lowercase words; snake_case names also yield their parts; a light plural strip."""
    tokens = []
    for word in _WORD.findall(text.lower()):
        parts = [word] + ([p for p in word.split("_") if p] if "_" in word else [])
        for t in parts:
            if t in STOPWORDS or len(t) < 2:
                continue
            if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
                t = t[:-1]
            tokens.append(t)
    return tokens


@dataclass(frozen=True)
class Section:
    runbook: str  # file name, e.g. payment-provider.md
    title: str
    heading: str
    text: str
    tokens: tuple[str, ...]


def split_sections(path: Path) -> list[Section]:
    """One Section per "## " heading. Text before the first heading (owner, scope) is
    metadata, not searchable content."""
    lines = path.read_text().splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), path.stem)
    title_tokens = tokenize(title) * TITLE_WEIGHT
    chunks: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("## "):
            chunks.append((line[3:].strip(), []))
        elif chunks and not line.startswith("# "):
            chunks[-1][1].append(line)
    sections = []
    for heading, body in chunks:
        text = "\n".join(body).strip()
        if text:
            tokens = title_tokens + tokenize(f"{heading}\n{text}")
            sections.append(Section(path.name, title, heading, text, tuple(tokens)))
    return sections


def load_sections(directory: Path = RUNBOOK_DIR) -> list[Section]:
    return [s for path in sorted(directory.glob("*.md")) for s in split_sections(path)]


class BM25Index:
    def __init__(self, sections: list[Section]) -> None:
        self.sections = sections
        self.counts = [Counter(s.tokens) for s in sections]
        self.avg_len = sum(len(s.tokens) for s in sections) / max(1, len(sections))
        df = Counter(t for c in self.counts for t in c)
        n = len(sections)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str) -> list[tuple[float, Section]]:
        terms = tokenize(query)
        scored = []
        for section, counts in zip(self.sections, self.counts, strict=True):
            length_norm = K1 * (1 - B + B * len(section.tokens) / self.avg_len)
            total = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    total += self.idf[term] * tf * (K1 + 1) / (tf + length_norm)
            if total > 0:
                scored.append((total, section))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    def search(self, query: str, top_k: int = 3) -> list[tuple[float, Section]]:
        """Best section of each of the top_k runbooks."""
        best: dict[str, tuple[float, Section]] = {}
        for score, section in self.score(query):
            best.setdefault(section.runbook, (score, section))
        return sorted(best.values(), key=lambda pair: pair[0], reverse=True)[:top_k]


class SearchRunbooksArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=3,
        max_length=300,
        description='What you suspect or observed, e.g. "payment provider timed out 504" or '
        '"db_pool_checked_out at pool size".',
    )
    top_k: int = Field(default=3, ge=1, le=5, description="How many runbooks to return.")


class SearchRunbooks(Tool):
    name = "search_runbooks"
    description = (
        "Search the team's runbooks (how to confirm and handle known kinds of failures). "
        "Returns the most relevant section of each matching runbook. Use it once you have "
        "a hypothesis, to learn how to confirm it. Runbooks advise; telemetry decides."
    )
    Args = SearchRunbooksArgs

    def __init__(self, ctx: ToolContext, directory: Path = RUNBOOK_DIR) -> None:
        super().__init__(ctx)
        self.index = BM25Index(load_sections(directory))

    def run(self, args: SearchRunbooksArgs) -> dict:
        results = []
        for score, s in self.index.search(args.query, args.top_k):
            text = s.text
            if len(text) > MAX_SECTION_CHARS:
                text = text[:MAX_SECTION_CHARS] + "..."
            results.append(
                {
                    "runbook": s.runbook,
                    "title": s.title,
                    "section": s.heading,
                    "score": round(score, 2),
                    "text": text,
                }
            )
        payload = {"query": args.query, "results": results}
        if not results:
            payload["note"] = "no runbook matched; rephrase with symptoms or metric names"
        return fit_list(payload, "results")
