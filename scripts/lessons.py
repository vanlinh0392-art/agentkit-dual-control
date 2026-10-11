#!/usr/bin/env python3
"""
AgentKit Context Learning & Resilience System v2.0
- Tri thức 2 tầng (Local + Global)
- Ghi file nguyên tử (Atomic Write) chống xung đột Windows
- Tìm kiếm mờ thông minh (Fuzzy / Token-Set Search)
- Dọn rác & Khử trùng lặp tri thức (Vacuum Engine)

Usage:
    python lessons.py search --query "windows encoding crash"
    python lessons.py vacuum
    python lessons.py add --category "debug" --trigger "..." --lesson "..."
    python lessons.py list
    python lessons.py extract
"""

import difflib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Shared utilities (DRY from _shared.py) ---
try:
    from _shared import atomic_write_json, get_brain_dir, emit_metric
except ImportError:
    def emit_metric(event, **kwargs): pass

    def get_brain_dir() -> Path:
        cwd = Path.cwd()
        for parent in [cwd, *cwd.parents]:
            brain = parent / ".brain"
            if brain.is_dir(): return brain
            if parent == Path.home() or parent.parent == parent: break
        global_brain = Path.home() / ".gemini" / "antigravity" / "brain"
        if global_brain.is_dir(): return global_brain
        brain = cwd / ".brain"
        brain.mkdir(parents=True, exist_ok=True)
        return brain

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


def get_lessons_file() -> Path:
    return get_brain_dir() / "lessons.json"


def get_global_lessons_file() -> Path:
    """Đường dẫn tri thức toàn cục chia sẻ giữa các dự án."""
    global_dir = Path.home() / ".gemini" / "antigravity" / "brain"
    global_dir.mkdir(parents=True, exist_ok=True)
    return global_dir / "global_lessons.json"


DEFAULT_GLOBAL_LESSONS = [
    {
        "id": "global-001",
        "date": "2026-08-30",
        "project": "global",
        "category": "runtime",
        "trigger": "Windows console encoding UTF-8 / charmap error",
        "lesson": "Luôn thêm sys.stdout.reconfigure(encoding='utf-8') ở đầu mọi script Python trên Windows",
        "applied_count": 5
    },
    {
        "id": "global-002",
        "date": "2026-08-30",
        "project": "global",
        "category": "api",
        "trigger": "429 RESOURCE_EXHAUSTED khi dispatch nhiều subagents",
        "lesson": "Kích hoạt In-Process Fallback (Doctor Strange simulation) trong Agent chính để không làm đứt đoạn flow",
        "applied_count": 3
    },
    {
        "id": "global-003",
        "date": "2026-08-30",
        "project": "global",
        "category": "windows",
        "trigger": "PermissionError [WinError 32] khi xóa thư mục / file",
        "lesson": "Luôn gỡ cờ Readonly bằng os.chmod(stat.S_IWRITE) và retry loop 3 lần với exponential backoff",
        "applied_count": 4
    }
]


def load_lessons(include_global: bool = True) -> list:
    """Nạp tri thức 2 tầng: Local Project Tier + Global Shared Tier (Chống Cold-Start)."""
    local_file = get_lessons_file()
    global_file = get_global_lessons_file()
    
    if not global_file.exists():
        atomic_write_json(global_file, DEFAULT_GLOBAL_LESSONS)

    all_lessons = []
    seen_triggers = set()

    if local_file.exists():
        try:
            local_items = json.loads(local_file.read_text(encoding="utf-8"))
            for item in local_items:
                all_lessons.append(item)
                seen_triggers.add(item.get("trigger", "").lower().strip())
        except Exception:
            pass

    if include_global and global_file.exists():
        try:
            global_items = json.loads(global_file.read_text(encoding="utf-8"))
            for item in global_items:
                t_key = item.get("trigger", "").lower().strip()
                if t_key and t_key not in seen_triggers:
                    all_lessons.append(item)
                    seen_triggers.add(t_key)
        except Exception:
            pass

    return all_lessons


def save_lessons(lessons: list, is_global: bool = False):
    f = get_global_lessons_file() if is_global else get_lessons_file()
    atomic_write_json(f, lessons)


def add_lesson(category: str, trigger: str, lesson: str, project: str = "") -> dict:
    lessons = load_lessons(include_global=False)
    entry = {
        "id": f"lesson-{len(lessons) + 1:03d}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "project": project or Path.cwd().name,
        "category": category,
        "trigger": trigger,
        "lesson": lesson,
        "applied_count": 0,
    }
    lessons.append(entry)
    save_lessons(lessons)
    print(f"✅ Added: {entry['id']} ({category})")
    print(f"   Trigger: {trigger}")
    print(f"   Lesson:  {lesson}")
    return entry


