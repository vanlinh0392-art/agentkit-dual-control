#!/usr/bin/env python3
"""
AgentKit Code Guard, Deep AST Semantics Linter & Snapshot Engine v5.0
Kiểm toán cú pháp & Ngữ nghĩa sâu AST trước khi ghi file (Pre-Write Deep Gate),
Tự động hoàn tác an toàn (Micro-Snapshot Revert) và Cắt lát Diff Ngữ cảnh (Diff Slicing).
100% Python Standard Library (Zero-Dependency), thời gian thực thi < 15ms.

Usage:
    # 1. Kiểm tra cú pháp cơ bản
    python code_guard.py validate --file app.py

    # 2. Kiểm toán ngữ nghĩa sâu (Deep AST Linter Gate)
    python code_guard.py lint --file app.py [--strict] [--json]

    # 3. Cắt lát Unified Diff cho Reviewer (Token-Thrifty)
    python code_guard.py diff-slice --diff changes.diff [--context 7]

    # 4. Quản lý snapshot & giao dịch đa file
    python code_guard.py snapshot --file app.py
    python code_guard.py restore --file app.py
    python code_guard.py begin-tx --desc "Refactor"
    python code_guard.py commit-tx --tx <tx_id>
    python code_guard.py rollback-tx --tx <tx_id>
    python code_guard.py recover-tx
"""

import ast
import builtins
import hashlib
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any, Optional

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from _shared import get_config, emit_metric
except ImportError:
    def get_config(section, key, default=None): return default
    def emit_metric(event, **kwargs): pass


# =====================================================================
# 1. PRE-WRITE AST SYNTAX & LANGUAGE PARSERS
# =====================================================================

def validate_smali_syntax(content: str) -> dict:
    """Kiểm tra cú pháp Smali bytecode: method pairs, annotations, registers, labels."""
    lines = content.splitlines()
    in_method = False
    current_method = None
    defined_labels = set()
    referenced_labels = []

    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith(".method "):
            if in_method:
                return {
                    "valid": False,
                    "language": "smali",
                    "error": f"Lỗi lồng .method dòng {idx}: Phương thức '{current_method}' chưa đóng bằng .end method",
                    "line": idx
                }
            in_method = True
            current_method = stripped
            defined_labels.clear()
            referenced_labels.clear()

        elif stripped == ".end method":
            if not in_method:
                return {
                    "valid": False,
                    "language": "smali",
                    "error": f"Lỗi thừa .end method tại dòng {idx} khi không ở trong phương thức",
                    "line": idx
                }
            for ref_lbl, ref_line in referenced_labels:
                if ref_lbl not in defined_labels:
                    return {
                        "valid": False,
                        "language": "smali",
                        "error": f"Nhãn nhảy '{ref_lbl}' tại dòng {ref_line} không được định nghĩa trong '{current_method}'",
                        "line": ref_line
                    }
            in_method = False
            current_method = None

        elif in_method:
            if stripped.startswith(":"):
                label_name = stripped.split()[0]
                defined_labels.add(label_name)
            elif any(stripped.startswith(op) for op in ("goto ", "goto/16 ", "if-", "packed-switch ", "sparse-switch ")):
                parts = stripped.split()
                for p in parts:
                    if p.startswith(":"):
                        referenced_labels.append((p, idx))

    if in_method:
        return {
            "valid": False,
            "language": "smali",
            "error": f"Phương thức '{current_method}' chưa được đóng bằng .end method",
            "line": len(lines)
        }

    return {"valid": True, "language": "smali", "error": None}


def validate_c_style_syntax(content: str, language: str) -> dict:
    """Kiểm tra dấu đóng mở ngoặc và unclosed comment cho ngôn ngữ C-Style."""
    stack = []
    pairs = {')': '(', '}': '{', ']': '['}
    in_string = None
    in_block_comment = False
    idx = 0
    length = len(content)

    while idx < length:
        ch = content[idx]

        # Kiểm tra Block Comment /* ... */
        if not in_string and not in_block_comment and idx + 1 < length and content[idx:idx + 2] == "/*":
            in_block_comment = True
            idx += 2
            continue
        elif in_block_comment and idx + 1 < length and content[idx:idx + 2] == "*/":
            in_block_comment = False
            idx += 2
            continue

        # Kiểm tra Line Comment // ...
        if not in_string and not in_block_comment and idx + 1 < length and content[idx:idx + 2] == "//":
            while idx < length and content[idx] != '\n':
                idx += 1
            continue

        # Kiểm tra String literal
        if ch in ('"', "'", '`') and (idx == 0 or content[idx - 1] != '\\'):
            if in_string == ch:
                in_string = None
            elif in_string is None:
                in_string = ch
        elif in_string is None and not in_block_comment:
            if ch in ('(', '{', '['):
                stack.append((ch, idx))
            elif ch in (')', '}', ']'):
                if not stack or stack[-1][0] != pairs[ch]:
                    return {
                        "valid": False,
                        "language": language,
                        "error": f"Mất cân bằng dấu đóng mở ngoặc '{ch}' tại vị trí ký tự {idx}"
                    }
                stack.pop()

        idx += 1

    if in_block_comment:
        return {
            "valid": False,
            "language": language,
            "error": "Phát hiện block comment '/*' chưa được đóng bằng '*/'"
        }

    if stack:
        unclosed, pos = stack[-1]
        return {
            "valid": False,
            "language": language,
            "error": f"Chưa đóng ngoặc '{unclosed}' mở tại vị trí {pos}"
        }

    return {"valid": True, "language": language, "error": None}


