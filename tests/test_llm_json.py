import json

from backend.llm_json import strip_code_fence


def test_strip_code_fence_removes_json_fence():
    raw = '```json\n[{"id": 1, "genre": "Rock"}]\n```'
    assert json.loads(strip_code_fence(raw)) == [{"id": 1, "genre": "Rock"}]


def test_strip_code_fence_removes_bare_fence():
    raw = '```\n{"a": 1}\n```'
    assert json.loads(strip_code_fence(raw)) == {"a": 1}


def test_strip_code_fence_leaves_plain_json_untouched():
    raw = '{"a": 1}'
    assert strip_code_fence(raw) == '{"a": 1}'