def calculate_similarity(query: str, target_text: str) -> float:
    """
    Tính độ tương đồng chuẩn xác:
    1. Token-Level Best Match: Tìm từ khớp nhất trong target cho từng từ trong query.
    2. Substring & Prefix bonus.
    """
    q_tokens = [w for w in re.findall(r'\w+', query.lower()) if len(w) >= 2]
    t_tokens = [w for w in re.findall(r'\w+', target_text.lower()) if len(w) >= 2]

    if not q_tokens or not t_tokens:
        return 0.0

    # 1. Token-level fuzzy match: tính trung bình điểm khớp tốt nhất của từng query token
    token_scores = []
    for q_tok in q_tokens:
        best_tok_score = 0.0
        for t_tok in t_tokens:
            if q_tok == t_tok:
                best_tok_score = 1.0
                break
            elif q_tok in t_tok or t_tok in q_tok:
                best_tok_score = max(best_tok_score, 0.85)
            else:
                sim = difflib.SequenceMatcher(None, q_tok, t_tok).ratio()
                if sim > best_tok_score:
                    best_tok_score = sim
        token_scores.append(best_tok_score)

    avg_token_score = sum(token_scores) / len(token_scores) if token_scores else 0.0

    # 2. Substring bonus nếu toàn bộ query xuất hiện
    if query.lower() in target_text.lower():
        avg_token_score = max(avg_token_score, 0.9)

    return min(1.0, avg_token_score)


def fuzzy_search_lessons(query: str, threshold: float = 0.25) -> list:
    """Tìm kiếm mờ thông minh với xếp hạng điểm số (Score-Ranked Search)."""
    lessons = load_lessons(include_global=True)
    if not query.strip():
        return lessons

    scored_results = []
    for l in lessons:
        full_text = f"{l.get('trigger', '')} {l.get('lesson', '')} {l.get('category', '')}"
        score = calculate_similarity(query, full_text)
        if score >= threshold:
            scored_results.append((score, l))

    scored_results.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_results]


def search_lessons(query: str):
    matches = fuzzy_search_lessons(query)
    if not matches:
        print(f"🔍 No lessons found for: '{query}'")
        return

    print(f"🔍 Found {len(matches)} relevant lessons for '{query}':")
    for l in matches:
        print(f"\n  [{l['id']}] ({l['category']}) — applied {l['applied_count']}x")
        print(f"  Trigger: {l['trigger']}")
        print(f"  Lesson:  {l['lesson']}")


# =====================================================================
# KNOWLEDGE VACUUM & DEDUPLICATION ENGINE
# =====================================================================

def vacuum_lessons() -> int:
    """Khử trùng lặp các bài học tương đồng >= 85% và tối ưu hóa kho tri thức."""
    local_file = get_lessons_file()
    if not local_file.exists():
        print("📭 Local lessons.json not found.")
        return 0

    try:
        lessons = json.loads(local_file.read_text(encoding="utf-8"))
    except Exception:
        return 0

    initial_count = len(lessons)
    deduped = []
    
    for item in lessons:
        is_dup = False
        for kept in deduped:
            sim = difflib.SequenceMatcher(None, item["trigger"].lower(), kept["trigger"].lower()).ratio()
            if sim >= 0.85:
                # Merge duplicate: Giữ lại giải pháp dài và chi tiết hơn
                if len(item.get("lesson", "")) > len(kept.get("lesson", "")):
                    kept["lesson"] = item["lesson"]
                kept["applied_count"] = kept.get("applied_count", 0) + item.get("applied_count", 0)
                is_dup = True
                break
        if not is_dup:
            deduped.append(item)

    # Đánh lại ID chuẩn hóa
    for idx, item in enumerate(deduped, 1):
        item["id"] = f"lesson-{idx:03d}"

    save_lessons(deduped, is_global=False)
    removed = initial_count - len(deduped)
    print(f"🧹 Vacuum Complete: Deduplicated {removed} redundant lessons. ({initial_count} -> {len(deduped)})")
    return removed


def list_lessons():
    lessons = load_lessons()
    if not lessons:
        print("📭 No lessons recorded yet.")
        return

    print(f"📚 {len(lessons)} lessons:")
    print("=" * 60)
    for l in lessons:
        print(f"\n  [{l['id']}] {l['date']} | {l['category']} | applied {l['applied_count']}x")
        print(f"  Trigger: {l['trigger'][:70]}")
        print(f"  Lesson:  {l['lesson'][:70]}")


def extract_from_decisions():
    """Trích xuất lessons từ decisions có outcome='failed' hoặc 'partial' và đã có lesson_learned."""
    brain = get_brain_dir()
    decisions_dir = brain / "decisions"
    if not decisions_dir.exists():
        print("⚠️ Không tìm thấy thư mục decisions/")
        return

    existing_lessons = load_lessons()
    existing_triggers = {l["trigger"] for l in existing_lessons}
    new_count = 0

    for f in sorted(decisions_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            lesson_text = entry.get("lesson_learned")
            if not lesson_text:
                continue
            trigger = entry.get("problem", "")
            if trigger in existing_triggers:
                continue

            add_lesson(
                category=entry.get("decision_type", "unknown"),
                trigger=trigger,
                lesson=lesson_text,
            )
            existing_triggers.add(trigger)
            new_count += 1

    print(f"\n📊 Extracted {new_count} new lessons from decisions")


def parse_cli_args(argv: list) -> tuple:
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
        add_lesson(
            args.get("category", "general"),
            args.get("trigger", ""),
            args.get("lesson", ""),
            args.get("project", ""),
        )
    elif command == "extract":
        extract_from_decisions()
    elif command == "search":
        search_lessons(args.get("query", ""))
    elif command == "vacuum":
        vacuum_lessons()
    elif command == "list":
        list_lessons()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
