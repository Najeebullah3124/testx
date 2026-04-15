from bugfinder.analyzer.hybrid_analyzer import HybridAnalyzer
from bugfinder.models import AnalysisIssue


def test_merge_issues_deduplicates() -> None:
    issues = [
        AnalysisIssue("bug", "high", "same", "a.py", 10),
        AnalysisIssue("bug", "high", "same", "a.py", 10),
        AnalysisIssue("bug", "high", "different", "a.py", 10),
    ]
    merged = HybridAnalyzer._merge_issues(issues)
    assert len(merged) == 2


def test_merge_issues_deduplicates_near_identical_descriptions() -> None:
    issues = [
        AnalysisIssue("bug", "high", "Dynamic SQL query construction detected.", "a.py", 20, source="static"),
        AnalysisIssue("bug", "high", "dynamic   sql query construction detected", "a.py", 20, source="ai:openai"),
        AnalysisIssue("bug", "high", "Dynamic SQL query construction detected`", "a.py", 20, source="ai:claude"),
    ]

    merged = HybridAnalyzer._merge_issues(issues)

    assert len(merged) == 1


def test_merge_issues_keeps_richer_fix_metadata() -> None:
    issues = [
        AnalysisIssue("security", "high", "Weak hash algorithm used.", "b.py", 9, fix=None),
        AnalysisIssue(
            "security",
            "high",
            "weak hash algorithm used",
            "b.py",
            9,
            fix="Use hashlib.sha256 or hashlib.sha512.",
        ),
    ]

    merged = HybridAnalyzer._merge_issues(issues)

    assert len(merged) == 1
    assert merged[0].fix == "Use hashlib.sha256 or hashlib.sha512."
