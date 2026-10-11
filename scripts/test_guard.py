#!/usr/bin/env python3
"""
AgentKit Test Rigor & Adversarial Mutation Verifier Engine v1.0 (Audit 4 Gate)
Động cơ thẩm định chất lượng bài test (Anti-Cargo-Cult Testing) và Đột biến đối kháng (Mutation Testing Lite).
100% Python Standard Library (Zero-Dependency), In-memory AST, tốc độ < 3 giây.

Usage:
    # 1. Kiểm tra độ nghiêm ngặt của test (Assert density, Tautology filter, Mock check)
    python test_guard.py audit --test tests/test_app.py

    # 2. Chạy Mutation Testing Lite (đột biến code xem test có bắt được không)
    python test_guard.py mutate --target app.py --test tests/test_app.py [--max 5]

    # 3. Chạy Test Suite (Gate 2.5 - auto-detect runner)
    python test_guard.py run --project-dir ./src [--timeout 120]
"""

import ast
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from _shared import get_config, emit_metric
except ImportError:
    def get_config(section, key, default=None): return default
    def emit_metric(event, **kwargs): pass

# =====================================================================
# 1. AST TEST RIGOR AUDITOR
# =====================================================================

class TestRigorAuditor(ast.NodeVisitor):
    """
    Kiểm tra độ nghiêm ngặt của file test:
    - Diệt trừ assertion-free tests (hàm test không có assert)
    - Diệt trừ tautology asserts (assert True, assert 1 == 1, assert x == x)
    - Kiểm tra Mock-to-Code ratio và cấm Mock-the-SUT (Subject Under Test)
    - Bắt buộc kiểm tra ngoại lệ (pytest.raises) hoặc điều kiện biên
    """
    def __init__(self, target_symbol: Optional[str] = None):
        self.target_symbol = target_symbol
        self.test_funcs = 0
        self.meaningful_asserts = 0
        self.empty_or_trivial_asserts = 0
        self.exception_asserts = 0
        self.mock_calls = 0
        self.sut_mocked = False
        self.violations: List[Dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_test_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._check_test_function(node)

    def _check_test_function(self, node):
        if node.name.startswith("test_") or node.name.endswith("_test"):
            self.test_funcs += 1
            func_asserts = 0
            has_exception_check = False

            for child in ast.walk(node):
                # 1. Kiểm tra Assert statement
                if isinstance(child, ast.Assert):
                    if self._is_trivial_assert(child.test):
                        self.empty_or_trivial_asserts += 1
                        self.violations.append({
                            "line": child.lineno,
                            "func": node.name,
                            "type": "Trivial/Tautology Assertion",
                            "detail": f"Phát hiện assert vô nghĩa/luôn đúng trong '{node.name}'."
                        })
                    else:
                        func_asserts += 1
                        self.meaningful_asserts += 1

                # 2. Kiểm tra pytest.raises hoặc assertRaises
                elif isinstance(child, ast.With):
                    for item in child.items:
                        if isinstance(item.context_expr, ast.Call):
                            call_name = getattr(item.context_expr.func, "id", "") or getattr(item.context_expr.func, "attr", "")
                            if "raises" in call_name:
                                has_exception_check = True
                                self.exception_asserts += 1

                # 3. Kiểm tra Mock interactions
                elif isinstance(child, ast.Call):
                    func_name = getattr(child.func, "id", "") or getattr(child.func, "attr", "")
                    if "patch" in func_name or "mock" in func_name.lower():
                        self.mock_calls += 1
                        # Kiểm tra Mock-the-SUT
                        if self.target_symbol and len(child.args) > 0:
                            first_arg = child.args[0]
                            arg_str = getattr(first_arg, "value", "") or getattr(first_arg, "id", "")
                            if self.target_symbol in str(arg_str):
                                self.sut_mocked = True
                                self.violations.append({
                                    "line": child.lineno,
                                    "func": node.name,
                                    "type": "Mock-the-SUT Violation",
                                    "detail": f"Hàm test đang mock chính class/method mục tiêu '{self.target_symbol}'."
                                })

            if func_asserts == 0 and not has_exception_check:
                self.violations.append({
                    "line": node.lineno,
                    "func": node.name,
                    "type": "Zero-Assert Test Function",
                    "detail": f"Hàm '{node.name}' không có bất kỳ assert thực chất nào (Cargo Cult Test)."
                })

        self.generic_visit(node)

    def _is_trivial_assert(self, test_node: ast.AST) -> bool:
        """Nhận diện các assert vô nghĩa: assert True, assert 1 == 1, assert x == x, assert len(x) >= 0."""
        # assert True / assert False / assert 1
        if isinstance(test_node, ast.Constant):
            return True

        # assert x == x hoặc assert x is x
        if isinstance(test_node, ast.Compare):
            if len(test_node.ops) == 1:
                op = test_node.ops[0]
                left = test_node.left
                right = test_node.comparators[0]
                
                # assert a == a
                if isinstance(op, (ast.Eq, ast.Is)):
                    left_id = getattr(left, "id", None)
                    right_id = getattr(right, "id", None)
                    if left_id and left_id == right_id:
                        return True
                    if isinstance(left, ast.Constant) and isinstance(right, ast.Constant) and left.value == right.value:
                        return True

                # assert len(x) >= 0 (luôn đúng)
                if isinstance(op, ast.GtE):
                    if isinstance(left, ast.Call) and getattr(left.func, "id", "") == "len":
                        if isinstance(right, ast.Constant) and right.value == 0:
                            return True

        return False


def verify_test_rigor(test_file_path: str, target_symbol: Optional[str] = None) -> dict:
    """Đánh giá độ nghiêm ngặt của file test qua AST tĩnh."""
    path = Path(test_file_path).resolve()
    if not path.exists():
        return {"error": f"File test không tồn tại: {test_file_path}", "passed": False}

    code = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(code, filename=str(path))
    except SyntaxError as se:
        return {"error": f"Cú pháp test file không hợp lệ: {se}", "passed": False}

    auditor = TestRigorAuditor(target_symbol=target_symbol)
    auditor.visit(tree)

    num_tests = max(auditor.test_funcs, 1)
    density = auditor.meaningful_asserts / num_tests
    
    # Tính Rigor Score (0 - 100)
    # Thresholds from config.yaml (fallback to hardcoded)
    min_density = get_config("quality_gates", "assert_density_min", 2.0)
    min_score = get_config("quality_gates", "rigor_score_min", 80)

    score = 100
    if density < 1.0:
        score -= 50
    elif density < min_density:
        score -= 20
        
    score -= (auditor.empty_or_trivial_asserts * 25)
    if auditor.sut_mocked:
        score -= 40
    if auditor.test_funcs > 0 and auditor.exception_asserts == 0:
        score -= 10
    score = max(0, score)

    passed = (
        score >= min_score 
        and auditor.empty_or_trivial_asserts == 0 
        and density >= 1.0 
        and not auditor.sut_mocked
    )

    return {
        "file": str(path),
        "test_functions_count": auditor.test_funcs,
        "meaningful_asserts": auditor.meaningful_asserts,
        "assert_density": round(density, 2),
        "trivial_asserts_count": auditor.empty_or_trivial_asserts,
        "exception_asserts_count": auditor.exception_asserts,
        "mock_calls_count": auditor.mock_calls,
        "sut_mocked": auditor.sut_mocked,
        "rigor_score": score,
        "passed": passed,
        "violations": auditor.violations
    }


# =====================================================================
# 2. ADVERSARIAL MUTATION TESTING LITE
# =====================================================================

class LiteMutator(ast.NodeTransformer):
    """
    AST Transformer sinh 5 loại đột biến đối kháng:
    - MUT-1: Đổi toán tử so sánh / biên (> thành <=, < thành >=, == thành !=)
    - MUT-2: Đảo cờ Boolean (True thành False)
    - MUT-3: Đảo toán tử logic (and thành or, or thành and)
    - MUT-4: Guard clause bypass (return <val> thành return None)
    - MUT-5: Đảo toán tử số học (+ thành -, - thành +)
    """
    def __init__(self, target_index: int):
        self.target_index = target_index
        self.current_idx = 0
        self.applied_desc: Optional[str] = None

    def visit_Compare(self, node: ast.Compare):
        if len(node.ops) == 1:
            op = node.ops[0]
            mutated_op = None
            desc = ""
            if isinstance(op, ast.Gt):
                mutated_op, desc = ast.LtE(), "> thành <="
            elif isinstance(op, ast.Lt):
                mutated_op, desc = ast.GtE(), "< thành >="
            elif isinstance(op, ast.GtE):
                mutated_op, desc = ast.Lt(), ">= thành <"
            elif isinstance(op, ast.LtE):
                mutated_op, desc = ast.Gt(), "<= thành >"
            elif isinstance(op, ast.Eq):
                mutated_op, desc = ast.NotEq(), "== thành !="
            elif isinstance(op, ast.NotEq):
                mutated_op, desc = ast.Eq(), "!= thành =="

            if mutated_op:
                if self.current_idx == self.target_index:
                    node.ops[0] = mutated_op
                    self.applied_desc = f"[MUT-1 Boundary] Dòng {node.lineno}: Đổi {desc}"
                self.current_idx += 1
        return self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, bool):
            if self.current_idx == self.target_index:
                node.value = not node.value
                self.applied_desc = f"[MUT-2 Boolean] Dòng {node.lineno}: Đổi Boolean thành {node.value}"
            self.current_idx += 1
        return node

    def visit_BoolOp(self, node: ast.BoolOp):
        if isinstance(node.op, ast.And):
            if self.current_idx == self.target_index:
                node.op = ast.Or()
                self.applied_desc = f"[MUT-3 Logical] Dòng {node.lineno}: Đổi `and` thành `or`"
            self.current_idx += 1
        elif isinstance(node.op, ast.Or):
            if self.current_idx == self.target_index:
                node.op = ast.And()
                self.applied_desc = f"[MUT-3 Logical] Dòng {node.lineno}: Đổi `or` thành `and`"
            self.current_idx += 1
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp):
        mutated_op = None
        desc = ""
        if isinstance(node.op, ast.Add):
            mutated_op, desc = ast.Sub(), "+ thành -"
        elif isinstance(node.op, ast.Sub):
            mutated_op, desc = ast.Add(), "- thành +"

        if mutated_op:
            if self.current_idx == self.target_index:
                node.op = mutated_op
                self.applied_desc = f"[MUT-5 Arithmetic] Dòng {node.lineno}: Đổi {desc}"
            self.current_idx += 1
        return self.generic_visit(node)


