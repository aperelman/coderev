import pytest
from datetime import datetime, timezone
from auto_pr_reviewer import (
    parse_ts,
    _annotate_diff,
    build_file_line_map,
    render_summary,
    append_failed_inline
)

def test_parse_ts():
    ts = "2026-08-14T11:38:17Z"
    dt = parse_ts(ts)
    assert dt == datetime(2026, 8, 14, 11, 38, 17, tzinfo=timezone.utc)

def test_annotate_diff():
    diff_text = "@@ -10,3 +10,4 @@\n context\n-removed\n+added\n context 2"
    expected = "@@ -10,3 +10,4 @@\n[10]  context\n[-] -removed\n[11] +added\n[12]  context 2"
    assert _annotate_diff(diff_text) == expected

def test_build_file_line_map():
    diff_text = "@@ -10,3 +10,4 @@\n context\n-removed\n+added\n context 2"
    files = [{'path': 'test.py', 'diff': diff_text}]
    line_map = build_file_line_map(files)
    
    # 10 is unchanged, maps to 10
    # 11 is added, maps to None
    # 12 is unchanged, maps to 12
    assert line_map['test.py'][10] == 10
    assert line_map['test.py'][11] is None
    assert line_map['test.py'][12] == 12

def test_render_summary():
    reviewer = "sergioram"
    review = {
        "verdict": "APPROVE",
        "summary": "Looks good."
    }
    expected = "## ✅ Sergio Ramos (@sergioram) — Code Review\n\nLooks good.\n"
    assert render_summary(reviewer, review) == expected

def test_render_summary_reject():
    reviewer = "sergioram"
    review = {
        "verdict": "REJECT",
        "summary": "Terrible code."
    }
    expected = "## ❌ Sergio Ramos (@sergioram) — Code Review\n\nTerrible code.\n"
    assert render_summary(reviewer, review) == expected

def test_append_failed_inline():
    summary = "Summary note"
    failed_inline = [
        {"file": "test.py", "line": 10, "comment": "Fix this."}
    ]
    expected = "Summary note\n---\n### Comments (could not be posted inline)\n\n**`test.py` line 10:** Fix this.\n\n"
    assert append_failed_inline(summary, failed_inline) == expected

def test_append_failed_inline_empty():
    summary = "Summary note"
    assert append_failed_inline(summary, []) == summary
