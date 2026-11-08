"""OFFLINE STUB - exists only to test that the pipeline wiring works without an API key.
Its output is meaningless and must never be reported as a result."""
import hashlib
import re


def _h(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest(), 16)


def mock_response(task: str, user: str):
    if task == "judge":
        ids = re.findall(r"- id=(\d+)", user)
        return {"candidates": [{
            "id": int(i), "hook_line": "", "hook_type": "mock", "hook_strength": 1 + _h(i + user[:50]) % 5,
            "cliffhanger_line": "", "cliffhanger_type": "mock",
            "cliff_strength": 1 + _h(i + "c" + user[:50]) % 5,
            "standalone_strength": 1 + _h(i + "s" + user[:50]) % 5,
            "why": "MOCK OUTPUT", "content_flags": [], "trim_start": None, "trim_end": None,
        } for i in ids], "extra_windows": []}
    if task == "stranger":
        return {"who": "mock", "conflict": "mock", "understandable": 2 + _h(user) % 4,
                "missing_context": "mock", "can_be_fixed_with_earlier_lines": True}
    if task == "repair":
        return {"start": None, "end": None}
    if task == "spoiler":
        return {"level": _h(user) % 3, "reason": "MOCK"}
    if task == "join":
        return {"start": None, "end": None}
    if task == "compare":
        return {"winner": "A" if _h(user) % 2 else "B", "reason": "MOCK"}
    return {}