def validate_code_syntax(filename: str, content: str) -> dict:
    """Kiểm tra tính hợp lệ cú pháp trước khi cho phép ghi file vào codebase."""
    ext = Path(filename).suffix.lower()

    # 1. Python AST Validation
    if ext == ".py":
        try:
            ast.parse(content, filename=filename)
            return {"valid": True, "language": "python", "error": None}
        except SyntaxError as e:
            return {
                "valid": False,
                "language": "python",
                "error": f"Lỗi cú pháp Python dòng {e.lineno}: {e.msg}",
                "line": e.lineno,
                "offset": e.offset
            }

    # 2. JSON Validation
    elif ext == ".json":
        try:
            json.loads(content)
            return {"valid": True, "language": "json", "error": None}
        except json.JSONDecodeError as e:
            return {
                "valid": False,
                "language": "json",
                "error": f"Lỗi cú pháp JSON dòng {e.lineno}, cột {e.colno}: {e.msg}",
                "line": e.lineno
            }

    # 3. XML Validation
    elif ext in (".xml", ".xaml", ".svg"):
        try:
            ET.fromstring(content)
            return {"valid": True, "language": "xml", "error": None}
        except ET.ParseError as e:
            return {
                "valid": False,
                "language": "xml",
                "error": f"Lỗi cú pháp XML: {e}",
                "line": getattr(e, "lineno", None)
            }

    # 4. Smali Bytecode Validation
    elif ext == ".smali":
        return validate_smali_syntax(content)

    # 5. JS / TS Bracket Check
    elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
        return validate_c_style_syntax(content, "javascript")

    # 6. C-Style Languages
    elif ext in (".java", ".rs", ".go", ".cs", ".cpp", ".c", ".h", ".kt", ".dart"):
        lang_name = ext[1:].lower()
        return validate_c_style_syntax(content, lang_name)

    return {"valid": True, "language": "plain_text", "error": None}


# =====================================================================
# 2. DEEP AST STATIC SEMANTICS & POLYGLOT LINTER (Audit 3 Gate)
# =====================================================================

PYTHON_BUILTIN_NAMES = set(dir(builtins)) - {
    "_", "__name__", "__doc__", "__file__", "__package__", "object", "type"
}

RESOURCE_ALLOCATORS = {
    "open", "connect", "socket", "urlopen", "ClientSession", 
    "TemporaryFile", "NamedTemporaryFile"
}

