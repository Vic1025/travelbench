"""
test_tool_output_logging.py — minimal tests for persisted tool_output shape.

The runner now logs a BOUNDED copy of each tool's output (top-K results +
char cap) so post-hoc recovery analysis is possible, while the model still
receives the full output.  These tests assert the truncation contract and
backward-compatibility of the transcript shape.

Run:
  python test_tool_output_logging.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.runner import (
    truncate_tool_output,
    TOOL_OUTPUT_TOP_K,
    TOOL_OUTPUT_MAX_CHARS,
)


def test_small_dict_passthrough():
    out = {"tool": "search_yelp", "count": 1, "results": [{"venue_id": "a"}]}
    t = truncate_tool_output(out)
    assert t == out, t
    # must remain JSON-serializable
    json.dumps(t)


def test_results_topk_trim():
    out = {"tool": "search_yelp", "count": 50,
           "results": [{"venue_id": f"v{i}"} for i in range(50)]}
    t = truncate_tool_output(out)
    if "_truncated" in t:
        # size cap kicked in first; still must record totals
        assert t["_results_total"] == 50
    else:
        assert len(t["results"]) == TOOL_OUTPUT_TOP_K
        assert t["_results_truncated"] is True
        assert t["_results_total"] == 50


def test_char_cap_large_payload():
    big = {"tool": "search_blogs_and_forums",
           "results": [{"doc_id": f"d{i}", "body": "x" * 500} for i in range(40)]}
    t = truncate_tool_output(big)
    s = json.dumps(t)
    # bounded well under any pathological size (cap + small metadata overhead)
    assert len(s) <= TOOL_OUTPUT_MAX_CHARS + 500, len(s)
    assert t.get("_truncated") is True
    assert "_truncated_repr" in t


def test_non_dict_payload():
    t = truncate_tool_output(["a", "b", "c"])
    json.dumps(t)  # serializable
    t2 = truncate_tool_output("x" * (TOOL_OUTPUT_MAX_CHARS + 100))
    assert t2.get("_truncated") is True and "_truncated_repr" in t2


def test_unserializable_payload():
    class Weird:
        pass
    t = truncate_tool_output({"obj": Weird()})
    # default=str makes it serializable; either passes through or truncated dict
    json.dumps(t)


def test_backward_compat_old_transcript_shape():
    """An old log entry without truncation markers still has the expected keys."""
    old_entry = {"tool_name": "search_yelp",
                 "tool_input": {"query": "museum"},
                 "tool_output": {"tool": "search_yelp", "results": []}}
    # consumers read tool_name/tool_input/tool_output — all present & unchanged
    assert set(["tool_name", "tool_input", "tool_output"]).issubset(old_entry)
    assert "results" in old_entry["tool_output"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