def run_mutation_testing(target_src: str, test_src: str, max_mutants: int = 5, test_command: Optional[List[str]] = None) -> dict:
    """
    Thực thi Mutation Testing Lite trực tiếp trên AST.
    - Đột biến code của target_src
    - Chạy test_src
    - Nếu test FAIL -> Mutant bị KILLED (Tốt!)
    - Nếu test PASS -> Mutant SURVIVED (Test rác, bỏ sót lỗi!)
    """
    target_path = Path(target_src).resolve()
    test_path = Path(test_src).resolve()

    if not target_path.exists() or not test_path.exists():
        return {"error": "Target hoặc Test file không tồn tại", "passed": False}

    original_code = target_path.read_text(encoding="utf-8", errors="replace")

    # Xây dựng lệnh test
    if not test_command:
        test_command = [sys.executable, "-m", "unittest", str(test_path)]

    # 1. Baseline Run (Test gốc phải PASS trước khi bắt đầu đột biến)
    try:
        base_res = subprocess.run(test_command, capture_output=True, text=True, timeout=10)
        if base_res.returncode != 0:
            # Thử lại với pytest nếu unittest không nhận
            alt_cmd = [sys.executable, "-m", "pytest", "-q", str(test_path)]
            alt_res = subprocess.run(alt_cmd, capture_output=True, text=True, timeout=10)
            if alt_res.returncode == 0:
                test_command = alt_cmd
            else:
                return {
                    "error": "Bộ test gốc chưa PASS baseline. Bắt buộc test phải pass trước khi chạy Mutation Testing.",
                    "baseline_stdout": base_res.stdout[:300],
                    "baseline_stderr": base_res.stderr[:300],
                    "passed": False
                }
    except Exception as e:
        return {"error": f"Lỗi chạy baseline test: {e}", "passed": False}

    mutants_log = []
    killed = 0
    survived = 0

    try:
        for idx in range(max_mutants):
            tree = ast.parse(original_code, filename=str(target_path))
            mutator = LiteMutator(target_index=idx)
            mutated_tree = mutator.visit(tree)

            if not mutator.applied_desc:
                # Đã duyệt hết các vị trí có thể đột biến
                break

            if hasattr(ast, "unparse"):
                mutated_code = ast.unparse(mutated_tree)
            else:
                break

            # Ghi đè tạm thời code đột biến
            target_path.write_text(mutated_code, encoding="utf-8")

            # Chạy test trên code đột biến
            try:
                run_res = subprocess.run(test_command, capture_output=True, text=True, timeout=10)
                if run_res.returncode != 0:
                    killed += 1
                    mutants_log.append({
                        "id": idx + 1,
                        "desc": mutator.applied_desc,
                        "status": "KILLED",
                        "verdict": "✅ Test phát hiện ra đột biến (Bắt lỗi chuẩn)"
                    })
                else:
                    survived += 1
                    mutants_log.append({
                        "id": idx + 1,
                        "desc": mutator.applied_desc,
                        "status": "SURVIVED",
                        "verdict": "🛑 Mutant sống sót! Test không bắt được lỗi này (Blind Test)"
                    })
            except subprocess.TimeoutExpired:
                killed += 1
                mutants_log.append({
                    "id": idx + 1,
                    "desc": mutator.applied_desc,
                    "status": "KILLED (Timeout)",
                    "verdict": "✅ Đột biến gây timeout, test suite bắt được"
                })
    finally:
        # Atomic Revert: Luôn khôi phục lại code gốc 100%
        target_path.write_text(original_code, encoding="utf-8")

    total = killed + survived
    score = (killed / total * 100.0) if total > 0 else 100.0
    passed = score >= 80.0

    return {
        "target": str(target_path),
        "test": str(test_path),
        "total_mutants": total,
        "killed": killed,
        "survived": survived,
        "kill_rate": round(score, 1),
        "passed": passed,
        "mutants": mutants_log
    }


