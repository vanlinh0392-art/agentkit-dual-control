#!/usr/bin/env python3
"""AgentKit Metrics v1.0 — Lightweight observability for protocol activity.

Tracks gate fires, watchdog kills, content filter blocks, remediation rounds,
and subagent counts. Stores append-only JSONL in .brain/agentkit_metrics.jsonl.

Usage:
    # Record a metric event:
    python scripts/metrics.py record --event gate_block --gate 1 --detail "bare except in utils.py"
    python scripts/metrics.py record --event watchdog_kill --task-id abc123 --cause NETWORK_FREEZE
    python scripts/metrics.py record --event filter_block --word "bypass" --context "invoke_subagent prompt"
    python scripts/metrics.py record --event remediation --rounds 2 --outcome pass
    python scripts/metrics.py record --event plan_dispatch --tier T2 --subagent-count 4

    # View summary:
    python scripts/metrics.py summary                    # All time
    python scripts/metrics.py summary --last 7d          # Last 7 days
    python scripts/metrics.py summary --last 24h         # Last 24 hours

    # Export raw data:
    python scripts/metrics.py export --format json       # Pretty JSON
    python scripts/metrics.py export --format csv        # CSV for spreadsheets

Exit codes:
    0 = Success
    2 = Script error
"""
import sys, os, json, pathlib, argparse, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Resolve metrics file location
def _metrics_path() -> pathlib.Path:
    """Find .brain directory (walk up from CWD or use home)."""
    # Try CWD first
    cwd = pathlib.Path.cwd()
    brain = cwd / ".brain"
    if brain.exists():
        return brain / "agentkit_metrics.jsonl"
    # Try home
    home_brain = pathlib.Path.home() / ".brain"
    home_brain.mkdir(parents=True, exist_ok=True)
    return home_brain / "agentkit_metrics.jsonl"


def record(args):
    """Append a metric event to JSONL."""
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": args.event,
    }
    # Add optional fields based on event type
    for attr in ["gate", "detail", "task_id", "cause", "word",
                 "context", "rounds", "outcome", "tier", "subagent_count"]:
        val = getattr(args, attr, None)
        if val is not None:
            entry[attr] = val

    path = _metrics_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Recorded: {entry['event']} → {path}")


def _load_entries(last: str = None) -> list[dict]:
    """Load all entries, optionally filtered by time window."""
    path = _metrics_path()
    if not path.exists():
        return []

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if last:
        now = datetime.datetime.now(datetime.timezone.utc)
        if last.endswith("d"):
            delta = datetime.timedelta(days=int(last[:-1]))
        elif last.endswith("h"):
            delta = datetime.timedelta(hours=int(last[:-1]))
        else:
            delta = datetime.timedelta(days=int(last))
        cutoff = (now - delta).isoformat()
        entries = [e for e in entries if e.get("timestamp", "") >= cutoff]

    return entries


def summary(args):
    """Print summary statistics."""
    entries = _load_entries(args.last)

    if not entries:
        print("No metrics recorded yet.")
        return

    # Count by event type
    counts = {}
    for e in entries:
        evt = e.get("event", "unknown")
        counts[evt] = counts.get(evt, 0) + 1

    # Specific breakdowns
    gate_details = {}
    watchdog_causes = {}
    filter_words = {}
    plan_tiers = {}
    remediation_outcomes = {"pass": 0, "fail": 0}

    for e in entries:
        evt = e.get("event")
        if evt == "gate_block":
            g = str(e.get("gate", "?"))
            gate_details[g] = gate_details.get(g, 0) + 1
        elif evt == "watchdog_kill":
            c = e.get("cause", "unknown")
            watchdog_causes[c] = watchdog_causes.get(c, 0) + 1
        elif evt == "filter_block":
            w = e.get("word", "unknown")
            filter_words[w] = filter_words.get(w, 0) + 1
        elif evt == "plan_dispatch":
            t = e.get("tier", "?")
            plan_tiers[t] = plan_tiers.get(t, 0) + 1
        elif evt == "remediation":
            o = e.get("outcome", "unknown")
            if o in remediation_outcomes:
                remediation_outcomes[o] += 1

    period = f" (last {args.last})" if args.last else " (all time)"
    print(f"\n{'='*50}")
    print(f"  AgentKit Metrics Summary{period}")
    print(f"{'='*50}\n")
    print(f"  Total events: {len(entries)}\n")

    print("  Event Counts:")
    for evt, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {evt:<25s} {count:>5d}")

    if gate_details:
        print("\n  Gate Blocks by Gate #:")
        for g, c in sorted(gate_details.items()):
            print(f"    Gate {g:<5s} {c:>5d}")

    if watchdog_causes:
        print("\n  Watchdog Kill Causes:")
        for cause, c in sorted(watchdog_causes.items(), key=lambda x: -x[1]):
            print(f"    {cause:<25s} {c:>5d}")

    if filter_words:
        print("\n  Content Filter Blocked Words:")
        for w, c in sorted(filter_words.items(), key=lambda x: -x[1]):
            print(f"    {w:<25s} {c:>5d}")

    if plan_tiers:
        print("\n  Plan Dispatch by Tier:")
        for t, c in sorted(plan_tiers.items()):
            print(f"    {t:<5s} {c:>5d}")

    print(f"\n{'='*50}\n")


def export(args):
    """Export raw data as JSON or CSV."""
    entries = _load_entries()
    if not entries:
        print("No metrics to export.")
        return

    if args.format == "json":
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    elif args.format == "csv":
        # Collect all keys
        all_keys = set()
        for e in entries:
            all_keys.update(e.keys())
        keys = sorted(all_keys)
        print(",".join(keys))
        for e in entries:
            print(",".join(str(e.get(k, "")) for k in keys))


def main():
    parser = argparse.ArgumentParser(description="AgentKit Metrics v1.0")
    sub = parser.add_subparsers(dest="command")

    # record
    rec = sub.add_parser("record", help="Record a metric event")
    rec.add_argument("--event", required=True,
                     choices=["gate_block", "gate_pass", "watchdog_kill",
                              "filter_block", "remediation", "plan_dispatch",
                              "error_recovery", "cow_snapshot"])
    rec.add_argument("--gate", type=int)
    rec.add_argument("--detail")
    rec.add_argument("--task-id", dest="task_id")
    rec.add_argument("--cause")
    rec.add_argument("--word")
    rec.add_argument("--context")
    rec.add_argument("--rounds", type=int)
    rec.add_argument("--outcome")
    rec.add_argument("--tier")
    rec.add_argument("--subagent-count", dest="subagent_count", type=int)

    # summary
    s = sub.add_parser("summary", help="Show summary statistics")
    s.add_argument("--last", help="Time window: 7d, 24h, etc.")

    # export
    e = sub.add_parser("export", help="Export raw data")
    e.add_argument("--format", choices=["json", "csv"], default="json")

    args = parser.parse_args()
    if args.command == "record":
        record(args)
    elif args.command == "summary":
        summary(args)
    elif args.command == "export":
        export(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
