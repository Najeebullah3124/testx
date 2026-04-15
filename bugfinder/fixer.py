from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bugfinder.models import AnalysisIssue

MEDIUM_CONFIDENCE = "medium"
HIGH_CONFIDENCE = "high"


@dataclass(slots=True)
class FixAction:
    file_path: str
    line: int | None
    description: str
    status: str
    confidence: str | None = None
    preview_before: str | None = None
    preview_after: str | None = None


@dataclass(slots=True)
class FixReport:
    total_issues: int = 0
    suggested_fixes: int = 0
    safe_fix_candidates: int = 0
    applied_count: int = 0
    skipped_count: int = 0
    actions: list[FixAction] = field(default_factory=list)


@dataclass(slots=True)
class FixCandidate:
    file_path: str
    line: int | None
    description: str
    confidence: str
    rule: str
    before: str | None = None
    after: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _list_text_files(root: Path) -> list[Path]:
    excluded = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "dist", "build"}
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in excluded for part in p.parts):
            continue
        files.append(p)
    return files


def _remove_line_in_memory(content: str, line_number: int) -> tuple[bool, str, str | None]:
    if line_number <= 0:
        return False, content, None
    lines = content.splitlines(keepends=True)
    if line_number > len(lines):
        return False, content, None
    before = lines[line_number - 1].rstrip("\n")
    del lines[line_number - 1]
    return True, "".join(lines), before


def _replace_line_in_memory(content: str, line_number: int, replacement: str) -> tuple[bool, str, str | None]:
    if line_number <= 0:
        return False, content, None
    lines = content.splitlines(keepends=True)
    if line_number > len(lines):
        return False, content, None
    before = lines[line_number - 1].rstrip("\n")
    newline = "\n" if lines[line_number - 1].endswith("\n") else ""
    lines[line_number - 1] = replacement + newline
    return True, "".join(lines), before


def _detect_issue_based_debug_lines(issues: list[AnalysisIssue]) -> list[FixCandidate]:
    candidates: list[FixCandidate] = []
    for issue in issues:
        desc = issue.description.lower()
        if "debug logging or debugger statement found in source" not in desc:
            continue
        candidates.append(
            FixCandidate(
                file_path=issue.file_path,
                line=issue.line,
                description=issue.description,
                confidence=HIGH_CONFIDENCE,
                rule="remove-debug-line",
            )
        )
    return candidates


def detect_python_print_debug(file_path: Path, content: str) -> list[FixCandidate]:
    if file_path.suffix.lower() != ".py":
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    lines = content.splitlines()
    candidates: list[FixCandidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Name) or call.func.id != "print":
            continue
        line = getattr(node, "lineno", None)
        if not line or line > len(lines):
            continue
        before = lines[line - 1]
        candidates.append(
            FixCandidate(
                file_path=str(file_path),
                line=line,
                description="Debug print() statement found in Python source.",
                confidence=HIGH_CONFIDENCE,
                rule="remove-python-print",
                before=before,
                after="",
            )
        )
    return candidates


def detect_bare_except(file_path: Path, content: str) -> list[FixCandidate]:
    if file_path.suffix.lower() != ".py":
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    lines = content.splitlines()
    candidates: list[FixCandidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is not None:
            continue
        line = getattr(node, "lineno", None)
        if not line or line > len(lines):
            continue
        source_line = lines[line - 1]
        if "except:" not in source_line:
            continue
        replacement = source_line.replace("except:", "except Exception:", 1)
        candidates.append(
            FixCandidate(
                file_path=str(file_path),
                line=line,
                description="Bare except clause can hide unexpected failures.",
                confidence=HIGH_CONFIDENCE,
                rule="replace-bare-except",
                before=source_line,
                after=replacement,
            )
        )
    return candidates


def detect_unused_imports(file_path: Path, content: str) -> list[FixCandidate]:
    if file_path.suffix.lower() != ".py":
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)

    lines = content.splitlines()
    candidates: list[FixCandidate] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if getattr(node, "lineno", 0) != getattr(node, "end_lineno", 0):
            continue
        if any(alias.name == "*" for alias in node.names):
            continue

        imported_names = [alias.asname or alias.name.split(".")[0] for alias in node.names]
        if any(name in used_names for name in imported_names):
            continue
        line = getattr(node, "lineno", None)
        if not line or line > len(lines):
            continue
        candidates.append(
            FixCandidate(
                file_path=str(file_path),
                line=line,
                description="Unused import detected (single-file inference).",
                confidence=MEDIUM_CONFIDENCE,
                rule="remove-unused-import",
                before=lines[line - 1],
                after="",
            )
        )
    return candidates


def detect_whitespace_and_newline(file_path: Path, content: str) -> list[FixCandidate]:
    candidates: list[FixCandidate] = []
    lines = content.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.rstrip(" \t")
        if stripped != line:
            candidates.append(
                FixCandidate(
                    file_path=str(file_path),
                    line=idx,
                    description="Trailing whitespace found.",
                    confidence=HIGH_CONFIDENCE,
                    rule="trim-trailing-whitespace",
                    before=line,
                    after=stripped,
                )
            )
    if content and not content.endswith("\n"):
        before_preview = content[-40:] if len(content) > 40 else content
        after_preview = (content + "\n")[-40:] if len(content) > 40 else content + "\n"
        candidates.append(
            FixCandidate(
                file_path=str(file_path),
                line=None,
                description="Missing newline at end of file.",
                confidence=HIGH_CONFIDENCE,
                rule="append-newline",
                before=before_preview,
                after=after_preview,
            )
        )
    return candidates


