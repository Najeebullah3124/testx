from pathlib import Path

from bugfinder.fixer import apply_safe_fixes
from bugfinder.models import AnalysisIssue


def test_apply_safe_fixes_removes_debug_line(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.js"
    file_path.write_text("const a = 1;\nconsole.log(a);\nconst b = 2;\n", encoding="utf-8")
    issues = [
        AnalysisIssue(
            issue_type="code_smell",
            severity="low",
            description="Debug logging or debugger statement found in source.",
            file_path=str(file_path),
            line=2,
            fix="Remove debug statements from production code paths.",
            source="static",
        )
    ]

    report = apply_safe_fixes(issues, root_path=str(tmp_path), dry_run=False)
    content = file_path.read_text(encoding="utf-8")

    assert report.applied_count == 1
    assert "console.log" not in content


def test_apply_safe_fixes_dry_run_makes_no_changes(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.js"
    original = "const a = 1;\nconsole.log(a);\n"
    file_path.write_text(original, encoding="utf-8")
    issues = [
        AnalysisIssue(
            issue_type="code_smell",
            severity="low",
            description="Debug logging or debugger statement found in source.",
            file_path=str(file_path),
            line=2,
            fix="Remove debug statements from production code paths.",
            source="static",
        )
    ]

    report = apply_safe_fixes(issues, root_path=str(tmp_path), dry_run=True)

    assert report.applied_count == 0
    assert report.safe_fix_candidates == 1
    assert file_path.read_text(encoding="utf-8") == original


def test_python_print_statements_are_removed(tmp_path: Path) -> None:
    file_path = tmp_path / "debug.py"
    file_path.write_text(
        "print('top level debug')\n"
        "def run():\n"
        "    print('inside function debug')\n"
        "    return 1\n",
        encoding="utf-8",
    )

    report = apply_safe_fixes([], root_path=str(tmp_path), dry_run=False)
    content = file_path.read_text(encoding="utf-8")

    assert report.applied_count >= 2
    assert "print(" not in content
    assert "return 1" in content


def test_python_print_dry_run_makes_no_changes(tmp_path: Path) -> None:
    file_path = tmp_path / "debug.py"
    original = "def run():\n    print('debug')\n    return 1\n"
    file_path.write_text(original, encoding="utf-8")

    report = apply_safe_fixes([], root_path=str(tmp_path), dry_run=True)

    assert report.applied_count == 0
    assert any(a.status == "planned" for a in report.actions)
    assert file_path.read_text(encoding="utf-8") == original


def test_bare_except_replaced_with_exception(tmp_path: Path) -> None:
    file_path = tmp_path / "except_case.py"
    file_path.write_text(
        "def run():\n"
        "    try:\n"
        "        x = 1 / 0\n"
        "    except:\n"
        "        return 0\n",
        encoding="utf-8",
    )

    apply_safe_fixes([], root_path=str(tmp_path), dry_run=False)
    content = file_path.read_text(encoding="utf-8")

    assert "except Exception:" in content
    assert "except:\n" not in content


def test_bare_except_dry_run_no_changes(tmp_path: Path) -> None:
    file_path = tmp_path / "except_case.py"
    original = "try:\n    x = 1\nexcept:\n    pass\n"
    file_path.write_text(original, encoding="utf-8")

    report = apply_safe_fixes([], root_path=str(tmp_path), dry_run=True)

    assert any("Bare except clause" in a.description and a.status == "planned" for a in report.actions)
    assert file_path.read_text(encoding="utf-8") == original


def test_unused_import_requires_force(tmp_path: Path) -> None:
    file_path = tmp_path / "imports.py"
    file_path.write_text("import os\nvalue = 1\n", encoding="utf-8")

    report = apply_safe_fixes([], root_path=str(tmp_path), dry_run=False, force=False)
    content = file_path.read_text(encoding="utf-8")

    assert "import os" in content
    assert any(a.status == "skipped-medium-confidence" for a in report.actions)


def test_unused_import_removed_with_force(tmp_path: Path) -> None:
    file_path = tmp_path / "imports.py"
    file_path.write_text("import os\nvalue = 1\n", encoding="utf-8")

    report = apply_safe_fixes([], root_path=str(tmp_path), dry_run=False, force=True)
    content = file_path.read_text(encoding="utf-8")

    assert report.applied_count >= 1
    assert "import os" not in content


def test_whitespace_and_newline_fixes(tmp_path: Path) -> None:
    file_path = tmp_path / "cleanme.py"
    file_path.write_text("x = 1    \ny = 2\t", encoding="utf-8")

    apply_safe_fixes([], root_path=str(tmp_path), dry_run=False)
    content = file_path.read_text(encoding="utf-8")

    assert content.endswith("\n")
    assert "    \n" not in content
    assert "\t\n" not in content


def test_whitespace_and_newline_dry_run_preview(tmp_path: Path) -> None:
    file_path = tmp_path / "cleanme.py"
    original = "x = 1    \nlast_line"
    file_path.write_text(original, encoding="utf-8")

    report = apply_safe_fixes([], root_path=str(tmp_path), dry_run=True)

    assert any(a.preview_before is not None and a.preview_after is not None for a in report.actions)
    assert file_path.read_text(encoding="utf-8") == original