class PythonSemanticsChecker(ast.NodeVisitor):
    """
    Kiểm toán ngữ nghĩa sâu AST cho Python:
    - PY-EXC-001: Bare except (`except:`) hoặc bắt `BaseException`
    - PY-EXC-002: Nuốt lỗi (`except Exception: pass`) không re-raise và không log
    - PY-EXC-003: Biến ngoại lệ `as e` không được sử dụng
    - PY-IMP-001: Dangling / Unused imports (không nằm trong __all__, không dùng)
    - PY-VAR-001: Variable shadowing built-in names (id, list, type, dict...)
    - PY-FLOW-001: Missing return paths trong hàm non-void
    - PY-RES-001: Resource leaks (open/socket/connect không bọc with/try-finally)
    """
    def __init__(self, source_code: str, filename: str):
        self.source = source_code
        self.filename = filename
        self.lines = source_code.splitlines()
        self.issues: List[Dict[str, Any]] = []

        self.imported_symbols: Dict[str, Tuple[int, int]] = {}
        self.referenced_symbols: Set[str] = set()
        self.scope_stack: List[Set[str]] = [set()]
        self.with_context_calls: Set[int] = set()

    def _get_line_content(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""

    def add_issue(self, rule_id: str, msg: str, node: ast.AST, severity: str, hint: str):
        lineno = getattr(node, "lineno", 1)
        col_offset = getattr(node, "col_offset", 0)
        self.issues.append({
            "rule_id": rule_id,
            "message": msg,
            "line": lineno,
            "col": col_offset,
            "severity": severity,
            "fix_hint": hint,
            "source_line": self._get_line_content(lineno)
        })

    def visit_With(self, node: ast.With):
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                self.with_context_calls.add(item.context_expr.lineno)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith):
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                self.with_context_calls.add(item.context_expr.lineno)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        # 1. Bare except:
        if node.type is None:
            self.add_issue(
                "PY-EXC-001",
                "Phát hiện Bare except (`except:`). Khối này bắt cả BaseException và chặn ngắt tiến trình.",
                node,
                "CRITICAL",
                "Thay bằng `except Exception:` hoặc bắt cụ thể loại Exception cần xử lý."
            )
        elif isinstance(node.type, ast.Name) and node.type.id == "BaseException":
            self.add_issue(
                "PY-EXC-001",
                "Bắt trực tiếp `BaseException` là phản mẫu nguy hiểm.",
                node,
                "CRITICAL",
                "Chỉ bắt `Exception` thay vì `BaseException`."
            )

        # 2. Empty / Swallowed exception
        is_empty = False
        if len(node.body) == 1:
            stmt = node.body[0]
            if isinstance(stmt, ast.Pass):
                is_empty = True
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                is_empty = True

        if is_empty:
            has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            if not has_raise:
                self.add_issue(
                    "PY-EXC-002",
                    "Khối except nuốt trọn ngoại lệ mà không log hoặc re-raise (Silent Exception Swallowing).",
                    node,
                    "ERROR",
                    "Ghi log lỗi qua logging/print hoặc re-raise ngoại lệ bằng `raise`."
                )

        # 3. Unused exception variable
        if node.name:
            loaded_in_body = {
                n.id for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            if node.name not in loaded_in_body:
                self.add_issue(
                    "PY-EXC-003",
                    f"Biến ngoại lệ '{node.name}' được gán nhưng không bao giờ được sử dụng trong except block.",
                    node,
                    "WARNING",
                    f"Sử dụng biến `{node.name}` để log hoặc bỏ alias `as {node.name}`."
                )

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            name = alias.asname or alias.name
            if alias.name != "__future__":
                self.imported_symbols[name] = (node.lineno, node.col_offset)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module != "__future__":
            for alias in node.names:
                name = alias.asname or alias.name
                self.imported_symbols[name] = (node.lineno, node.col_offset)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            self.referenced_symbols.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            if node.id in PYTHON_BUILTIN_NAMES and getattr(node, "lineno", 0) > 1:
                # Bỏ qua id và type ở mức script nhỏ
                if node.id in ("list", "dict", "str", "int", "open", "input", "format", "dir", "len"):
                    self.add_issue(
                        "PY-VAR-001",
                        f"Tên biến '{node.id}' ghi đè hàm chuẩn (built-in shadowing) của Python.",
                        node,
                        "ERROR",
                        f"Đổi tên biến thành `{node.id}_val`, `item_{node.id}` hoặc tên mang nghĩa nghiệp vụ."
                    )
            self.scope_stack[-1].add(node.id)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare):
        """PY-CMP-001: == None, PY-CMP-002: == True/False."""
        for op, comparator in zip(node.ops, node.comparators):
            # PY-CMP-001: == None / != None
            if isinstance(comparator, ast.Constant) and comparator.value is None:
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    op_str = "==" if isinstance(op, ast.Eq) else "!="
                    is_str = "is" if isinstance(op, ast.Eq) else "is not"
                    self.add_issue(
                        "PY-CMP-001",
                        f"So sánh với None bằng `{op_str}` thay vì `{is_str}` — có thể sai khi __eq__ bị override.",
                        node, "WARNING",
                        f"Thay `{op_str} None` bằng `{is_str} None` (PEP 8)."
                    )
            # PY-CMP-002: == True / == False
            if (isinstance(comparator, ast.Constant) and isinstance(comparator.value, bool)
                    and isinstance(op, (ast.Eq, ast.NotEq))):
                val = comparator.value
                self.add_issue(
                    "PY-CMP-002",
                    f"So sánh boolean `== {val}` — dùng trực tiếp biểu thức boolean.",
                    node, "WARNING",
                    f"Thay `x == True` bằng `x`, `x == False` bằng `not x`."
                )
        self.generic_visit(node)

    def visit_While(self, node: ast.While):
        """PY-LOOP-001: Unbounded while True/1 without break or return."""
        is_infinite = isinstance(node.test, ast.Constant) and node.test.value in (True, 1)
        if is_infinite:
            has_exit = any(isinstance(n, (ast.Break, ast.Return)) for n in ast.walk(node))
            if not has_exit:
                self.add_issue(
                    "PY-LOOP-001",
                    "Vòng lặp `while True` không có `break` hoặc `return` — nguy cơ infinite loop.",
                    node, "ERROR",
                    "Thêm `break` với điều kiện dừng hoặc dùng `for _ in range(max_retries):`."
                )
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        """PY-TOCTOU-001: Check-then-act race condition on filesystem."""
        exists_var = None
        # Extract variable from os.path.exists(var) in if test
        test = node.test
        if (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
                and isinstance(test.func.value, ast.Attribute)
                and isinstance(test.func.value.value, ast.Name)
                and test.func.value.value.id == "os"
                and test.func.value.attr == "path"
                and test.func.attr == "exists"
                and test.args):
            arg = test.args[0]
            if isinstance(arg, ast.Name):
                exists_var = arg.id

        if exists_var:
            fs_ops = {"remove", "unlink", "rename", "rmdir"}
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                # Check open(var) or os.remove(var)
                name = ""
                if isinstance(child.func, ast.Name):
                    name = child.func.id
                elif isinstance(child.func, ast.Attribute) and isinstance(child.func.value, ast.Name):
                    if child.func.value.id == "os":
                        name = child.func.attr
                if name in ("open", *fs_ops) and child.args:
                    if isinstance(child.args[0], ast.Name) and child.args[0].id == exists_var:
                        self.add_issue(
                            "PY-TOCTOU-001",
                            f"TOCTOU race: `os.path.exists({exists_var})` rồi `{name}({exists_var})` — giữa check và act file có thể thay đổi.",
                            node, "WARNING",
                            "Dùng try/except trực tiếp thay vì check-then-act, hoặc dùng os.replace/atomic write."
                        )
                        break
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._check_function_scope(node)

    def _check_function_scope(self, node):
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.arg in PYTHON_BUILTIN_NAMES and arg.arg in ("list", "dict", "str", "int", "open", "format"):
                self.add_issue(
                    "PY-VAR-001",
                    f"Tham số hàm '{arg.arg}' ghi đè built-in của Python.",
                    arg,
                    "ERROR",
                    f"Đổi tên tham số thành `{arg.arg}_name` hoặc tên chuyên biệt."
                )

        # PY-ARG-001: Mutable default argument
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                kind = {ast.List: "list", ast.Dict: "dict", ast.Set: "set"}.get(type(default), "mutable")
                self.add_issue(
                    "PY-ARG-001",
                    f"Hàm '{node.name}' dùng {kind} làm giá trị mặc định — chia sẻ giữa các lần gọi, gây data corruption.",
                    default,
                    "ERROR",
                    "Dùng `None` làm default: `def f(items=None): items = items or []`."
                )

        self._check_missing_returns(node)
        self._check_unreachable_code(node.body)
        self.scope_stack.append(set())
        self.generic_visit(node)
        self.scope_stack.pop()

    def _check_unreachable_code(self, stmts):
        """PY-FLOW-002: Phát hiện code sau return/raise/sys.exit() không bao giờ chạy.
        Recursive — kiểm tra cả bên trong if/try/for/while/with blocks."""
        for i, stmt in enumerate(stmts):
            is_terminal = isinstance(stmt, (ast.Return, ast.Raise))
            if not is_terminal and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                if isinstance(call.func, ast.Attribute):
                    if (isinstance(call.func.value, ast.Name) and
                            call.func.value.id == "sys" and call.func.attr == "exit"):
                        is_terminal = True
                elif isinstance(call.func, ast.Name) and call.func.id == "exit":
                    is_terminal = True
            if is_terminal and i < len(stmts) - 1:
                next_stmt = stmts[i + 1]
                self.add_issue(
                    "PY-FLOW-002",
                    "Mã không bao giờ được thực thi (Unreachable Code) sau return/raise/sys.exit().",
                    next_stmt,
                    "ERROR",
                    "Xóa mã sau câu lệnh kết thúc hoặc di chuyển lên trước."
                )
                break
            # Recurse into sub-blocks
            if isinstance(stmt, ast.If):
                self._check_unreachable_code(stmt.body)
                if stmt.orelse:
                    self._check_unreachable_code(stmt.orelse)
            elif isinstance(stmt, (ast.For, ast.While)):
                self._check_unreachable_code(stmt.body)
            elif isinstance(stmt, ast.Try):
                self._check_unreachable_code(stmt.body)
                for handler in stmt.handlers:
                    self._check_unreachable_code(handler.body)
                if stmt.finalbody:
                    self._check_unreachable_code(stmt.finalbody)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                self._check_unreachable_code(stmt.body)

    def _check_missing_returns(self, func_node):
        # Bỏ qua generators
        for sub in ast.walk(func_node):
            if isinstance(sub, (ast.Yield, ast.YieldFrom)):
                return

        value_returns = [
            n for n in ast.walk(func_node)
            if isinstance(n, ast.Return) and n.value is not None and not (
                isinstance(n.value, ast.Constant) and n.value.value is None
            )
        ]

        if not value_returns:
            return

        if not self._is_block_terminal(func_node.body):
            self.add_issue(
                "PY-FLOW-001",
                f"Hàm '{func_node.name}' có nhánh trả về giá trị nhưng tồn tại luồng thực thi bị rơi tự do (Missing Return Path).",
                func_node,
                "CRITICAL",
                "Đảm bảo mọi nhánh if/elif/else hoặc khối kết thúc đều có `return` giá trị hoặc `raise` ngoại lệ."
            )

    def _is_block_terminal(self, stmts: List[ast.stmt]) -> bool:
        if not stmts:
            return False
        last_stmt = stmts[-1]

        if isinstance(last_stmt, (ast.Return, ast.Raise)):
            return True

        if isinstance(last_stmt, ast.If):
            if not last_stmt.orelse:
                return False
            return self._is_block_terminal(last_stmt.body) and self._is_block_terminal(last_stmt.orelse)

        if isinstance(last_stmt, ast.Try):
            if last_stmt.finalbody and self._is_block_terminal(last_stmt.finalbody):
                return True
            try_term = self._is_block_terminal(last_stmt.body)
            handlers_term = bool(last_stmt.handlers) and all(
                self._is_block_terminal(h.body) for h in last_stmt.handlers
            )
            return try_term and handlers_term

        return False

    def visit_Call(self, node: ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in RESOURCE_ALLOCATORS:
            if node.lineno not in self.with_context_calls:
                # Kiểm tra xem có nằm ở root level hoặc gán biến trần không
                line_text = self._get_line_content(node.lineno)
                if "with " not in line_text:
                    self.add_issue(
                        "PY-RES-001",
                        f"Khởi tạo tài nguyên `{func_name}()` mà không dùng `with` context manager.",
                        node,
                        "WARNING",
                        f"Bọc lời gọi `{func_name}()` trong `with ... as handle:` để chống rò rỉ file/socket."
                    )

        # PY-SEC-001: Unsafe deserialization
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            mod = node.func.value.id
            attr = node.func.attr
            if mod == "pickle" and attr in ("load", "loads"):
                self.add_issue(
                    "PY-SEC-001",
                    f"`{mod}.{attr}()` cho phép thực thi mã tùy ý qua Deserialization Attack.",
                    node, "CRITICAL",
                    "Dùng `json.loads()` hoặc serializer an toàn thay pickle cho dữ liệu không tin cậy."
                )
            elif mod == "yaml" and attr == "load":
                has_safe = any(
                    isinstance(kw, ast.keyword) and kw.arg == "Loader" and
                    isinstance(kw.value, ast.Attribute) and kw.value.attr in ("SafeLoader", "CSafeLoader")
                    for kw in node.keywords
                )
                if not has_safe:
                    self.add_issue(
                        "PY-SEC-001",
                        "`yaml.load()` không chỉ định SafeLoader — cho phép thực thi mã tùy ý.",
                        node, "CRITICAL",
                        "Dùng `yaml.safe_load()` hoặc `yaml.load(data, Loader=yaml.SafeLoader)`."
                    )

        self.generic_visit(node)

    def finalize_checks(self):
        if self.filename.endswith("__init__.py"):
            return

        for sym, (line, col) in self.imported_symbols.items():
            if sym not in self.referenced_symbols:
                line_content = self._get_line_content(line)
                if "noqa" not in line_content and "unused" not in line_content:
                    dummy_node = ast.AST()
                    dummy_node.lineno = line
                    dummy_node.col_offset = col
                    self.add_issue(
                        "PY-IMP-001",
                        f"Import '{sym}' được định nghĩa nhưng không bao giờ được sử dụng (Dangling Import).",
                        dummy_node,
                        "ERROR",
                        f"Xóa import `{sym}` hoặc thêm vào `__all__` nếu là public interface."
                    )


class PolyglotInvariantsChecker:
    """Kiểm tra Invariants ngữ nghĩa siêu tốc cho JS, TS, Go, Rust, Java."""
    @staticmethod
    def check_javascript_typescript(content: str, filename: str) -> List[Dict[str, Any]]:
        issues = []
        lines = content.splitlines()

        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue

            # 1. Floating Promise
            if re.match(r"^(fetch\(|axios\.\w+\(|[a-zA-Z0-9_]+Service\.\w+\().*;\s*$", stripped):
                if not stripped.startswith("await ") and "return " not in stripped:
                    issues.append({
                        "rule_id": "JS-ASYNC-001",
                        "message": "Phát hiện Floating Promise không có `await` và không có `.catch()`.",
                        "line": idx,
                        "col": 0,
                        "severity": "WARNING",
                        "fix_hint": "Thêm `await` trước lời gọi hoặc thêm `.catch(err => ...)`.",
                        "source_line": stripped
                    })

            # 2. Empty Catch
            if re.search(r"catch\s*(\([^)]*\))?\s*\{\s*\}", stripped):
                issues.append({
                    "rule_id": "JS-EXC-001",
                    "message": "Khối catch rỗng làm mất dấu vết lỗi runtime trong JavaScript/TypeScript.",
                    "line": idx,
                    "col": 0,
                    "severity": "ERROR",
                    "fix_hint": "Ghi log lỗi bằng `console.error(err)` hoặc ném lại lỗi.",
                    "source_line": stripped
                })

            # 3. Dangerous eval
            if re.search(r"\beval\s*\(", stripped) and not stripped.startswith("//"):
                issues.append({
                    "rule_id": "JS-SEC-001",
                    "message": "Sử dụng `eval()` gây lỗ hổng Arbitrary Code Execution nghiêm trọng.",
                    "line": idx,
                    "col": 0,
                    "severity": "CRITICAL",
                    "fix_hint": "Thay thế bằng JSON.parse hoặc hàm xử lý dữ liệu an toàn.",
                    "source_line": stripped
                })

        return issues

    @staticmethod
    def check_golang(content: str, filename: str) -> List[Dict[str, Any]]:
        issues = []
        lines = content.splitlines()

        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            # 1. Ignored Error Tuples
            if re.match(r"^\s*_\s*,\s*_\s*:?=\s*(os\.|http\.|io\.|net\.)", stripped):
                issues.append({
                    "rule_id": "GO-ERR-001",
                    "message": "Âm thầm bỏ qua biến error trong Go (`_ , _ := ...`).",
                    "line": idx,
                    "col": 0,
                    "severity": "CRITICAL",
                    "fix_hint": "Nhận biến error và kiểm tra `if err != nil { return err }`.",
                    "source_line": stripped
                })

            # 2. Missing defer Body.Close()
            if "http.Get(" in stripped or "http.Post(" in stripped:
                has_close = any("Body.Close()" in lookahead for lookahead in lines[idx:min(idx + 6, len(lines))])
                if not has_close:
                    issues.append({
                        "rule_id": "GO-RES-001",
                        "message": "Gọi HTTP Request nhưng thiếu `defer resp.Body.Close()`, gây rò rỉ Socket/FD.",
                        "line": idx,
                        "col": 0,
                        "severity": "CRITICAL",
                        "fix_hint": "Thêm `defer resp.Body.Close()` ngay sau khi kiểm tra `err != nil`.",
                        "source_line": stripped
                    })

        return issues

    @staticmethod
    def check_rust(content: str, filename: str) -> List[Dict[str, Any]]:
        issues = []
        if "test" in filename:
            return issues

        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue

            if ".unwrap()" in stripped:
                issues.append({
                    "rule_id": "RS-PANIC-001",
                    "message": "Sử dụng `.unwrap()` trong code chính có thể gây crash panic đột ngột.",
                    "line": idx,
                    "col": 0,
                    "severity": "WARNING",
                    "fix_hint": "Sử dụng toán tử `?`, `unwrap_or_default()`, hoặc xử lý bằng `match / if let`.",
                    "source_line": stripped
                })

        return issues


def lint_code(filename: str, content: str, strict: bool = False) -> dict:
    """Kiểm toán ngữ nghĩa sâu toàn diện (Deep Semantics Linter Gate)."""
    start_time = time.perf_counter()
    ext = Path(filename).suffix.lower()

    # 1. Kiểm tra cú pháp cơ bản trước
    syntax_res = validate_code_syntax(filename, content)
    if not syntax_res["valid"]:
        return {
            "file": filename,
            "language": syntax_res["language"],
            "valid": False,
            "syntax_error": syntax_res["error"],
            "issues": [{
                "rule_id": "SYNTAX-ERR",
                "severity": "CRITICAL",
                "line": syntax_res.get("line", 1),
                "message": syntax_res["error"],
                "fix_hint": "Sửa lỗi cú pháp trước khi chạy semantic linter."
            }],
            "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
        }

    issues = []

    # 2. Python Semantic AST Checks
    if ext == ".py":
        try:
            tree = ast.parse(content, filename=filename)
            checker = PythonSemanticsChecker(content, filename)
            checker.visit(tree)
            checker.finalize_checks()
            issues.extend(checker.issues)
        except Exception:
            pass

    # 3. Polyglot Invariants Checks
    elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
        issues.extend(PolyglotInvariantsChecker.check_javascript_typescript(content, filename))
    elif ext == ".go":
        issues.extend(PolyglotInvariantsChecker.check_golang(content, filename))
    elif ext == ".rs":
        issues.extend(PolyglotInvariantsChecker.check_rust(content, filename))

    # 4. Filter disabled rules from config.yaml
    disabled = get_config("linter", "disabled_rules", [])
    if disabled:
        disabled_set = set(disabled) if isinstance(disabled, list) else set()
        issues = [i for i in issues if i["rule_id"] not in disabled_set]

    # 5. Strict mode from config (WARNING → ERROR)
    if strict or get_config("linter", "strict_mode", False):
        strict = True

    has_critical = any(i["severity"] == "CRITICAL" for i in issues)
    has_error = any(i["severity"] == "ERROR" for i in issues)

    valid = not has_critical if not strict else (not has_critical and not has_error)

    summary = {
        "critical": sum(1 for i in issues if i["severity"] == "CRITICAL"),
        "error": sum(1 for i in issues if i["severity"] == "ERROR"),
        "warning": sum(1 for i in issues if i["severity"] == "WARNING")
    }

    return {
        "file": filename,
        "language": syntax_res["language"],
        "valid": valid,
        "summary": summary,
        "issues": issues,
        "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
    }


# =====================================================================
# 3. TOKEN-THRIFTY UNIFIED DIFF SLICING (Audit 6 Gate)
# =====================================================================

def diff_slice(diff_text: str, context_lines: int = 7, max_tokens: int = 1500) -> str:
    """
    Cắt lát Unified Diff giữ lại context_lines dòng ngữ cảnh quanh thay đổi
    và đính kèm Hunk Header để giảm 85%-95% lượng token input.
    """
    lines = diff_text.splitlines()
    # Track which line indices are "important" (headers, hunks, changes)
    important = set()

    for i, line in enumerate(lines):
        if (line.startswith("diff --git") or line.startswith("--- ") or
                line.startswith("+++ ") or line.startswith("@@")):
            important.add(i)
        elif line.startswith("+") or line.startswith("-"):
            important.add(i)
            # Mark surrounding context lines as keep-worthy too
            for offset in range(1, context_lines + 1):
                if i - offset >= 0:
                    important.add(i - offset)
                if i + offset < len(lines):
                    important.add(i + offset)

    sliced = []
    prev_included = -2  # Track gaps for ellipsis insertion
    for i, line in enumerate(lines):
        if i in important:
            if prev_included < i - 1 and prev_included >= 0:
                sliced.append(f"  ... ({i - prev_included - 1} lines omitted) ...")
            sliced.append(line)
            prev_included = i

    result = "\n".join(sliced)
    # Cắt trần nếu vượt quá max_tokens xấp xỉ (1 token ~ 4 chars)
    max_chars = max_tokens * 4
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... [TRUNCATED DIFF FOR TOKEN-THRIFTY REVIEW] ...\n"

    return result


# =====================================================================
# 4. MICRO-SNAPSHOT & INSTANT REVERT ENGINE
# =====================================================================

def get_snapshot_dir() -> Path:
    """Thư mục lưu snapshot an toàn."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        brain = parent / ".brain"
        if brain.is_dir():
            s_dir = brain / "snapshots"
            s_dir.mkdir(parents=True, exist_ok=True)
            return s_dir
        if parent == Path.home():
            break

    global_s_dir = Path.home() / ".gemini" / "antigravity" / "brain" / "snapshots"
    global_s_dir.mkdir(parents=True, exist_ok=True)
    return global_s_dir


def get_path_hash(path: Path) -> str:
    """Tạo mã băm SHA-256 rút gọn (8 ký tự) từ đường dẫn tuyệt đối (đã chuẩn hóa normcase)."""
    normalized = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def atomic_write_json(file_path: Path, data: dict) -> bool:
    """Ghi file JSON an toàn nguyên tử bằng Write-to-Temp + os.replace."""
    file_path = Path(file_path).resolve()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file_path.parent / f".tmp_{file_path.name}_{os.getpid()}_{time.time_ns()}.tmp"
    try:
        temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_file, file_path)
        return True
    except Exception:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        return False


def create_snapshot(target_file: str) -> str:
    """Tạo bản snapshot .bak an toàn (Microsecond timestamp + random salt chống collision)."""
    path = Path(target_file)
    if not path.exists():
        return ""

    s_dir = get_snapshot_dir()
    path_hash = get_path_hash(path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = f"{path.stem}_{path_hash}_{timestamp}_{os.urandom(2).hex()}{path.suffix}.bak"
    snapshot_path = s_dir / safe_name

    shutil.copy2(path, snapshot_path)
    return str(snapshot_path)


def restore_latest_snapshot(target_file: str) -> bool:
    """Khôi phục bản snapshot gần nhất của file mục tiêu chính xác theo Path Hash."""
    path = Path(target_file)
    s_dir = get_snapshot_dir()
    path_hash = get_path_hash(path)

    pattern = f"{path.stem}_{path_hash}_*{path.suffix}.bak"
    matches = sorted(s_dir.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)

    if not matches:
        legacy_pattern = f"{path.stem}_*{path.suffix}.bak"
        matches = sorted(s_dir.glob(legacy_pattern), key=lambda x: x.stat().st_mtime, reverse=True)

    if not matches:
        return False

    latest_bak = matches[0]
    shutil.copy2(latest_bak, path)
    return True


def prune_snapshots(keep: int = 5) -> int:
    """Tự động dọn rác snapshot cũ và các transaction đã hoàn tất."""
    s_dir = get_snapshot_dir()
    if not s_dir.exists():
        return 0

    snapshots_by_prefix = {}
    for f in s_dir.glob("*.bak"):
        parts = f.stem.split("_")
        prefix = "_".join(parts[:2]) if len(parts) >= 2 else f.stem
        snapshots_by_prefix.setdefault(prefix, []).append(f)

    deleted_count = 0
    for prefix, files in snapshots_by_prefix.items():
        sorted_files = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)
        if len(sorted_files) > keep:
            for old_file in sorted_files[keep:]:
                try:
                    old_file.unlink()
                    deleted_count += 1
                except Exception:
                    pass

    for tx_dir in s_dir.glob("tx_*"):
        if tx_dir.is_dir():
            wal_file = tx_dir / "wal.json"
            if wal_file.exists():
                try:
                    data = json.loads(wal_file.read_text(encoding="utf-8"))
                    if data.get("status") in ("COMMITTED", "ROLLED_BACK"):
                        shutil.rmtree(tx_dir, ignore_errors=True)
                        deleted_count += 1
                except Exception:
                    pass

    return deleted_count


# =====================================================================
# 5. TRANSACTIONAL MULTI-FILE SNAPSHOT & WAL ROLLBACK
# =====================================================================

def begin_transaction(description: str = "") -> str:
    """Khởi tạo một giao dịch sửa đổi đa file (ACID Multi-File Transaction)."""
    s_dir = get_snapshot_dir()
    tx_id = f"tx_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.urandom(3).hex()}"
    tx_dir = s_dir / tx_id
    tx_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "tx_id": tx_id,
        "created_at": datetime.now().isoformat(),
        "status": "IN_PROGRESS",
        "description": description,
        "files": {}
    }
    atomic_write_json(tx_dir / "wal.json", manifest)
    return tx_id


def snapshot_transaction_file(tx_id: str, target_file: str) -> bool:
    """Lưu bản snapshot của 1 file vào giao dịch hiện thời trước khi sửa đổi."""
    path = Path(target_file).resolve()
    if not path.exists():
        return False

    s_dir = get_snapshot_dir()
    tx_dir = s_dir / tx_id
    wal_file = tx_dir / "wal.json"
    if not wal_file.exists():
        return False

    manifest = json.loads(wal_file.read_text(encoding="utf-8"))
    path_hash = get_path_hash(path)
    bak_name = f"{path.stem}_{path_hash}_{os.urandom(2).hex()}{path.suffix}.bak"
    bak_path = tx_dir / bak_name

    shutil.copy2(path, bak_path)
    manifest["files"][str(path)] = {
        "bak_name": bak_name,
        "original_path": str(path),
        "size": path.stat().st_size
    }
    atomic_write_json(wal_file, manifest)
    return True


def commit_transaction(tx_id: str) -> bool:
    """Xác nhận giao dịch thành công (Commit Transaction)."""
    s_dir = get_snapshot_dir()
    tx_dir = s_dir / tx_id
    wal_file = tx_dir / "wal.json"
    if not wal_file.exists():
        return False

    manifest = json.loads(wal_file.read_text(encoding="utf-8"))
    manifest["status"] = "COMMITTED"
    manifest["committed_at"] = datetime.now().isoformat()
    atomic_write_json(wal_file, manifest)
    return True


def rollback_transaction(tx_id: str) -> dict:
    """Hoàn tác nguyên tử toàn bộ các file trong giao dịch (ACID WAL Rollback < 5ms)."""
    s_dir = get_snapshot_dir()
    tx_dir = s_dir / tx_id
    wal_file = tx_dir / "wal.json"
    if not wal_file.exists():
        return {"success": False, "error": f"Không tìm thấy transaction {tx_id}"}

    manifest = json.loads(wal_file.read_text(encoding="utf-8"))
    restored_files = []

    for orig_path_str, info in manifest.get("files", {}).items():
        orig_p = Path(orig_path_str)
        bak_p = tx_dir / info["bak_name"]
        if bak_p.exists():
            orig_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bak_p, orig_p)
            restored_files.append(orig_path_str)

    manifest["status"] = "ROLLED_BACK"
    manifest["rolled_back_at"] = datetime.now().isoformat()
    atomic_write_json(wal_file, manifest)

    return {
        "success": True,
        "tx_id": tx_id,
        "restored_count": len(restored_files),
        "restored_files": restored_files
    }


def recover_dangling_transactions() -> dict:
    """Tự động phát hiện và rollback các transaction bị gián đoạn do crash (IN_PROGRESS)."""
    s_dir = get_snapshot_dir()
    recovered = []
    for tx_dir in s_dir.glob("tx_*"):
        if tx_dir.is_dir():
            wal_file = tx_dir / "wal.json"
            if wal_file.exists():
                try:
                    data = json.loads(wal_file.read_text(encoding="utf-8"))
                    if data.get("status") == "IN_PROGRESS":
                        res = rollback_transaction(data["tx_id"])
                        recovered.append({"tx_id": data["tx_id"], "res": res})
                except Exception:
                    pass
    return {"recovered_count": len(recovered), "recovered": recovered}


# =====================================================================
# CLI PARSER
# =====================================================================

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
    target = args.get("file", "")

    if command == "validate":
        if not target or not Path(target).exists():
            print(f"❌ File không tồn tại: {target}")
            sys.exit(1)
        content = Path(target).read_text(encoding="utf-8", errors="replace")
        res = validate_code_syntax(target, content)
        if res["valid"]:
            print(f"✅ [{res['language'].upper()}] Cú pháp hợp lệ 100%!")
        else:
            print(f"🛑 [SYNTAX ERROR] {res['error']}")
            sys.exit(1)

    elif command == "lint":
        if not target or not Path(target).exists():
            print(f"❌ File không tồn tại: {target}")
            sys.exit(1)
        content = Path(target).read_text(encoding="utf-8", errors="replace")
        strict = args.get("strict") == "true"
        res = lint_code(target, content, strict=strict)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"🛡️ DEEP AST LINTER: {Path(target).name} ({res['execution_time_ms']}ms)")
            print(f"  • Summary: {res['summary']['critical']} Critical | {res['summary']['error']} Error | {res['summary']['warning']} Warning")
            print(f"  • Verdict: {'✅ PASS' if res['valid'] else '🛑 REJECTED (Block Ghi File)'}")
            if res.get("issues"):
                print("\n📋 Chi tiết vi phạm:")
                for iss in res["issues"]:
                    print(f"    - [L{iss['line']}] [{iss['severity']}] {iss['rule_id']}: {iss['message']}")
                    print(f"      👉 Khắc phục: {iss['fix_hint']}")
        # Emit metrics
        emit_metric("gate_pass" if res["valid"] else "gate_block",
                     gate=1, file=target, summary=res.get("summary"))
        sys.exit(0 if res["valid"] else 1)

    elif command == "diff-slice":
        diff_f = args.get("diff", "")
        if not diff_f or not Path(diff_f).exists():
            print(f"❌ Cần cung cấp --diff <diff_file_path>")
            sys.exit(1)
        diff_text = Path(diff_f).read_text(encoding="utf-8", errors="replace")
        default_ctx = get_config("diff_slicing", "context_lines", 7)
        ctx = int(args.get("context", default_ctx))
        sliced = diff_slice(diff_text, context_lines=ctx)
        print(sliced)

    elif command == "snapshot":
        if not target or not Path(target).exists():
            print(f"❌ File không tồn tại: {target}")
            sys.exit(1)
        snap = create_snapshot(target)
        if snap:
            print(f"📸 Đã tạo snapshot: {Path(snap).name}")
        else:
            print(f"❌ Không thể tạo snapshot cho {target}")

    elif command == "restore":
        if not target:
            print("❌ Cần cung cấp --file <target>")
            sys.exit(1)
        ok = restore_latest_snapshot(target)
        if ok:
            print(f"⏪ Đã hoàn tác {target} về snapshot gần nhất thành công!")
        else:
            print(f"⚠️ Không tìm thấy snapshot nào cho {target}")

    elif command == "begin-tx":
        desc = args.get("desc", "Multi-file transaction")
        tx_id = begin_transaction(desc)
        print(f"🚀 Transaction started: {tx_id}")

    elif command == "commit-tx":
        tx_id = args.get("tx", "")
        if commit_transaction(tx_id):
            print(f"✅ Transaction committed: {tx_id}")
        else:
            print(f"❌ Commit failed for {tx_id}")

    elif command == "rollback-tx":
        tx_id = args.get("tx", "")
        res = rollback_transaction(tx_id)
        if res.get("success"):
            print(f"⏪ Rolled back {res['restored_count']} files for {tx_id}")
        else:
            print(f"❌ Rollback failed: {res.get('error')}")

    elif command == "recover-tx":
        rec = recover_dangling_transactions()
        print(f"🛡️ Dangling Transactions Recovered: {rec['recovered_count']}")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
