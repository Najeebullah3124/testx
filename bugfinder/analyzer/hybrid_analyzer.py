from __future__ import annotations

import re
from pathlib import Path

from bugfinder.ai.base import RateLimiter
from bugfinder.ai.claude_client import ClaudeClient
from bugfinder.ai.openai_client import OpenAIClient
from bugfinder.ai.prompt_builder import build_analysis_prompt
from bugfinder.analyzer.ast_analyzer import analyze_file_with_ast
from bugfinder.analyzer.generic_analyzer import analyze_generic_file
from bugfinder.cache.cache_manager import CacheManager
from bugfinder.models import AnalysisIssue, AnalysisReport
from bugfinder.scanner import chunk_source_file, scan_source_files

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


class HybridAnalyzer:
    def __init__(
        self,
        ai_provider: str = "none",
        model: str | None = None,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        max_cost: float = 10.0,
        cache_db: str = ".bugfinder_cache.sqlite3",
        rate_limit_per_minute: int = 30,
    ) -> None:
        self.ai_provider = ai_provider
        self.model = model
        self.max_cost = max_cost
        self.cache = CacheManager(db_path=cache_db)
        self.limiter = RateLimiter(calls_per_minute=rate_limit_per_minute)
        self._cost = 0.0
        self.client = None

        if ai_provider == "openai":
            if not openai_api_key:
                raise ValueError("OPENAI_API_KEY is required for --ai openai")
            self.client = OpenAIClient(api_key=openai_api_key, model=model or "gpt-4o-mini")
        elif ai_provider == "claude":
            if not anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY is required for --ai claude")
            self.client = ClaudeClient(api_key=anthropic_api_key, model=model or "claude-3-5-sonnet-latest")

    @staticmethod
    def _normalize_description(description: str) -> str:
        normalized = description.strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = re.sub(r"[`'\".]+", "", normalized)
        return normalized

    @staticmethod
    def _semantic_key(issue: AnalysisIssue) -> tuple[str, str, str, int | None, str]:
        return (
            issue.issue_type.strip().lower(),
            issue.severity.strip().lower(),
            issue.file_path,
            issue.line,
            HybridAnalyzer._normalize_description(issue.description),
        )

    @staticmethod
    def _merge_issues(issues: list[AnalysisIssue]) -> list[AnalysisIssue]:
        seen: dict[tuple[str, str, str, int | None, str], int] = {}
        merged: list[AnalysisIssue] = []
        for issue in issues:
            k = HybridAnalyzer._semantic_key(issue)
            if k in seen:
                existing = merged[seen[k]]
                # Keep the richest fix metadata when deduplicating near-identical findings.
                if not existing.fix and issue.fix:
                    existing.fix = issue.fix
                if existing.confidence is None and issue.confidence is not None:
                    existing.confidence = issue.confidence
                continue
            seen[k] = len(merged)
            merged.append(issue)
        return merged

    @staticmethod
    def _filter_issues_by_min_severity(issues: list[AnalysisIssue], min_severity: str | None) -> list[AnalysisIssue]:
        if not min_severity:
            return issues
        threshold = SEVERITY_RANK.get(min_severity.lower())
        if threshold is None:
            return issues
        filtered: list[AnalysisIssue] = []
        for issue in issues:
            level = SEVERITY_RANK.get(issue.severity.lower(), 0)
            if level >= threshold:
                filtered.append(issue)
        return filtered

    def analyze_codebase(
        self,
        root_path: str,
        exclude_dirs: set[str] | None = None,
        include_extensions: set[str] | None = None,
        min_severity: str | None = None,
    ) -> AnalysisReport:
        root = Path(root_path).resolve()
        source_files = scan_source_files(
            str(root),
            exclude_dirs=exclude_dirs,
            include_extensions=include_extensions,
        )

        all_issues: list[AnalysisIssue] = []
        chunks_analyzed = 0

        for file_path in source_files:
            if file_path.suffix.lower() == ".py":
                all_issues.extend(analyze_file_with_ast(file_path))
            else:
                all_issues.extend(analyze_generic_file(file_path))
            if self.ai_provider == "none" or self.client is None:
                continue

            for chunk in chunk_source_file(file_path):
                if self._cost >= self.max_cost:
                    break
                chunks_analyzed += 1
                cache_key = self.cache.cache_key(
                    file_path=chunk.file_path,
                    model=self.client.model,
                    provider=self.ai_provider,
                    content=chunk.content,
                )
                cached = self.cache.get(cache_key)
                if cached is not None:
                    for item in cached.get("issues", []):
                        all_issues.append(
                            AnalysisIssue(
                                issue_type=item.get("type", "bug"),
                                severity=item.get("severity", "medium"),
                                description=item.get("description", ""),
                                file_path=chunk.file_path,
                                line=item.get("line"),
                                fix=item.get("fix"),
                                source=item.get("source", f"ai:{self.ai_provider}"),
                            )
                        )
                    continue

                prompt = build_analysis_prompt(
                    file_path=chunk.file_path,
                    language=chunk.language,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    code=chunk.content,
                )
                self.limiter.wait()
                try:
                    issues, usage = self.client.analyze_code(prompt=prompt, file_path=chunk.file_path)
                except Exception as exc:  # pragma: no cover
                    all_issues.append(
                        AnalysisIssue(
                            issue_type="bug",
                            severity="low",
                            description=f"AI analysis failed: {type(exc).__name__}",
                            file_path=chunk.file_path,
                            line=chunk.start_line,
                            fix="Retry with a stable network/API configuration.",
                            source="system",
                        )
                    )
                    continue

                self._cost += usage.estimated_cost_usd
                all_issues.extend(issues)
                self.cache.set(
                    cache_key,
                    {
                        "issues": [x.to_dict() for x in issues],
                        "usage": {
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "estimated_cost_usd": usage.estimated_cost_usd,
                        },
                    },
                )

        merged = self._merge_issues(all_issues)
        merged = self._filter_issues_by_min_severity(merged, min_severity=min_severity)
        effective_model = self.client.model if self.client is not None else self.model
        return AnalysisReport(
            root_path=str(root),
            issues=merged,
            files_scanned=len(source_files),
            chunks_analyzed=chunks_analyzed,
            ai_provider=self.ai_provider,
            ai_model=effective_model,
            estimated_cost_usd=self._cost,
        )
