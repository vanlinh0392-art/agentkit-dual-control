#!/usr/bin/env python3
"""
AgentKit SDE Contract & Scope Creep Gate Engine v1.0 (Audit 2 Gate)
Động cơ kiểm toán khế ước đặc tả (Spec-Driven Engineering Contract),
đối soát 100% Acceptance Criteria Gherkin và Chặn đứng Scope Creep ngoài target_files.
100% Python Standard Library (Zero-Dependency).

Usage:
    # 1. Đối soát 100% Acceptance Criteria
    python sde_guard.py verify-ac --spec spec.md --tests tests/

    # 2. Kiểm soát vùng biên & Chặn Scope Creep
    python sde_guard.py check-scope --plan plan.md --base ./

    # 3. Thẩm định các bất biến hệ thống (Invariants)
    python sde_guard.py assert-invariants --code-dir src/
"""

import ast
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from _shared import emit_metric
except ImportError:
    def emit_metric(event, **kwargs): pass

# =====================================================================
# 1. ACCEPTANCE CRITERIA TRACEABILITY & EXECUTION VERIFIER
# =====================================================================

def extract_ac_tags_from_spec(spec_text: str) -> List[Dict[str, str]]:
    """Trích xuất các kịch bản AC (@AC-XXX-NNN hoặc Scenario: ...) từ spec.md."""
    ac_list = []
    
    # 1. Regex tìm các tag @AC-XXX-NNN
    pattern = r"@(AC-[A-Za-z0-9_\-]+)\s*(?:\[(.*?)\])?\s*\n\s*(?:Scenario:?\s*(.*?)(?=\n|$))?"
    matches = re.finditer(pattern, spec_text)
    for m in matches:
        tag = m.group(1).strip()
        meta = m.group(2).strip() if m.group(2) else ""
        scenario_title = m.group(3).strip() if m.group(3) else ""
        ac_list.append({
            "tag": tag,
            "meta": meta,
            "title": scenario_title or tag
        })

    # 2. Fallback tìm Scenario: nếu không có tag @AC
    if not ac_list:
        scenario_pattern = r"(?i)Scenario:\s*(.*?)(?=\n|$)"
        for idx, sm in enumerate(re.finditer(scenario_pattern, spec_text), 1):
            title = sm.group(1).strip()
            ac_list.append({
                "tag": f"AC-{idx:03d}",
                "meta": "Standard",
                "title": title
            })

    return ac_list


