import re

COMPLEX_PATTERNS = [
    r"\bnot\b",
    r"\bno\b",
    r"\bwithout\b",
    r"\bexcept\b",
    r"\bexcluding\b",
    r"\bbut\b",
    r"\bother than\b",
    r"\bbefore \d{4}\b",
    r"\bafter \d{4}\b",
    r"\b(?:19|20)?\d{2}'?s\b",  # "1990s", "90s", "'90s", "2000s", "00s" - anywhere, not just after "from the"
]

_COMPLEX_RE = re.compile("|".join(COMPLEX_PATTERNS), re.IGNORECASE)


def is_complex_query(query: str) -> bool:
    """Heuristic: does this query have negation, exclusion, or an era/date constraint that a
    plain embedding similarity search handles poorly? If so, route it through the cloud
    query parser to get a structured filter instead of relying on semantic similarity alone."""
    return bool(_COMPLEX_RE.search(query))
