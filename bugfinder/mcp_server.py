from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from bugfinder.api import AuditOptions, render_report, run_audit
from bugfinder.fixer import apply_safe_fixes

SERVER_NAME = "testx-mcp"
SERVER_VERSION = "0.1.0"


def _read_message() -> dict[str, Any] | None:
    line = input()
    if not line:
        return None
    return json.loads(line)


def _write_message(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


def _ok(msg_id: Any, result: dict[str, Any]) -> None:
    _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _err(msg_id: Any, code: int, message: str) -> None:
    _write_message(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }
    )


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "scan_codebase",
            "description": "Run codebase scan and return report.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "ai_provider": {"type": "string", "enum": ["none", "openai", "claude"]},
                    "model": {"type": "string"},
                    "max_cost": {"type": "number"},
                    "output": {"type": "string", "enum": ["text", "json", "html"]},
                    "min_severity": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["path"],
            },
        },
        {
            "name": "fix_codebase",
            "description": "Apply safe fixes and return fix summary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "force": {"type": "boolean"},
                    "ai_provider": {"type": "string", "enum": ["none", "openai", "claude"]},
                    "model": {"type": "string"},
                    "max_cost": {"type": "number"},
                    "min_severity": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["path"],
            },
        },
    ]


def _handle_scan(args: dict[str, Any]) -> dict[str, Any]:
    output = args.get("output", "json")
    report = run_audit(
        args["path"],
        AuditOptions(
            ai_provider=args.get("ai_provider"),
            model=args.get("model"),
            max_cost=args.get("max_cost"),
            min_severity=args.get("min_severity"),
        ),
    )
    rendered = render_report(report, output=output)
    return {
        "content": [{"type": "text", "text": rendered}],
        "structuredContent": report.to_dict(),
    }


def _handle_fix(args: dict[str, Any]) -> dict[str, Any]:
    path = args["path"]
    report = run_audit(
        path,
        AuditOptions(
            ai_provider=args.get("ai_provider"),
            model=args.get("model"),
            max_cost=args.get("max_cost"),
            min_severity=args.get("min_severity"),
        ),
    )
    fix_report = apply_safe_fixes(
        report.issues,
        root_path=path,
        dry_run=bool(args.get("dry_run", False)),
        force=bool(args.get("force", False)),
    )
    post_report = run_audit(
        path,
        AuditOptions(
            ai_provider=args.get("ai_provider"),
            model=args.get("model"),
            max_cost=args.get("max_cost"),
            min_severity=args.get("min_severity"),
        ),
    )
    summary = {
        "total_issues": fix_report.total_issues,
        "suggested_fixes": fix_report.suggested_fixes,
        "safe_fix_candidates": fix_report.safe_fix_candidates,
        "applied_count": fix_report.applied_count,
        "skipped_count": fix_report.skipped_count,
        "actions": [
            {
                "file_path": a.file_path,
                "line": a.line,
                "description": a.description,
                "status": a.status,
                "confidence": a.confidence,
                "preview_before": a.preview_before,
                "preview_after": a.preview_after,
            }
            for a in fix_report.actions
        ],
        "remaining_issues": len(post_report.issues),
    }
    return {
        "content": [{"type": "text", "text": json.dumps(summary, indent=2)}],
        "structuredContent": summary,
    }


def main() -> None:
    while True:
        try:
            msg = _read_message()
        except EOFError:
            break
        except Exception:
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            continue

        if msg is None:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        try:
            if method == "initialize":
                _ok(
                    msg_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    },
                )
            elif method == "notifications/initialized":
                # Notification: no response required.
                continue
            elif method == "tools/list":
                _ok(msg_id, {"tools": _tool_specs()})
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if name == "scan_codebase":
                    _ok(msg_id, _handle_scan(arguments))
                elif name == "fix_codebase":
                    _ok(msg_id, _handle_fix(arguments))
                else:
                    _err(msg_id, -32601, f"Unknown tool: {name}")
            else:
                _err(msg_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # pragma: no cover
            _err(msg_id, -32000, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


if __name__ == "__main__":
    # Ensure relative paths from client resolve against server launch directory.
    Path(".").resolve()
    main()
