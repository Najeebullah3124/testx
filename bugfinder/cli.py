from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bugfinder.api import should_fail_ci
from bugfinder.analyzer.hybrid_analyzer import HybridAnalyzer
from bugfinder.config import load_config
from bugfinder.fixer import apply_safe_fixes
from bugfinder.reporters import render_html, render_json, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="testx",
        description="Analyze a codebase for bugs using static and optional AI analysis.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan a project path.")
    scan.add_argument("path", help="Project root path to scan.")
    scan.add_argument("--ai", choices=["openai", "claude", "none"], default=None, help="AI provider.")
    scan.add_argument("--model", default=None, help="Model name.")
    scan.add_argument("--max-cost", type=float, default=None, help="Max estimated AI spend in USD.")
    scan.add_argument("--output", choices=["text", "json", "html"], default="text", help="Output format.")
    scan.add_argument("--output-file", default=None, help="Write output to file.")
    scan.add_argument("--config", default=None, help="Path to .bugfinder.toml")
    scan.add_argument("--cache-db", default=".bugfinder_cache.sqlite3", help="SQLite cache database path.")
    scan.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude. Repeat for multiple values.",
    )
    scan.add_argument(
        "--include-ext",
        action="append",
        default=[],
        help="Only scan these file extensions (e.g. .py, .ts). Repeat for multiple values.",
    )
    scan.add_argument(
        "--min-severity",
        choices=["low", "medium", "high"],
        default=None,
        help="Only include issues at or above this severity in final output.",
    )
    scan.add_argument(
        "--fail-on-severity",
        choices=["low", "medium", "high"],
        default=None,
        help="Exit with status code 1 when report contains this severity or higher.",
    )
    scan.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe auto-fixes for supported issue types.",
    )
    scan.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview safe auto-fixes without modifying files.",
    )
    scan.add_argument(
        "--retest-command",
        default="pytest -q",
        help="Command to run after applying fixes (default: pytest -q).",
    )
    scan.add_argument(
        "--force",
        action="store_true",
        help="Apply medium-confidence fixes in addition to high-confidence fixes.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config(args.config)

    provider = args.ai if args.ai is not None else cfg.default_provider
    model = args.model if args.model is not None else cfg.default_model
    max_cost = args.max_cost if args.max_cost is not None else cfg.max_cost

    analyzer = HybridAnalyzer(
        ai_provider=provider,
        model=model,
        openai_api_key=cfg.openai_api_key,
        anthropic_api_key=cfg.anthropic_api_key,
        max_cost=max_cost,
        cache_db=args.cache_db,
        rate_limit_per_minute=cfg.rate_limit_per_minute,
    )
    include_extensions = {x.lower() if x.startswith(".") else f".{x.lower()}" for x in args.include_ext} or None
    exclude_dirs = set(args.exclude_dir) or None
    report = analyzer.analyze_codebase(
        args.path,
        exclude_dirs=exclude_dirs,
        include_extensions=include_extensions,
        min_severity=args.min_severity,
    )

    if args.output == "json":
        rendered = render_json(report)
    elif args.output == "html":
        rendered = render_html(report)
    else:
        rendered = render_text(report)

    if args.output_file:
        out_path = Path(args.output_file)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"Report written to {out_path}")
    else:
        print(rendered)

    if args.fix or args.dry_run:
        fix_report = apply_safe_fixes(
            report.issues,
            root_path=args.path,
            dry_run=args.dry_run,
            force=args.force,
        )
        mode = "DRY RUN" if args.dry_run else "APPLY"
        print("\n=== Auto-fix pipeline ===")
        print(f"Mode: {mode}")
        print(f"Issues detected: {fix_report.total_issues}")
        print(f"Suggested fixes available: {fix_report.suggested_fixes}")
        print(f"Safe fix candidates: {fix_report.safe_fix_candidates}")
        print(f"Applied fixes: {fix_report.applied_count}")
        print(f"Skipped for human review/other: {fix_report.skipped_count}")

        if args.fix and fix_report.applied_count > 0:
            print("\n=== Re-test ===")
            result = subprocess.run(
                args.retest_command,
                shell=True,
                cwd=Path(args.path).resolve(),
                check=False,
            )
            print(f"Re-test exit code: {result.returncode}")

            report = analyzer.analyze_codebase(
                args.path,
                exclude_dirs=exclude_dirs,
                include_extensions=include_extensions,
                min_severity=args.min_severity,
            )
            print("\n=== Remaining issues for human review ===")
            print(render_text(report))

    if should_fail_ci(report, args.fail_on_severity):
        print(
            f"Fail threshold reached: found issues with severity >= {args.fail_on_severity}.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
