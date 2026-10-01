from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from . import __version__
from .core import compile_plugin, inspect, validate_plugin


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compile skills into Codex plugins and evaluate measured improvements.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("inspect", help="Read skills and report resources/dependencies without executing them")
    i.add_argument("sources", nargs="+")
    b = sub.add_parser("build", help="Build a new package or a separate update candidate")
    b.add_argument("sources", nargs="+")
    b.add_argument("--output", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--description")
    b.add_argument("--plugin-version", default="0.1.0")
    b.add_argument("--existing")
    b.add_argument("--resource-root", action="append", default=[])
    v = sub.add_parser("validate", help="Validate package structure, references and basic MCP configuration")
    v.add_argument("plugin")
    l = sub.add_parser("learn", help="Explicitly manage local feedback, evaluations and verified promotions")
    ls = l.add_subparsers(dest="operation", required=True)
    for op in ("record", "status", "evaluate", "promote", "rollback"):
        a = ls.add_parser(op)
        a.add_argument("--project", required=True, help="Project holding baseline/candidate dirs and .skill-to-plugin state")
        if op == "record":
            a.add_argument("--event", required=True, help="JSON event file; feedback remains local")
        if op in {"evaluate", "promote", "rollback"}:
            a.add_argument("--baseline", required=True)
        if op in {"evaluate", "promote"}:
            a.add_argument("--candidate", required=True)
        if op == "evaluate":
            a.add_argument("--suite", required=True)
            a.add_argument("--allow-exec", action="store_true", help="Authorize the trusted suite's subprocesses; no shell/sandbox implied")
        if op == "promote":
            a.add_argument("--evaluation-id", required=True)
        if op == "rollback":
            a.add_argument("--promotion-id", required=True)
    sub.add_parser("mcp", help="Run newline JSON-RPC MCP stdio server")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "mcp":
        from .mcp import serve
        return serve()
    try:
        if args.command == "inspect":
            result = inspect(args.sources)
        elif args.command == "build":
            result = compile_plugin(args.sources, args.output, args.name, description=args.description,
                                    version=args.plugin_version, existing=args.existing, resource_roots=args.resource_root)
        elif args.command == "validate":
            result = validate_plugin(args.plugin)
        else:
            from . import learning
            store = Path(args.project).expanduser() / ".skill-to-plugin"
            if args.operation == "record":
                result = learning.record_event(store, json.loads(Path(args.event).read_text(encoding="utf-8")))
            elif args.operation == "status":
                result = {"proposedLessons": learning.lessons(store)}
            elif args.operation == "evaluate":
                result = learning.evaluate(Path(args.baseline), Path(args.candidate), Path(args.suite), store, args.allow_exec)
            elif args.operation == "promote":
                validation = validate_plugin(args.candidate)
                if not validation["valid"]:
                    raise ValueError("Candidate package invalid: " + "; ".join(validation["errors"]))
                result = learning.promote(Path(args.baseline), Path(args.candidate), store, args.evaluation_id)
            else:
                result = learning.rollback(Path(args.baseline), store, args.promotion_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if isinstance(result, dict) and result.get("valid") is False else 0
    except (ValueError, OSError, TypeError, KeyError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2