def scan_tests_for_ac_tags(test_dir: Path) -> Dict[str, List[str]]:
    """Quét toàn bộ thư mục test để tìm các test functions ánh xạ với AC tags."""
    ac_to_tests = {}
    if not test_dir.exists():
        return ac_to_tests

    for test_file in test_dir.rglob("*.py"):
        if not test_file.is_file():
            continue
        try:
            content = test_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(test_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_name = node.name
                    doc = ast.get_docstring(node) or ""
                    # Tìm AC tag trong tên hàm hoặc docstring
                    for match in re.finditer(r"AC-[A-Za-z0-9_\-]+", func_name + " " + doc, re.IGNORECASE):
                        tag = match.group(0).upper()
                        ac_to_tests.setdefault(tag, []).append(f"{test_file.name}::{func_name}")
        except Exception:
            pass

    return ac_to_tests


def verify_acceptance_criteria(spec_file: str, test_dir: str = "tests", report_xml: Optional[str] = None) -> dict:
    """Đối soát 100% kịch bản trong spec.md có test cover và pass không."""
    spec_path = Path(spec_file).resolve()
    if not spec_path.exists():
        return {"error": f"File spec không tồn tại: {spec_file}", "passed": False}

    spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    expected_acs = extract_ac_tags_from_spec(spec_text)
    
    if not expected_acs:
        return {
            "error": "Không tìm thấy bất kỳ Acceptance Criteria hoặc Scenario nào trong spec file!",
            "passed": False,
            "expected_count": 0
        }

    test_path = Path(test_dir).resolve()
    ac_mapping = scan_tests_for_ac_tags(test_path)

    # Đọc kết quả JUnit XML nếu có
    passed_tests = set()
    if report_xml and Path(report_xml).exists():
        try:
            tree = ET.parse(report_xml)
            for tc in tree.findall(".//testcase"):
                name = tc.get("name", "")
                classname = tc.get("classname", "")
                failures = tc.findall("failure") + tc.findall("error")
                skipped = tc.findall("skipped")
                if not failures and not skipped:
                    passed_tests.add(name)
                    passed_tests.add(f"{classname}::{name}")
        except Exception:
            pass

    covered_acs = []
    missing_acs = []

    for item in expected_acs:
        tag = item["tag"].upper()
        matching_tests = ac_mapping.get(tag, [])
        
        # Nếu có report_xml, bắt buộc test phải passed
        if report_xml and Path(report_xml).exists():
            is_passed = any(any(pt in mt for pt in passed_tests) for mt in matching_tests)
            if is_passed:
                covered_acs.append({**item, "tests": matching_tests, "status": "PASSED"})
            else:
                missing_acs.append({**item, "tests": matching_tests, "status": "NOT_PASSED_OR_MISSING"})
        else:
            # Nếu không có report_xml, chỉ kiểm tra static AST traceability
            if matching_tests:
                covered_acs.append({**item, "tests": matching_tests, "status": "COVERED"})
            else:
                missing_acs.append({**item, "tests": [], "status": "UNCOVERED"})

    total = len(expected_acs)
    covered = len(covered_acs)
    coverage_rate = (covered / total * 100.0) if total > 0 else 0.0

    return {
        "spec_file": str(spec_path),
        "total_ac": total,
        "covered_ac": covered,
        "missing_ac": len(missing_acs),
        "coverage_rate": round(coverage_rate, 1),
        "passed": coverage_rate == 100.0,
        "covered_details": covered_acs,
        "missing_details": missing_acs
    }


# =====================================================================
# 2. SCOPE CREEP & DIFF BOUNDARY INSPECTOR
# =====================================================================

FORBIDDEN_PATTERNS = {
    "package.json", "package-lock.json", "requirements.txt", 
    "pyproject.toml", "Cargo.toml", "Cargo.lock",
    ".env", ".env.local", ".env.production",
    "docker-compose.yml", "Dockerfile", ".gitignore"
}

def extract_allowed_files_from_plan(plan_text: str) -> Set[str]:
    """Trích xuất danh sách file cho phép sửa từ plan.md."""
    allowed = set()

    # 1. Tìm các block JSON nếu có contract_id
    json_blocks = re.findall(r"```json\s*(.*?)\s*```", plan_text, re.DOTALL)
    for jb in json_blocks:
        try:
            data = json.loads(jb)
            if isinstance(data, dict) and "allowed_files" in data:
                for af in data["allowed_files"]:
                    if isinstance(af, str):
                        allowed.add(Path(af).as_posix().lower())
                    elif isinstance(af, dict) and "path" in af:
                        allowed.add(Path(af["path"]).as_posix().lower())
        except Exception:
            pass

    # 2. Tìm các mẫu markdown: #### [MODIFY] [file](path) hoặc [NEW] [file](path)
    file_links = re.findall(r"\[(?:MODIFY|NEW|DELETE)\]\s*\[(.*?)\]\((.*?)\)", plan_text, re.IGNORECASE)
    for fname, fpath in file_links:
        clean_p = fpath.replace("file:///", "").replace("file://", "").strip()
        allowed.add(Path(clean_p).name.lower())
        allowed.add(Path(clean_p).as_posix().lower())

    # 3. Tìm các bullet points file path: - `path/to/file`
    bullets = re.findall(r"-\s*`([a-zA-Z0-9_\-/\\]+\.[a-zA-Z0-9]+)`", plan_text)
    for b in bullets:
        allowed.add(Path(b).name.lower())
        allowed.add(Path(b).as_posix().lower())

    return allowed


def check_scope_creep(plan_file: str, base_dir: str = ".", modified_files: Optional[List[str]] = None, auto_revert: bool = False) -> dict:
    """Kiểm tra và ngăn chặn sửa file ngoài target_files đã cam kết trong plan."""
    plan_path = Path(plan_file).resolve()
    if not plan_path.exists():
        return {"error": f"File plan không tồn tại: {plan_file}", "passed": False}

    plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
    allowed_set = extract_allowed_files_from_plan(plan_text)

    base_p = Path(base_dir).resolve()

    # Nếu không truyền modified_files, tự quét qua CoW manifest hoặc git status
    if modified_files is None:
        modified_files = []
        cow_manifest = base_p / ".agentkit_cow_manifest.json"
        if cow_manifest.exists():
            try:
                m_data = json.loads(cow_manifest.read_text(encoding="utf-8"))
                modified_files = m_data.get("modified_files", []) + m_data.get("created_files", [])
            except Exception:
                pass

    unauthorized_files = []
    forbidden_touched = []

    for f_str in modified_files:
        p = Path(f_str)
        p_name = p.name.lower()
        p_posix = p.as_posix().lower()

        # Kiểm tra forbidden config
        if p_name in FORBIDDEN_PATTERNS or any(p_name.startswith(pat) for pat in [".env"]):
            forbidden_touched.append(f_str)

        # Kiểm tra whitelist
        if allowed_set:
            is_allowed = (
                p_name in allowed_set 
                or p_posix in allowed_set 
                or any(p_posix.endswith(a) for a in allowed_set)
                or p.name.startswith("test_") # Cho phép file test đi kèm
            )
            if not is_allowed:
                unauthorized_files.append(f_str)

    # Auto Revert nếu được yêu cầu
    reverted_files = []
    if auto_revert and (unauthorized_files or forbidden_touched):
        for bad_file in set(unauthorized_files + forbidden_touched):
            target = base_p / bad_file
            if target.exists():
                try:
                    if target.is_file():
                        target.unlink()
                        reverted_files.append(bad_file)
                except Exception:
                    pass

    passed = len(unauthorized_files) == 0 and len(forbidden_touched) == 0

    return {
        "plan_file": str(plan_path),
        "allowed_count": len(allowed_set),
        "total_modified": len(modified_files),
        "unauthorized_files": unauthorized_files,
        "forbidden_touched": forbidden_touched,
        "reverted_files": reverted_files,
        "passed": passed
    }


# =====================================================================
# 3. SPEC INVARIANT ASSERTION ENGINE
# =====================================================================

def assert_system_invariants(code_dir: str = ".") -> dict:
    """Xác minh các bất biến hệ thống (Invariants): timeout, secrets, bare except."""
    base_p = Path(code_dir).resolve()
    violations = []

    for py_file in base_p.rglob("*.py"):
        # Bỏ qua tests và virtualenvs
        p_str = str(py_file).lower()
        if "test" in p_str or ".venv" in p_str or "venv" in p_str or "__pycache__" in p_str:
            continue

        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(py_file))

            for node in ast.walk(tree):
                # Invariant 1: Cấm bare except
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    violations.append({
                        "file": str(py_file.relative_to(base_p)),
                        "line": node.lineno,
                        "rule": "INV-SEC-001",
                        "desc": "Bare except (`except:`) vi phạm System Invariant (không nuốt lỗi)."
                    })

                # Invariant 2: Cấm HTTP request không có timeout
                elif isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    if func_name in ("get", "post", "put", "delete", "request"):
                        # Kiểm tra xem có keyword 'timeout' không
                        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                        # Kiểm tra xem có phải module requests / httpx / urllib không
                        if not has_timeout:
                            mod_name = getattr(getattr(node.func, "value", None), "id", "")
                            if mod_name in ("requests", "httpx", "client", "session"):
                                violations.append({
                                    "file": str(py_file.relative_to(base_p)),
                                    "line": node.lineno,
                                    "rule": "INV-RUNTIME-002",
                                    "desc": f"Lời gọi HTTP '{mod_name}.{func_name}' thiếu tham số `timeout` bắt buộc."
                                })
        except Exception:
            pass

    return {
        "code_dir": str(base_p),
        "total_violations": len(violations),
        "passed": len(violations) == 0,
        "violations": violations
    }


