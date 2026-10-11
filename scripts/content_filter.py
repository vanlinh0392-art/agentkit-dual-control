#!/usr/bin/env python3
"""
content_filter.py — AgentKit Content Filter Safety & Prompt Sanitizer
Triển khai Protocol §12: Tự động phát hiện và chuyển ngữ các từ khóa nhạy cảm
khi viết Prompt cho invoke_subagent sang ngôn ngữ kỹ thuật trung lập.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# Đảm bảo UTF-8 trên Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPTS_DIR = Path(__file__).resolve().parent
VOCAB_PATH = SCRIPTS_DIR.parent / "references" / "safe_vocabulary.yaml"

try:
    from _shared import get_config, emit_metric
except ImportError:
    def get_config(section, key, default=None): return default
    def emit_metric(event, **kwargs): pass


def load_vocabulary(vocab_file: Path = VOCAB_PATH) -> list[dict]:
    """Tải bảng từ vựng chuyển ngữ an toàn từ safe_vocabulary.yaml."""
    if not vocab_file.exists():
        return []

    try:
        import yaml
        with open(vocab_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "substitutions" in data:
            return data["substitutions"]
    except ImportError:
        pass

    # Fallback YAML parser nếu chưa cài PyYAML
    substitutions = []
    current_entry = {}
    with open(vocab_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("- sensitive:"):
                if current_entry and "sensitive" in current_entry and "safe" in current_entry:
                    substitutions.append(current_entry)
                val = line.split("- sensitive:", 1)[1].strip().strip('"\'')
                current_entry = {"sensitive": val}
            elif line.startswith("sensitive:"):
                val = line.split("sensitive:", 1)[1].strip().strip('"\'')
                current_entry["sensitive"] = val
            elif line.startswith("safe:"):
                val = line.split("safe:", 1)[1].strip().strip('"\'')
                current_entry["safe"] = val
            elif line.startswith("context:"):
                val = line.split("context:", 1)[1].strip().strip('"\'')
                current_entry["context"] = val

    if current_entry and "sensitive" in current_entry and "safe" in current_entry:
        substitutions.append(current_entry)

    return substitutions


def save_vocabulary(substitutions: list[dict], vocab_file: Path = VOCAB_PATH) -> bool:
    """Lưu lại bảng từ vựng vào file safe_vocabulary.yaml."""
    try:
        content = [
            "# AgentKit Content Filter — Safe Vocabulary Table",
            "# =====================================================",
            "# Bảng chuyển ngữ động cho Protocol §12 (Content Filter Safety).",
            "# Dùng khi viết Prompt cho invoke_subagent.",
            "#",
            "substitutions:",
        ]
        for entry in substitutions:
            content.append(f'  - sensitive: "{entry.get("sensitive", "")}"')
            content.append(f'    safe: "{entry.get("safe", "")}"')
            if entry.get("context"):
                content.append(f'    context: "{entry.get("context", "")}"')
            content.append("")

        with open(vocab_file, "w", encoding="utf-8") as f:
            f.write("\n".join(content) + "\n")
        return True
    except Exception as e:
        print(f"Lỗi khi lưu vocabulary: {e}", file=sys.stderr)
        return False


def check_text(text: str, vocab_file: Path = VOCAB_PATH) -> list[dict]:
    """Quét văn bản tìm các từ nhạy cảm cần thay thế."""
    vocab = load_vocabulary(vocab_file)
    matches = []

    for item in vocab:
        sensitive = item.get("sensitive", "")
        if not sensitive:
            continue
        # Tạo regex không phân biệt hoa thường và khớp ranh giới từ nếu có thể
        pattern = re.compile(re.escape(sensitive), re.IGNORECASE)
        for m in pattern.finditer(text):
            matches.append({
                "sensitive": sensitive,
                "safe": item.get("safe", ""),
                "context": item.get("context", ""),
                "start": m.start(),
                "end": m.end(),
                "matched_text": m.group(0),
            })

    return matches


def sanitize_text(text: str, vocab_file: Path = VOCAB_PATH) -> tuple[str, list[dict]]:
    """Tự động chuyển ngữ các từ nhạy cảm trong prompt thành thuật ngữ an toàn."""
    vocab = load_vocabulary(vocab_file)
    applied = []
    sanitized = text

    # Sắp xếp từ dài nhất trước để tránh replace trùng lặp
    sorted_vocab = sorted(vocab, key=lambda x: len(x.get("sensitive", "")), reverse=True)

    for item in sorted_vocab:
        sensitive = item.get("sensitive", "")
        safe = item.get("safe", "")
        if not sensitive or not safe:
            continue
        pattern = re.compile(re.escape(sensitive), re.IGNORECASE)
        if pattern.search(sanitized):
            sanitized = pattern.sub(safe, sanitized)
            applied.append(item)

    if applied:
        emit_metric("content_filter_sanitized", count=len(applied))

    return sanitized, applied


def add_term(sensitive: str, safe: str, context: str = "", vocab_file: Path = VOCAB_PATH) -> bool:
    """Thêm một thuật ngữ mới vào bảng safe_vocabulary.yaml."""
    vocab = load_vocabulary(vocab_file)
    for item in vocab:
        if item.get("sensitive", "").lower() == sensitive.lower():
            item["safe"] = safe
            if context:
                item["context"] = context
            return save_vocabulary(vocab, vocab_file)

    vocab.append({
        "sensitive": sensitive,
        "safe": safe,
        "context": context or "general",
    })
    return save_vocabulary(vocab, vocab_file)


def main():
    parser = argparse.ArgumentParser(
        description="AgentKit Content Filter & Prompt Sanitizer (Protocol §12)"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Command: check
    check_p = subparsers.add_parser("check", help="Kiểm tra xem văn bản có chứa từ nhạy cảm không")
    check_p.add_argument("text", help="Chuỗi văn bản hoặc prompt cần kiểm tra")

    # Command: sanitize
    san_p = subparsers.add_parser("sanitize", help="Chuyển ngữ văn bản sang từ vựng an toàn")
    san_p.add_argument("text", help="Chuỗi văn bản hoặc prompt cần chuyển ngữ")

    # Command: add-term
    add_p = subparsers.add_parser("add-term", help="Thêm thuật ngữ mới vào từ điển")
    add_p.add_argument("--sensitive", required=True, help="Từ khóa nhạy cảm")
    add_p.add_argument("--safe", required=True, help="Thuật ngữ kỹ thuật an toàn thay thế")
    add_p.add_argument("--context", default="", help="Ngữ cảnh sử dụng")

    # Command: list
    subparsers.add_parser("list", help="Liệt kê danh sách các từ vựng an toàn")

    args = parser.parse_args()

    if args.command == "check":
        matches = check_text(args.text)
        if matches:
            print(f"⚠️ Phát hiện {len(matches)} thuật ngữ nhạy cảm:")
            for m in matches:
                print(f"  - '{m['matched_text']}' → Đề xuất: '{m['safe']}' (Ngữ cảnh: {m['context']})")
            sys.exit(1)
        else:
            print("✅ Không phát hiện thuật ngữ nhạy cảm. Prompt an toàn!")
            sys.exit(0)

    elif args.command == "sanitize":
        res, applied = sanitize_text(args.text)
        print(res)
        if applied:
            print(f"\n(Đã thay thế {len(applied)} thuật ngữ an toàn)", file=sys.stderr)

    elif args.command == "add-term":
        ok = add_term(args.sensitive, args.safe, args.context)
        if ok:
            print(f"✅ Đã thêm: '{args.sensitive}' → '{args.safe}'")
        else:
            print("❌ Thất bại khi thêm thuật ngữ.", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        vocab = load_vocabulary()
        print(f"=== BẢNG TỪ VỰNG AN TOÀN ({len(vocab)} mục) ===")
        for item in vocab:
            print(f"  • {item.get('sensitive', '')}  ➔  {item.get('safe', '')}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