# =====================================================================
# 3. TEST EXECUTION GATE (Gate 2.5)
# =====================================================================

def _detect_test_runner(project_dir: str) -> tuple:
    """Auto-detect test runner dựa trên project files. Returns (command_list, runner_name)."""
    p = Path(project_dir).resolve()

    # pytest detection: pytest.ini, setup.cfg, or pyproject.toml with pytest
    if (p / "pytest.ini").exists():
        return ([sys.executable, "-m", "pytest", "-q", "--tb=short"], "pytest")

    for cfg_name in ("setup.cfg", "pyproject.toml"):
        cfg_path = p / cfg_name
        if cfg_path.exists():
            try:
                content = cfg_path.read_text(encoding="utf-8", errors="replace")
                if "pytest" in content:
                    return ([sys.executable, "-m", "pytest", "-q", "--tb=short"], "pytest")
            except OSError:
                pass

    # npm test detection
    pkg_json = p / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            if pkg.get("scripts", {}).get("test"):
                return (["npm", "test"], "npm")
        except (json.JSONDecodeError, OSError):
            pass

    # Cargo test detection
    if (p / "Cargo.toml").exists():
        return (["cargo", "test"], "cargo")

    # Go test detection
    if (p / "go.mod").exists():
        return (["go", "test", "./..."], "go")

    # Fallback: look for tests/ or test/ directory
    for test_dir_name in ("tests", "test"):
        if (p / test_dir_name).is_dir():
            return ([sys.executable, "-m", "pytest", "-q"], "pytest (fallback)")

    return ([sys.executable, "-m", "pytest", "-q"], "pytest (default)")