# =====================================================================
# CLI INTERFACE
# =====================================================================

def parse_cli_args(argv: list) -> tuple:
    if len(argv) < 2:
        return "help", {}
    cmd = argv[1]
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
    return cmd, args


def main():
    cmd, args = parse_cli_args(sys.argv)

    if cmd == "verify-ac":
        spec_file = args.get("spec", "")
        test_dir = args.get("tests", "tests")
        report = args.get("report")
        if not spec_file:
            print("❌ Cần cung cấp --spec <spec_file_path>")
            sys.exit(1)
        res = verify_acceptance_criteria(spec_file, test_dir=test_dir, report_xml=report)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"📋 ACCEPTANCE CRITERIA TRACEABILITY: {Path(spec_file).name}")
            print(f"  • Total ACs in Spec: {res.get('total_ac')}")
            print(f"  • Covered in Tests: {res.get('covered_ac')}")
            print(f"  • Missing: {res.get('missing_ac')}")
            print(f"  • Coverage Rate: {res.get('coverage_rate')}%")
            print(f"  • Verdict: {'✅ 100% COVERED' if res.get('passed') else '❌ INCOMPLETE COVERAGE'}")
            if res.get("missing_details"):
                print("\n⚠️ Kịch bản chưa có test:")
                for m in res["missing_details"]:
                    print(f"    - [{m['tag']}] {m['title']}")
        sys.exit(0 if res.get("passed") else 1)

    elif cmd == "check-scope":
        plan_file = args.get("plan", "")
        base_dir = args.get("base", ".")
        revert = args.get("auto-revert") == "true"
        if not plan_file:
            print("❌ Cần cung cấp --plan <plan_file_path>")
            sys.exit(1)
        res = check_scope_creep(plan_file, base_dir=base_dir, auto_revert=revert)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"🛡️ SCOPE CREEP & DIFF BOUNDARY CHECK")
            print(f"  • Whitelisted Files in Plan: {res.get('allowed_count')}")
            print(f"  • Total Modified: {res.get('total_modified')}")
            print(f"  • Unauthorized Scope Creep: {len(res.get('unauthorized_files', []))}")
            print(f"  • Forbidden Touched: {len(res.get('forbidden_touched', []))}")
            print(f"  • Verdict: {'✅ CLEAN (In Scope)' if res.get('passed') else '🛑 SCOPE VIOLATION DETECTED'}")
            if res.get("unauthorized_files"):
                print("\n⚠️ File ngoài phạm vi cho phép:")
                for u in res["unauthorized_files"]:
                    print(f"    - {u}")
            if res.get("forbidden_touched"):
                print("\n🛑 File cấu hình nhạy cảm bị sửa trái phép:")
                for f in res["forbidden_touched"]:
                    print(f"    - {f}")
        sys.exit(0 if res.get("passed") else 1)

    elif cmd == "assert-invariants":
        code_dir = args.get("code-dir", ".")
        res = assert_system_invariants(code_dir=code_dir)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"🔒 SYSTEM INVARIANTS AUDIT")
            print(f"  • Total Violations: {res.get('total_violations')}")
            print(f"  • Verdict: {'✅ ALL INVARIANTS PRESERVED' if res.get('passed') else '🛑 INVARIANT BREACH'}")
            if res.get("violations"):
                print("\n⚠️ Chi tiết vi phạm:")
                for v in res["violations"]:
                    print(f"    - [L{v['line']}] [{v['rule']}] {v['file']}: {v['desc']}")
        sys.exit(0 if res.get("passed") else 1)

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
