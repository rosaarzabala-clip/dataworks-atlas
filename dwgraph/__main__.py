"""Command line entry point: python -m dwgraph <command>."""

import argparse
import sys

from .client import DataWorksClient
from .config import load_config


def cmd_check(_args) -> int:
    """Connectivity test: list one PROD node."""
    cfg = load_config()
    client = DataWorksClient(cfg)
    result = client.call("ListNodes", {
        "ProjectId": cfg.project_id, "ProjectEnv": "PROD", "PageNumber": 1, "PageSize": 1,
    })
    err = client.error_of(result)
    if err:
        print(f"DataWorks API error: {err}", file=sys.stderr)
        return 1
    total = (result.get("Data") or {}).get("TotalCount")
    print(f"OK. PROD nodes reported by DataWorks: {total}")
    return 0


def _not_yet(name: str, phase: str):
    def run(_args) -> int:
        print(f"'{name}' is not implemented yet ({phase} in docs/03-lineage-graph-plan.md).", file=sys.stderr)
        return 2
    return run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dwgraph", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="test the connection to DataWorks").set_defaults(func=cmd_check)
    for name, phase, help_ in [
        ("extract", "Phase 1", "snapshot DataWorks nodes, edges, code and table I/O to data/snapshots/"),
        ("load", "Phase 2", "incrementally sync a snapshot into Neo4j"),
        ("classify", "Phase 3", "assign domains using domains.yaml and report new nodes"),
        ("analyze", "Phase 4", "find duplicated logic in a domain"),
    ]:
        sub.add_parser(name, help=help_).set_defaults(func=_not_yet(name, phase))
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ConnectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
