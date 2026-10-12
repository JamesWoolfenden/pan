import re

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def strip_code_fence(text: str) -> str:
    """Claude sometimes wraps JSON responses in a ```json ... ``` code fence despite being
    asked not to. Strip it so json.loads() doesn't choke on the fence markers."""
    return _CODE_FENCE_RE.sub("", text.strip()).strip()