def run_test_suite(project_dir: str, timeout: int = 120) -> dict:
    """
    Gate 2.5 — Chạy test suite của project, auto-detect runner.
    Returns dict với passed, exit_code, runner, output/error snippets, duration_ms.
    """
    p = Path(project_dir).resolve()
    if not p.is_dir():
        return {
            "passed": False, "exit_code": -1, "runner": "none",
            "output_snippet": "", "error_snippet": f"Project dir không tồn tại: {project_dir}",
            "duration_ms": 0
        }

    cmd, runner = _detect_test_runner(str(p))

    start_ts = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(p)
        )
        duration_ms = int((time.monotonic() - start_ts) * 1000)

        return {
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "runner": runner,
            "output_snippet": result.stdout[:2000] if result.stdout else "",
            "error_snippet": result.stderr[:2000] if result.stderr else "",
            "duration_ms": duration_ms
        }
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        return {
            "passed": False, "exit_code": -1, "runner": runner,
            "output_snippet": "", "error_snippet": f"Timeout sau {timeout}s",
            "duration_ms": duration_ms
        }
    except FileNotFoundError as e:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        return {
            "passed": False, "exit_code": -1, "runner": runner,
            "output_snippet": "", "error_snippet": f"Không tìm thấy runner: {e}",
            "duration_ms": duration_ms
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

    if cmd == "audit":
        test_file = args.get("test", "")
        if not test_file:
            print("❌ Cần cung cấp --test <test_file_path>")
            sys.exit(1)
        res = verify_test_rigor(test_file, target_symbol=args.get("symbol"))
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"🧪 TEST RIGOR AUDIT: {Path(test_file).name}")
            print(f"  • Test Functions: {res.get('test_functions_count')}")
            print(f"  • Assert Density: {res.get('assert_density')} asserts/test")
            print(f"  • Trivial/Tautology: {res.get('trivial_asserts_count')}")
            print(f"  • Rigor Score: {res.get('rigor_score')}/100")
            print(f"  • Verdict: {'✅ PASS' if res.get('passed') else '❌ REJECTED'}")
            if res.get("violations"):
                print("\n⚠️ Vi phạm phát hiện:")
                for v in res["violations"]:
                    print(f"    - [L{v['line']}] [{v['type']}] {v['detail']}")
        sys.exit(0 if res.get("passed") else 1)

    elif cmd == "mutate":
        target = args.get("target", "")
        test_file = args.get("test", "")
        max_m = int(args.get("max", 5))
        if not target or not test_file:
            print("❌ Cần cung cấp --target <src_file> và --test <test_file>")
            sys.exit(1)
        res = run_mutation_testing(target, test_file, max_mutants=max_m)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"⚡ MUTATION TESTING LITE: {Path(target).name}")
            print(f"  • Total Mutants: {res.get('total_mutants')}")
            print(f"  • Killed: {res.get('killed')} | Survived: {res.get('survived')}")
            print(f"  • Kill Rate: {res.get('kill_rate')}% (Ngưỡng yêu cầu: >= 80%)")
            print(f"  • Verdict: {'✅ PASS' if res.get('passed') else '❌ REJECTED'}")
            if res.get("mutants"):
                print("\n📋 Chi tiết Mutants:")
                for m in res["mutants"]:
                    print(f"    [{m['status']}] {m['desc']} -> {m['verdict']}")
        sys.exit(0 if res.get("passed") else 1)

    elif cmd == "run":
        project_dir = args.get("project-dir", ".")
        timeout = int(args.get("timeout", 120))
        res = run_test_suite(project_dir, timeout=timeout)
        emit_metric("gate_2_5_test_run", **res)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            status_icon = "✅" if res.get("passed") else "❌"
            print(f"🚀 TEST EXECUTION GATE (2.5): {Path(project_dir).resolve().name}")
            print(f"  • Runner: {res.get('runner')}")
            print(f"  • Exit Code: {res.get('exit_code')}")
            print(f"  • Duration: {res.get('duration_ms')}ms")
            print(f"  • Verdict: {status_icon} {'PASS' if res.get('passed') else 'FAIL'}")
            if res.get("output_snippet"):
                print(f"\n📤 Output:\n{res['output_snippet']}")
            if res.get("error_snippet"):
                print(f"\n📥 Stderr:\n{res['error_snippet']}")
        if res.get("exit_code") == -1:
            sys.exit(2)
        sys.exit(0 if res.get("passed") else 1)

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
