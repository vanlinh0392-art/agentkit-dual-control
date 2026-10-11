#!/usr/bin/env python3
"""
AgentKit Decision Log Engine v1.0
Ghi và đọc Decision Log cho AgentKit Dual-Control.

Usage (từ AI agent hoặc CLI):
    # Thêm decision
    python decision_log.py add \
        --type "debug" \
        --problem "PIL ImageGrab fails headless" \
        --solution "Fallback PowerShell .NET CopyFromScreen" \
        --confidence "high" \
        --framing "Screenshot trên Windows headless không có GPU context" \
        --adversarial "PowerShell fallback có thể chậm hơn 2-3x" \
        --verification "pass"

    # Cập nhật outcome
    python decision_log.py update --id "dec-20260830-001" --outcome "success" --lesson "Luôn có dual fallback cho platform-specific API"

    # Xem stats
    python decision_log.py stats

    # Xem gần nhất
    python decision_log.py recent --count 5
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

import time
import uuid

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Shared utilities (DRY from _shared.py) ---
try:
    from _shared import atomic_write_json, atomic_append_jsonl, get_brain_subdir, emit_metric
    def get_log_dir() -> Path:
        return get_brain_subdir("decisions")
except ImportError:
    # Standalone fallback — inline definitions
    def emit_metric(event, **kwargs): pass

    def atomic_write_json(file_path, data, retries=3, backoff=0.05):
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = file_path.parent / f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            content = json.dumps(data, ensure_ascii=False, indent=2)
            temp_path.write_text(content, encoding="utf-8")
            for attempt in range(retries):
                try:
                    os.replace(temp_path, file_path)
                    return True
                except PermissionError:
                    if attempt < retries - 1: time.sleep(backoff * (2 ** attempt))
                    else: raise
        except Exception as e:
            if temp_path.exists():
                try: temp_path.unlink()
                except Exception: pass
            raise e
        return False

    def atomic_append_jsonl(file_path, lines, retries=3, backoff=0.05):
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        text_chunk = "\n".join(lines) + "\n"
        for attempt in range(retries):
            try:
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(text_chunk)
                return True
            except PermissionError:
                if attempt < retries - 1: time.sleep(backoff * (2 ** attempt))
                else: raise
        return False

    def get_log_dir():
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            brain = parent / ".brain"
            if brain.is_dir():
                d = brain / "decisions"
                d.mkdir(parents=True, exist_ok=True)
                return d
            if parent == Path.home() or parent.parent == parent: break
        global_brain = Path.home() / ".gemini" / "antigravity" / "brain"
        if global_brain.is_dir():
            d = global_brain / "decisions"
            d.mkdir(parents=True, exist_ok=True)
            return d
        d = cwd / ".brain" / "decisions"
        d.mkdir(parents=True, exist_ok=True)
        return d


# --- Config ---
LOG_DIR_NAME = ".brain/decisions"


def generate_id(log_dir: Path) -> str:
    """Tạo ID duy nhất chống xung đột khi nhiều subagent cùng ghi: dec-YYYYMMDD-HHMMSS-xxxx."""
    today = datetime.now().strftime("%Y%m%d")
    unique_suffix = f"{datetime.now().strftime('%H%M%S')}_{os.urandom(2).hex()}"
    return f"dec-{today}-{unique_suffix}"


def add_decision(args: dict) -> dict:
    """Thêm 1 decision entry mới."""
    log_dir = get_log_dir()
    entry = {
        "id": generate_id(log_dir),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision_type": args.get("type", "unknown"),
        "problem": args.get("problem", ""),
        "solution_chosen": args.get("solution", ""),
        "alternatives_considered": [a.strip() for a in args.get("alternatives", "").split("|") if a.strip()],
        "confidence": args.get("confidence", "medium"),
        "reasoning_layers": {
            "problem_framing": args.get("framing", ""),
            "self_adversarial": args.get("adversarial", ""),
            "pre_delivery": args.get("verification", "pass"),
        },
        "outcome": "pending",
        "lesson_learned": None,
    }

    today = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"{today}.jsonl"
    atomic_append_jsonl(log_file, [json.dumps(entry, ensure_ascii=False)])
    export_session_summary(log_dir)

    return entry


def batch_add_decisions(items: list) -> list:
    """Ghi nhiều decisions vào log trong 1 lần I/O duy nhất (Session-End Flush)."""
    log_dir = get_log_dir()
    today = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"{today}.jsonl"
    
    # Tính base counter
    count = 0
    if log_file.exists():
        count = sum(1 for _ in log_file.open("r", encoding="utf-8") if _.strip())

    added_entries = []
    lines_to_write = []

    for i, item in enumerate(items):
        count += 1
        entry = {
            "id": f"dec-{today}-{count:03d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_type": item.get("type", item.get("decision_type", "unknown")),
            "problem": item.get("problem", ""),
            "solution_chosen": item.get("solution", item.get("solution_chosen", "")),
            "alternatives_considered": item.get("alternatives", item.get("alternatives_considered", [])),
            "confidence": item.get("confidence", "medium"),
            "reasoning_layers": item.get("reasoning_layers", {
                "problem_framing": item.get("framing", ""),
                "self_adversarial": item.get("adversarial", ""),
                "pre_delivery": item.get("verification", "pass"),
            }),
            "outcome": item.get("outcome", "pending"),
            "lesson_learned": item.get("lesson_learned", None),
        }
        added_entries.append(entry)
        lines_to_write.append(json.dumps(entry, ensure_ascii=False))

    if lines_to_write:
        atomic_append_jsonl(log_file, lines_to_write)
        export_session_summary(log_dir)

    return added_entries


def export_session_summary(log_dir: Path):
    """Xuất file .brain/session_summary.json máy đọc được (W8 Structured Metadata Bundle)."""
    try:
        brain_dir = log_dir.parent
        summary_file = brain_dir / "session_summary.json"
        
        all_entries = []
        for f in sorted(log_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        all_entries.append(json.loads(line))
                    except Exception:
                        pass

        by_type = {}
        by_confidence = {}
        for e in all_entries:
            t = e.get("decision_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            c = e.get("confidence", "medium")
            by_confidence[c] = by_confidence.get(c, 0) + 1

        summary_data = {
            "version": "3.2.0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "workspace": str(Path.cwd()),
            "metrics": {
                "total_decisions": len(all_entries),
                "by_type": by_type,
                "by_confidence": by_confidence
            },
            "recent_decisions": [
                {
                    "id": e.get("id"),
                    "type": e.get("decision_type"),
                    "problem": e.get("problem"),
                    "solution": e.get("solution_chosen"),
                    "confidence": e.get("confidence")
                }
                for e in all_entries[-5:]
            ]
        }
        atomic_write_json(summary_file, summary_data)
    except Exception:
        pass


def update_decision(decision_id: str, outcome: str, lesson: str = None) -> bool:
    """Cập nhật outcome và lesson cho 1 decision."""
    log_dir = get_log_dir()
    # Extract date from ID: dec-YYYYMMDD-NNN
    parts = decision_id.split("-")
    if len(parts) < 3:
        print(f"Error: Invalid ID format: {decision_id}")
        return False

    date_str = parts[1]
    log_file = log_dir / f"{date_str}.jsonl"
    if not log_file.exists():
        print(f"Error: Log file not found: {log_file}")
        return False

    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    updated = False
    new_lines = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("id") == decision_id:
            entry["outcome"] = outcome
            if lesson:
                entry["lesson_learned"] = lesson
            updated = True
        new_lines.append(json.dumps(entry, ensure_ascii=False))

    if updated:
        log_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"Updated: {decision_id} → outcome={outcome}")
    else:
        print(f"Not found: {decision_id}")
    return updated


def show_stats():
    """Hiển thị thống kê tổng quan."""
    log_dir = get_log_dir()
    total = 0
    by_type = {}
    by_confidence = {"high": 0, "medium": 0, "low": 0}
    by_outcome = {"pending": 0, "success": 0, "partial": 0, "failed": 0}
    lessons_count = 0

    for f in sorted(log_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            total += 1
            dt = entry.get("decision_type", "unknown")
            by_type[dt] = by_type.get(dt, 0) + 1
            conf = entry.get("confidence", "medium")
            by_confidence[conf] = by_confidence.get(conf, 0) + 1
            out = entry.get("outcome", "pending")
            by_outcome[out] = by_outcome.get(out, 0) + 1
            if entry.get("lesson_learned"):
                lessons_count += 1

    print(f"📊 Decision Log Stats")
    print(f"{'='*40}")
    print(f"Total decisions: {total}")
    print(f"\nBy Type:")
    for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"\nBy Confidence:")
    for k, v in by_confidence.items():
        pct = f"{v/total*100:.0f}%" if total else "0%"
        print(f"  {k}: {v} ({pct})")
    print(f"\nBy Outcome:")
    for k, v in by_outcome.items():
        print(f"  {k}: {v}")
    print(f"\nLessons learned: {lessons_count}")


def show_recent(count: int = 5):
    """Hiển thị N decisions gần nhất."""
    log_dir = get_log_dir()
    entries = []
    for f in sorted(log_dir.glob("*.jsonl"), reverse=True):
        for line in reversed(f.read_text(encoding="utf-8").strip().split("\n")):
            if not line.strip():
                continue
            entries.append(json.loads(line))
            if len(entries) >= count:
                break
        if len(entries) >= count:
            break

    print(f"📋 Last {len(entries)} decisions:")
    print(f"{'='*60}")
    for e in entries:
        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(e.get("confidence", ""), "⚪")
        out_icon = {"success": "✅", "failed": "❌", "partial": "⚠️", "pending": "⏳"}.get(e.get("outcome", ""), "❓")
        print(f"\n{e['id']} | {e['decision_type']} | {conf_icon} {e['confidence']} | {out_icon} {e['outcome']}")
        print(f"  Problem:  {e['problem'][:80]}")
        print(f"  Solution: {e['solution_chosen'][:80]}")
        if e.get("lesson_learned"):
            print(f"  Lesson:   {e['lesson_learned'][:80]}")


def parse_cli_args(argv: list) -> tuple:
    """Parse CLI args đơn giản (không dùng argparse để giữ minimal)."""
    if len(argv) < 2:
        return "help", {}

    command = argv[1]
    args = {}
    i = 2
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                args[key] = argv[i + 1]
                i += 2
            else:
                args[key] = "true"
                i += 1
        else:
            i += 1
    return command, args


def main():
    command, args = parse_cli_args(sys.argv)

    if command == "add":
        entry = add_decision(args)
        print(f"✅ Logged: {entry['id']} ({entry['decision_type']}, {entry['confidence']})")
    elif command in ("batch", "flush"):
        data_str = args.get("data", "")
        file_path = args.get("file", "")
        items = []
        if file_path and Path(file_path).exists():
            try:
                items = json.loads(Path(file_path).read_text(encoding="utf-8"))
            except Exception as e:
                print(f"❌ Error reading file {file_path}: {e}")
        elif data_str:
            try:
                items = json.loads(data_str)
            except Exception:
                # Handle potential shell unescaping
                try:
                    cleaned = data_str.replace('\\"', '"').replace("'", '"')
                    items = json.loads(cleaned)
                except Exception as e:
                    print(f"❌ Error parsing JSON data: {e}")
        elif not sys.stdin.isatty():
            try:
                stdin_data = sys.stdin.read().strip()
                if stdin_data:
                    items = json.loads(stdin_data)
            except Exception as e:
                print(f"❌ Error reading from stdin: {e}")

        if isinstance(items, list) and items:
            added = batch_add_decisions(items)
            print(f"✅ Batch logged {len(added)} decisions ({added[0]['id']} -> {added[-1]['id']})")
        else:
            print(f"⚠️ No valid decision items provided for batch/flush.")
    elif command == "update":
        update_decision(args.get("id", ""), args.get("outcome", "pending"), args.get("lesson"))
    elif command == "stats":
        show_stats()
    elif command == "recent":
        show_recent(int(args.get("count", "5")))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
