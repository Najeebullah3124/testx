# textx

`textx` is a hybrid codebase auditing engine for teams that need deeper bug discovery than linting alone.  
It combines deterministic static analysis with optional AI reasoning, then emits structured, actionable reports.

For a deep system breakdown, see `ARCHITECTURE.md`.

## Table of Contents

- [Why textx](#why-textx)
- [Core Capabilities](#core-capabilities)
- [Architecture at a Glance](#architecture-at-a-glance)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [CLI Reference](#cli-reference)
- [Output Model](#output-model)
- [How Analysis Works](#how-analysis-works)
- [Operational Guidance](#operational-guidance)
- [Testing](#testing)
- [Release / Publish](#release--publish)
- [Security Practices](#security-practices)
- [Roadmap](#roadmap)

## Why textx

Traditional static tools often miss contextual or cross-language risks. `textx` is designed to:

- detect high-signal bug/security/reliability risks quickly
- operate fully offline (`--ai none`) for restricted environments
- scale analysis depth with AI providers when allowed
- track cost and avoid repeated spend through chunk-level caching
- produce readable audits for engineers, leads, and reviewers

## Core Capabilities

- **Hybrid analysis pipeline**
  - Python AST rules for semantic checks
  - Generic analyzer for cross-language heuristics
  - Optional AI deep analysis over code chunks
- **One-library integration SDK**
  - importable API for embedding audits into your app/CI
  - severity gating for release policies
  - extension and directory scan controls
- **Multi-language scanning**
  - Python, JS/TS/React, Java, Go, Rust, C/C++, C#, Ruby, PHP, SQL, shell, YAML, JSON
- **Cost and throughput controls**
  - rate limiter for AI calls
  - hard cap with `--max-cost`
  - SQLite cache keyed by provider/model/file/content hash
- **Audit-focused reporting**
  - text, JSON, and HTML outputs
  - severity/type/source counts and risk hotspots
  - concrete fix guidance per issue

## Architecture at a Glance

Execution pipeline:

1. CLI parses command options and loads config.
2. Scanner enumerates supported source files.
3. Static analyzers execute per file.
4. If AI is enabled, files are chunked and analyzed with provider client.
5. Results are deduplicated and aggregated.
6. Reporter renders final output.

Primary modules:

- `bugfinder/cli.py` - command interface
- `bugfinder/config.py` - environment and TOML config merge
- `bugfinder/scanner.py` - file discovery + chunking
- `bugfinder/analyzer/ast_analyzer.py` - Python semantic checks
- `bugfinder/analyzer/generic_analyzer.py` - non-Python heuristics
- `bugfinder/analyzer/hybrid_analyzer.py` - orchestration layer
- `bugfinder/ai/*_client.py` - provider adapters
- `bugfinder/cache/cache_manager.py` - SQLite cache
- `bugfinder/reporters.py` - renderers
- `bugfinder/models.py` - issue/report data models

Detailed architecture and data flow are documented in `ARCHITECTURE.md`.

## Installation

### Runtime install

```bash
pip install textx
```

### Local development install

```bash
pip install -e ".[dev]"
```

## Quick Start

### 1) Static-only audit (fastest and offline)

```bash
textx scan . --ai none --output text
```

### 1b) Programmatic integration (all in one library)

```python
from bugfinder import AuditOptions, render_report, run_audit, should_fail_ci

report = run_audit(
    ".",
    AuditOptions(
        ai_provider="none",
        include_extensions={".py", ".ts", ".tsx"},
        exclude_dirs={"dist", "build"},
        min_severity="medium",
    ),
)

print(render_report(report, output="text"))
if should_fail_ci(report, "high"):
    raise SystemExit("High severity issues detected")
```

### 2) Deep audit with OpenAI

```bash
textx scan . \
  --ai openai \
  --model gpt-4o-mini \
  --max-cost 2.50 \
  --output html \
  --output-file audit.html
```

### 3) Deep audit with Claude

```bash
textx scan . \
  --ai claude \
  --model claude-3-5-sonnet-latest \
  --max-cost 2.50 \
  --output json \
  --output-file audit.json
```

## Configuration

Configuration resolution order:

1. Environment variables
2. `.bugfinder.toml` (or `--config` path)

Supported environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

Example `.bugfinder.toml`:

```toml
[ai]
openai_api_key = "sk-..."
anthropic_api_key = "sk-ant-..."
default_provider = "none"
default_model = "gpt-4o-mini"
max_cost = 5.0
rate_limit_per_minute = 30
```

## CLI Reference

Command:

```bash
textx scan <path> [options]
```

Options:

- `--ai`: `openai | claude | none`
- `--model`: provider model override
- `--max-cost`: max estimated AI spend in USD
- `--output`: `text | json | html`
- `--output-file`: write report to file path
- `--config`: explicit TOML config path
- `--cache-db`: SQLite cache path (default `.bugfinder_cache.sqlite3`)
- `--exclude-dir`: directory name to exclude (repeatable)
- `--include-ext`: only analyze listed extensions (repeatable)
- `--min-severity`: only include findings at or above threshold
- `--fail-on-severity`: non-zero exit for CI policy enforcement

## Output Model

Each issue includes:

- `type`: `bug | security | performance | code_smell`
- `severity`: `low | medium | high`
- `description`: actionable risk statement
- `file` and optional `line`
- `fix`: concrete remediation guidance
- `source`: `static`, `ai:<provider>`, or `system`

Report summary includes:

- total issue count
- severity/type/source distribution
- top risky files by issue density
- scan metadata (`files_scanned`, `chunks_analyzed`, provider/model, estimated cost)

## How Analysis Works

### Static analysis layer

- **AST analyzer (`.py`)**
  - dynamic execution risks (`eval`, `exec`)
  - broad exception handling (`except`, `except Exception`)
  - mutable defaults
  - unreachable code patterns
  - unsafe subprocess usage (`shell=True`)
  - unsafe YAML loading patterns
  - runtime `assert` misuse warnings

- **Generic analyzer (other languages + text patterns)**
  - TODO/FIXME/HACK risk markers
  - debug statements (`console.log`, `debugger`)
  - hardcoded secrets/tokens/password-like assignments
  - private key marker detection

### AI analysis layer (optional)

- file chunks are generated
- prompt includes language + line range context
- provider returns strict JSON
- parsed issues are normalized into shared model
- responses are cached by file content hash + provider + model

### Merge and dedup

Issues are deduplicated on type, severity, description, file path, and line.

## Operational Guidance

- Start with `--ai none` in CI for fast deterministic checks.
- Run AI scans on main branch nightly or pre-release for deeper coverage.
- Use `--max-cost` aggressively in shared environments.
- Persist report artifacts as CI artifacts, not committed source files.
- Rotate API keys immediately if accidentally exposed.

## Testing

Run unit tests:

```bash
pytest -q
```

Tests cover:

- scanner chunking behavior
- cache read/write semantics
- issue deduplication
- deep-audit regression rules (security detection + report summary structure)

## Release / Publish

```bash
python -m pip install --upgrade build twine
python -m build
twine check dist/*
twine upload --repository testpypi dist/*
twine upload dist/*
```

### Trusted Publishing with GitHub Actions (recommended)

`textx` includes `.github/workflows/publish.yml` for OIDC-based PyPI publishing.

PyPI Trusted Publisher settings:

- Project name: `textx`
- Owner: `Najeebullah3124`
- Repository: `textx`
- Workflow: `publish.yml`
- Environment: `pypi`

Workflow behavior:

- triggers automatically when a GitHub Release is published
- also supports manual run via `workflow_dispatch`
- builds wheel + sdist, then publishes via `pypa/gh-action-pypi-publish`

## Security Practices

- never commit secrets or API keys
- prefer environment variables for credentials
- keep AI optional in sensitive environments
- use dedicated CI environments for publishing permissions
- rotate credentials after any accidental exposure

## Roadmap

- incremental and diff-only scans
- pluggable custom rule packs
- SARIF output for security tooling integration
- confidence scoring calibration and false-positive suppression

