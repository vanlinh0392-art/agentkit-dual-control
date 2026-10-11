#!/usr/bin/env python3
"""
task_watchdog.py — Antigravity Task Watchdog & Log Diagnostic Engine
Phát hiện, phân tích log và chẩn đoán nguyên nhân treo của các task script/process.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

# Đảm bảo UTF-8 trên Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from _shared import get_config, emit_metric
except ImportError:
    def get_config(section, key, default=None): return default
    def emit_metric(event, **kwargs): pass

HANG_PATTERNS = [
    {
        "category": "WAITING_INPUT",
        "regex": r"(?i)(Are you sure|Proceed\?|\[y/N\]|\[Y/n\]|\(y/n\)|password:|Enter your username|Do you want to continue\?|Terminate batch job|Press any key)",
        "description": "Tien trinh dang tam dung de cho input tu nguoi dung (Interactive Prompt).",
        "action": "Dung manage_task(send_input='y\\n') hoac kill va them co silent (-y, --yes, --batch, -NonInteractive).",
    },
    {
        "category": "NETWORK_FREEZE",
        "regex": r"(?i)(Connecting to|Resolving host|Retrying \(\d+\/\d+\)|Downloading \.\.\. \d+B/\?|Waiting for headers|Socket timeout|ETIMEDOUT)",
        "description": "Tien trinh bi do ket noi mang hoac server khong phan hoi.",
        "action": "Kill task, bo sung timeout cung (--max-time, --timeout) hoac doi mirror/proxy.",
    },
    {
        "category": "FILE_LOCK_OR_DEADLOCK",
        "regex": r"(?i)(Waiting for file lock|acquire lock|lock held by pid|Resource deadlock avoided|database is locked)",
        "description": "Tien trinh bi nghen do tranh chap tai nguyen hoac khoa file tren Windows.",
        "action": "Kill task, thu hoi file lock bang cach giai phong tien trinh cu, sau do retry.",
    },
    {
        "category": "PACKAGE_MANAGER_PROMPT",
        "regex": r"(?i)(npm WARN|Need to install the following packages|Ok to proceed\?|Would you like to run)",
        "description": "Trinh quan ly goi (npm/npx/pip) dang hoi xac nhan cai dat.",
        "action": "Dung manage_task(send_input='y\\n') hoac kill va them co --yes/--force.",
    },
]


def analyze_log_file(log_path: str, tail_lines: int = 100) -> dict:
    path = Path(log_path)
    if not path.exists():
        return {
            "status": "NOT_FOUND",
            "message": f"Log file khong ton tai: {log_path}",
        }

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {
            "status": "READ_ERROR",
            "message": f"Khong the doc log file: {e}",
        }

    total_lines = len(lines)
    tail = lines[-tail_lines:] if total_lines > tail_lines else lines
    tail_text = "".join(tail)

    findings = []
    for pattern in HANG_PATTERNS:
        match = re.search(pattern["regex"], tail_text)
        if match:
            findings.append({
                "category": pattern["category"],
                "matched_text": match.group(0),
                "description": pattern["description"],
                "recommended_action": pattern["action"],
            })

    mtime = path.stat().st_mtime
    seconds_idle = time.time() - mtime
    silence_threshold = get_config("watchdog", "progress_silence_seconds", 300)

    result = {
        "status": "ANALYZED",
        "log_path": str(path.resolve()),
        "total_lines": total_lines,
        "seconds_since_last_write": int(seconds_idle),
        "is_silent": seconds_idle > silence_threshold,
        "findings": findings,
        "last_lines": [line.strip() for line in tail[-10:] if line.strip()],
    }

    # Emit metrics for observability
    if findings:
        for f in findings:
            emit_metric("watchdog_finding", category=f["category"], log_path=str(path))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Task Watchdog & Log Diagnostic Engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_p = subparsers.add_parser("analyze", help="Phan tich file log cua mot task")
    analyze_p.add_argument("--log", required=True, help="Duong dan toi file task.log")
    analyze_p.add_argument("--lines", type=int, default=100, help="So dong cuoi can quet")

    args = parser.parse_args()

    if args.command == "analyze":
        result = analyze_log_file(args.log, args.lines)
        print("\n" + "=" * 70)
        print(f"[WATCHDOG LOG ANALYSIS] {result.get('log_path', args.log)}")
        print("=" * 70)
        print(f"* Tong so dong: {result.get('total_lines', 0)}")
        print(f"* Thoi gian im lang (Idle): {result.get('seconds_since_last_write', 0)}s")
        print(f"* Trang thai Silent (>5m): {'[CANH BAO] CO (Nghi ngo treo)' if result.get('is_silent') else '[OK] Khong (Van hoat dong)'}")

        findings = result.get("findings", [])
        if findings:
            print(f"\n[PHAT HIEN DAU HIEU NGHEN] ({len(findings)} diem):")
            for idx, f in enumerate(findings, 1):
                print(f"  [{idx}] {f['category']}: {f['description']}")
                print(f"      Matched: '{f['matched_text']}'")
                print(f"      -> Xu ly: {f['recommended_action']}")
        else:
            print("\n[OK] Khong phat hien mau Interactive Prompt hoac Network Timeout ro rang.")

        last_lines = result.get("last_lines", [])
        if last_lines:
            print("\n[10 DONG LOG CUOI CUNG]:")
            for l in last_lines:
                print(f"   | {l}")
        print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