def _apply_candidate(content: str, candidate: FixCandidate) -> tuple[bool, str]:
    if candidate.rule in {"remove-debug-line", "remove-python-print", "remove-unused-import"}:
        if candidate.line is None:
            return False, content
        ok, new_content, before = _remove_line_in_memory(content, candidate.line)
        if ok and candidate.before is None:
            candidate.before = before
        return ok, new_content
    if candidate.rule in {"replace-bare-except", "trim-trailing-whitespace"}:
        if candidate.line is None or candidate.after is None:
            return False, content
        ok, new_content, _ = _replace_line_in_memory(content, candidate.line, candidate.after)
        return ok, new_content
    if candidate.rule == "append-newline":
        if content.endswith("\n"):
            return False, content
        return True, content + "\n"
    return False, content


def _resolve_path(root: Path, file_path: str) -> Path:
    path = Path(file_path)
    if path.is_absolute():
        return path
    return root / path


def _gather_detector_candidates(root: Path) -> list[FixCandidate]:
    candidates: list[FixCandidate] = []
    for file_path in _list_text_files(root):
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        candidates.extend(detect_whitespace_and_newline(file_path, content))
        candidates.extend(detect_python_print_debug(file_path, content))
        candidates.extend(detect_bare_except(file_path, content))
        candidates.extend(detect_unused_imports(file_path, content))
    return candidates


def _candidate_key(candidate: FixCandidate) -> tuple[str, int | None, str]:
    return (candidate.file_path, candidate.line, candidate.rule)


def apply_safe_fixes(
    issues: list[AnalysisIssue],
    root_path: str,
    dry_run: bool = False,
    force: bool = False,
) -> FixReport:
    report = FixReport(total_issues=len(issues))
    root = Path(root_path).resolve()
    issue_candidates = _detect_issue_based_debug_lines(issues)
    detector_candidates = _gather_detector_candidates(root)
    all_candidates = issue_candidates + detector_candidates

    deduped: list[FixCandidate] = []
    seen: set[tuple[str, int | None, str]] = set()
    for candidate in all_candidates:
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    report.suggested_fixes = len(deduped)
    report.safe_fix_candidates = len(deduped)

    grouped: dict[Path, list[FixCandidate]] = {}
    for candidate in deduped:
        path = _resolve_path(root, candidate.file_path)
        grouped.setdefault(path, []).append(candidate)

    for file_path, candidates in grouped.items():
        if not file_path.exists():
            for candidate in candidates:
                report.skipped_count += 1
                report.actions.append(
                    FixAction(
                        file_path=candidate.file_path,
                        line=candidate.line,
                        description=candidate.description,
                        status="skipped-not-found",
                        confidence=candidate.confidence,
                    )
                )
            continue

        content = file_path.read_text(encoding="utf-8", errors="ignore")
        candidates_sorted = sorted(candidates, key=lambda c: (c.line is None, -(c.line or 0)))
        for candidate in candidates_sorted:
            if candidate.confidence == MEDIUM_CONFIDENCE and not force:
                report.skipped_count += 1
                report.actions.append(
                    FixAction(
                        file_path=candidate.file_path,
                        line=candidate.line,
                        description=candidate.description,
                        status="skipped-medium-confidence",
                        confidence=candidate.confidence,
                        preview_before=candidate.before,
                        preview_after=candidate.after,
                    )
                )
                continue

            if dry_run:
                report.actions.append(
                    FixAction(
                        file_path=candidate.file_path,
                        line=candidate.line,
                        description=candidate.description,
                        status="planned",
                        confidence=candidate.confidence,
                        preview_before=candidate.before,
                        preview_after=candidate.after,
                    )
                )
                continue

            ok, new_content = _apply_candidate(content, candidate)
            if ok:
                content = new_content
                report.applied_count += 1
                report.actions.append(
                    FixAction(
                        file_path=candidate.file_path,
                        line=candidate.line,
                        description=candidate.description,
                        status="applied",
                        confidence=candidate.confidence,
                        preview_before=candidate.before,
                        preview_after=candidate.after,
                    )
                )
            else:
                report.skipped_count += 1
                report.actions.append(
                    FixAction(
                        file_path=candidate.file_path,
                        line=candidate.line,
                        description=candidate.description,
                        status="skipped-not-applicable",
                        confidence=candidate.confidence,
                        preview_before=candidate.before,
                        preview_after=candidate.after,
                    )
                )

        if not dry_run:
            file_path.write_text(content, encoding="utf-8")

    for issue in issues:
        if issue.fix:
            continue
        report.skipped_count += 1
        report.actions.append(
            FixAction(
                file_path=issue.file_path,
                line=issue.line,
                description=issue.description,
                status="skipped-human-review",
            )
        )

    return report
