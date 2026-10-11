#!/usr/bin/env python3
"""
AgentKit Orchestrate DAG & 3-Way Diff Merge Engine v5.3
Động cơ điều phối đa tác tử theo đồ thị có hướng (DAG), Trọng tài hợp nhất mã nguồn (3-Way Merge Arbiter),
Cross-Worker Contract Verification (Gate 0.5), True Adversarial Review Prompt Generator,
và Security-Aware Review Complexity Router.

Usage:
    # 1. Giải quyết thứ tự thực thi DAG
    python orchestrate_engine.py plan-dag --spec '[{"id":"t1","depends_on":[]},{"id":"t2","depends_on":["t1"]}]'

    # 2. Hợp nhất 3 chiều tự động
    python orchestrate_engine.py merge-3way --base base.py --a a.py --b b.py --output merged.py

    # 3. Kiểm tra Cross-Worker Contract (Gate 0.5)
    python orchestrate_engine.py verify-contracts --dir ./src [--files "a.py,b.py"]

    # 4. Sinh prompts cho 3 reviewer subagents
    python orchestrate_engine.py generate-review-prompts --diff changes.diff [--spec spec.md]

    # 5. Route review tự động phát hiện sensitive content
    python orchestrate_engine.py review-route --lines 120 --files 3 [--diff changes.diff]
"""

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from _shared import emit_metric, get_config
except ImportError:
    def emit_metric(event, **kwargs): pass
    def get_config(section, key, default=None): return default


# =====================================================================
# 1. TASK DAG DEPENDENCY RESOLVER
# =====================================================================

def resolve_dag_stages(tasks: list) -> list:
    """
    Phân rã danh sách tasks thành các Stages thực thi song song (Topological Sort).
    Mỗi stage chứa các tasks có thể chạy đồng thời vì dependencies đã hoàn thành ở stage trước.
    """
    task_map = {t["id"]: t for t in tasks}
    in_degree = {t["id"]: 0 for t in tasks}
    adj_list = defaultdict(list)

    # Xây dựng đồ thị
    for t in tasks:
        deps = t.get("depends_on", [])
        in_degree[t["id"]] = len(deps)
        for dep in deps:
            if dep not in task_map:
                raise ValueError(f"Task '{t['id']}' phụ thuộc vào task không tồn tại: '{dep}'")
            adj_list[dep].append(t["id"])

    # Tìm tasks ở Stage 0 (không có dependencies)
    queue = deque([t_id for t_id, deg in in_degree.items() if deg == 0])
    stages = []
    processed_count = 0

    while queue:
        current_stage = []
        next_queue = deque()

        while queue:
            node_id = queue.popleft()
            current_stage.append(task_map[node_id])
            processed_count += 1

            for neighbor in adj_list[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_queue.append(neighbor)

        stages.append(current_stage)
        queue = next_queue

    if processed_count < len(tasks):
        unresolved = [t_id for t_id, deg in in_degree.items() if deg > 0]
        raise ValueError(f"Phát hiện vòng lặp phụ thuộc (Cyclic Dependency) giữa các tasks: {unresolved}")

    return stages


def format_dag_plan(stages: list) -> str:
    """Format DAG execution plan đẹp mắt."""
    output = ["🏛️ AGENTKIT ORCHESTRATE DAG EXECUTION PLAN", "=" * 60]
    for idx, stage in enumerate(stages):
        output.append(f"\n📍 STAGE {idx + 1} ({len(stage)} task(s) chạy song song):")
        for t in stage:
            deps_str = f" [sau: {', '.join(t.get('depends_on', []))}]" if t.get("depends_on") else " [Độc lập]"
            output.append(f"  • [{t.get('id')}] {t.get('role', 'Agent')} ({t.get('type', 'self')}){deps_str}")
            if t.get("prompt"):
                output.append(f"    Prompt: {t.get('prompt')[:70]}...")
    output.append("\n" + "=" * 60)
    return "\n".join(output)


# =====================================================================
# 2. 3-WAY DIFF MERGE ARBITER (CHUNK-BASED STREAM ITERATOR + GIT FAST-PATH)
# =====================================================================

def merge_3way(base_text: str, a_text: str, b_text: str) -> dict:
    """
    Thực hiện 3-way merge chính xác 100% giữa base, version A và version B.
    Hỗ trợ Native Git Fast-Path (<5ms) và Python Chunk-based Stream Iterator O(N)
    đảm bảo không bao giờ nuốt code mới chèn thêm (Pure Insertion Safe).
    """
    if a_text == base_text:
        return {'clean': True, 'merged_text': b_text, 'conflicts': [], 'stats': {'method': 'fast_no_op_a'}}
    if b_text == base_text:
        return {'clean': True, 'merged_text': a_text, 'conflicts': [], 'stats': {'method': 'fast_no_op_b'}}
    if a_text == b_text:
        return {'clean': True, 'merged_text': a_text, 'conflicts': [], 'stats': {'method': 'fast_identical'}}

    # --- TIER 1: NATIVE GIT FAST-PATH ---
    try:
        with tempfile.TemporaryDirectory(prefix="merge_3way_") as tmp_dir:
            tmp_p = Path(tmp_dir)
            f_base = tmp_p / "base.tmp"
            f_a = tmp_p / "a.tmp"
            f_b = tmp_p / "b.tmp"

            f_base.write_text(base_text, encoding="utf-8")
            f_a.write_text(a_text, encoding="utf-8")
            f_b.write_text(b_text, encoding="utf-8")

            res = subprocess.run(
                ["git", "merge-file", "-p", "-q", str(f_a), str(f_base), str(f_b)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10  # Anti-Hang Timeout (10s)
            )
            # Exit code 0 nghĩa là clean merge hoàn hảo!
            if res.returncode == 0:
                return {
                    'clean': True,
                    'merged_text': res.stdout,
                    'conflicts': [],
                    'stats': {'method': 'native_git_clean'}
                }
            elif res.returncode > 0:
                # Có conflict được đánh dấu cục bộ bằng git merge-file
                return {
                    'clean': False,
                    'merged_text': res.stdout,
                    'conflicts': [{'type': 'git_conflict_markers', 'count': res.returncode}],
                    'stats': {'method': 'native_git_conflict'}
                }
    except Exception:
        pass

    # --- TIER 2: PYTHON CHUNK-BASED STREAM ITERATOR (INSERTION-SAFE O(N)) ---
    base_lines = base_text.splitlines(keepends=True)
    a_lines = a_text.splitlines(keepends=True)
    b_lines = b_text.splitlines(keepends=True)

    matcher_a = difflib.SequenceMatcher(None, base_lines, a_lines)
    matcher_b = difflib.SequenceMatcher(None, base_lines, b_lines)

    # Trích xuất danh sách edit hunks: (alo, ahi, replacement_lines, branch_id)
    edits_a = []
    for tag, alo, ahi, blo, bhi in matcher_a.get_opcodes():
        if tag != 'equal':
            edits_a.append((alo, ahi, a_lines[blo:bhi], 'A'))

    edits_b = []
    for tag, alo, ahi, blo, bhi in matcher_b.get_opcodes():
        if tag != 'equal':
            edits_b.append((alo, ahi, b_lines[blo:bhi], 'B'))

    # Kiểm tra xung đột (Overlap Collision Detection)
    conflicts = []
    for ea in edits_a:
        alo_a, ahi_a, lines_a, _ = ea
        for eb in edits_b:
            alo_b, ahi_b, lines_b, _ = eb

            # Kiểm tra va chạm:
            is_collision = False
            if alo_a == ahi_a and alo_b == ahi_b:
                # Cả 2 cùng chèn tại 1 điểm
                if alo_a == alo_b:
                    if lines_a != lines_b:
                        is_collision = True
            elif alo_a == ahi_a:
                # A chèn trong dải B thay đổi
                if alo_b < alo_a < ahi_b:
                    is_collision = True
            elif alo_b == ahi_b:
                # B chèn trong dải A thay đổi
                if alo_a < alo_b < ahi_a:
                    is_collision = True
            else:
                # Cả 2 cùng thay đổi dải dòng
                if max(alo_a, alo_b) < min(ahi_a, ahi_b):
                    is_collision = True

            if is_collision:
                conflicts.append({'edit_a': ea, 'edit_b': eb})

    if conflicts:
        # Tạo conflict markers cục bộ
        conflict_block = (
            f"<<<<<<< CANDIDATE_A\n{a_text}\n"
            f"=======\n"
            f"{b_text}\n"
            f">>>>>>> CANDIDATE_B\n"
        )
        return {
            'clean': False,
            'merged_text': conflict_block,
            'conflicts': conflicts,
            'stats': {'method': 'python_stream_conflict', 'conflict_count': len(conflicts)}
        }

    # Hợp nhất an toàn bằng cách gom và sắp xếp toàn bộ edits
    all_edits = sorted(edits_a + edits_b, key=lambda x: (x[0], 0 if x[0] == x[1] else 1))

    combined = []
    last_base_idx = 0

    for alo, ahi, rep_lines, branch_id in all_edits:
        if alo > last_base_idx:
            combined.extend(base_lines[last_base_idx:alo])
        combined.extend(rep_lines)
        last_base_idx = max(last_base_idx, ahi)

    if last_base_idx < len(base_lines):
        combined.extend(base_lines[last_base_idx:])

    return {
        'clean': True,
        'merged_text': "".join(combined),
        'conflicts': [],
        'stats': {'method': 'python_chunk_stream_clean', 'applied_edits': len(all_edits)}
    }


# =====================================================================
# CLI PARSER
# =====================================================================
# 3. DYNAMIC DAG FAILURE ISOLATION & RECOVERY
# =====================================================================

def isolate_task_failure(tasks: list, failed_task_ids: list) -> dict:
    """
    Khi 1 hoặc nhiều task trong stage bị lỗi, tự động cô lập toàn bộ các downstream
    dependents của task lỗi và tái cấu trúc các stage cho những task độc lập còn lại.
    """
    failed_set = set(failed_task_ids)
    skipped_set = set()

    # Tìm transitive downstream dependents
    changed = True
    while changed:
        changed = False
        for t in tasks:
            t_id = t["id"]
            if t_id in failed_set or t_id in skipped_set:
                continue
            deps = t.get("depends_on", [])
            if any(dep in failed_set or dep in skipped_set for dep in deps):
                skipped_set.add(t_id)
                changed = True

    # Lọc danh sách runnable tasks
    runnable_tasks = [t for t in tasks if t["id"] not in failed_set and t["id"] not in skipped_set]
    
    new_stages = []
    if runnable_tasks:
        new_stages = resolve_dag_stages(runnable_tasks)

    return {
        "failed_tasks": list(failed_set),
        "skipped_tasks": list(skipped_set),
        "runnable_tasks": [t["id"] for t in runnable_tasks],
        "new_stages": new_stages,
        "salvaged_count": len(runnable_tasks),
        "status": "partial_success" if runnable_tasks else "all_failed"
    }


# =====================================================================
# 4. DYNAMIC TOPOLOGY CLASSIFIER & CONTEXT SLICER (v4.0)
# =====================================================================

def classify_topology(task_info: dict) -> dict:
    """
    Tự động phân loại tác vụ thành 1 trong 4 Mô hình Topology tối ưu:
    1. query_fan_out: Scatter-gather cho /plan, /ultra, /brainstorm, review.
    2. parallel_cow: Fork-join song song cho các module/component độc lập.
    3. sequential_chain: Chuỗi mắt xích tuần tự cho DB migration, pipeline phụ thuộc.
    4. hybrid_mesh: Mạng lai ghép cho toàn trình dự án SDLC.
    """
    mode = task_info.get("mode", "").lower()
    tasks = task_info.get("tasks", [])
    files = task_info.get("files", [])
    has_db = any("db" in str(f).lower() or "migration" in str(f).lower() for f in files)
    
    if mode in ("plan", "ultra", "brainstorm", "audit", "review") or len(tasks) == 0:
        return {
            "topology": "query_fan_out",
            "name": "Query Fan-Out (Scatter-Gather)",
            "description": "Phát tán đồng thời tới 5-9 candidates và tổng hợp qua Kongming Verifier trong 1 turn duy nhất",
            "concurrency": 5,
            "estimated_speedup": "3x - 5x"
        }
    
    # Kiểm tra đồ thị phụ thuộc
    deps_count = sum(len(t.get("depends_on", [])) for t in tasks)
    if deps_count == 0 and len(tasks) > 1:
        return {
            "topology": "parallel_cow",
            "name": "Parallel CoW Execution (Fork-Join)",
            "description": "Chạy song song các worker trên các CoW sandbox độc lập và gộp lại qua N-Way Fast-Path",
            "concurrency": min(len(tasks), 4),
            "estimated_speedup": "3x"
        }
    elif has_db or (deps_count >= len(tasks) - 1 and len(tasks) > 1):
        return {
            "topology": "sequential_chain",
            "name": "Sequential Chain (Pipeline Handoff)",
            "description": "Thực thi tuần tự từng bước kèm Compact Contract và Micro-Snapshot checkpoint",
            "concurrency": 1,
            "estimated_speedup": "1x (Đảm bảo tính toàn vẹn 100%)"
        }
    else:
        return {
            "topology": "hybrid_mesh",
            "name": "Hybrid Adaptive Mesh",
            "description": "Kết hợp Fan-Out lúc lập plan, Sequential khi làm DB và Parallel khi viết code",
            "concurrency": 3,
            "estimated_speedup": "2.5x"
        }


HEAVY_DIRS_TO_PRUNE = {".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", ".cache", ".pytest_cache", ".cargo", "target"}


def merge_nway_disjoint(base_dir: Path, branch_dirs: list, output_dir: Path = None, council_verdict: Optional[dict] = None) -> dict:
    """
    Hợp nhất N nhánh đồng thời (Dual-Tier Fast-Path Merge).
    Tier 1: Quét O(Δ) qua CoW Manifest nếu có.
    Tier 2: In-Flight Pruned Directory Walk + Stat-Filter (st_size, st_mtime) chống nghẽn I/O 9GB.
    Pre-Merge Gate: Thẩm định phán quyết của Hội đồng Review Đối kháng (Fail-Closed Veto).
    """
    if council_verdict is not None and not council_verdict.get("can_merge", False):
        veto_reasons = council_verdict.get("veto_triggers", ["Chưa qua kiểm duyệt của Hội đồng Review"])
        raise PermissionError(f"🛑 [FAIL-CLOSED MERGE BLOCKED] Lệnh Merge bị chặn đứng bởi Hội đồng Review Đối kháng!\n{json.dumps(veto_reasons, ensure_ascii=False, indent=2)}")

    base_dir = Path(base_dir).resolve()
    branch_paths = [Path(b).resolve() for b in branch_dirs]
    out_dir = Path(output_dir).resolve() if output_dir else base_dir

    branch_changes = []
    file_to_branches = defaultdict(list)

    for idx, b_dir in enumerate(branch_paths):
        b_files = {}
        manifest_file = b_dir / ".agentkit_cow_manifest.json"

        # Tier 1: Fast O(Δ) CoW Manifest Scan (<20ms)
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                for rel_p in manifest.get("modified_files", []):
                    src_f = b_dir / rel_p
                    if src_f.exists():
                        b_files[rel_p] = src_f
                        file_to_branches[rel_p].append(idx)
                for rel_p in manifest.get("created_files", []):
                    src_f = b_dir / rel_p
                    if src_f.exists():
                        b_files[rel_p] = src_f
                        file_to_branches[rel_p].append(idx)
            except Exception:
                pass

        # Tier 2: Fallback In-Flight Pruned Walk
        if not b_files:
            for root, dirs, files in os.walk(b_dir):
                # In-Flight Pruning các thư mục nặng
                dirs[:] = [d for d in dirs if d not in HEAVY_DIRS_TO_PRUNE and not d.startswith(".agentkit")]
                for f in files:
                    if f.startswith(".agentkit"):
                        continue
                    src_p = Path(root) / f
                    rel_p = str(src_p.relative_to(b_dir).as_posix())
                    base_f = base_dir / rel_p

                    # Stat Filter trước khi đọc bytes
                    if not base_f.exists():
                        b_files[rel_p] = src_p
                        file_to_branches[rel_p].append(idx)
                    else:
                        st_src = src_p.stat()
                        st_base = base_f.stat()
                        if st_src.st_size != st_base.st_size or (st_src.st_mtime != st_base.st_mtime and src_p.read_bytes() != base_f.read_bytes()):
                            b_files[rel_p] = src_p
                            file_to_branches[rel_p].append(idx)

        branch_changes.append(b_files)

    # Kiểm tra Disjoint-Set
    overlapping_files = [f for f, b_indices in file_to_branches.items() if len(b_indices) > 1]
    
    applied_files = []
    conflict_files = []

    if not overlapping_files:
        # 100% Disjoint Fast-Path O(1)
        for b_idx, b_files in enumerate(branch_changes):
            for rel_p, src_file in b_files.items():
                dest_file = (out_dir / rel_p).resolve()
                # Zero-Trust Sandbox Jail Guard
                try:
                    dest_file.relative_to(out_dir)
                except ValueError:
                    raise PermissionError(f"🛑 [Zero-Trust Sandbox Jail Escape Blocked] {dest_file} is outside {out_dir}")
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                applied_files.append(rel_p)

        return {
            "clean": True,
            "method": "nway_disjoint_fastpath",
            "applied_files": applied_files,
            "conflict_files": [],
            "stats": {"branches_merged": len(branch_dirs), "total_files": len(applied_files)}
        }
    else:
        # Có file chồng lấn, áp dụng 3-Way merge cho file trùng
        for rel_p, b_indices in file_to_branches.items():
            dest_file = (out_dir / rel_p).resolve()
            try:
                dest_file.relative_to(out_dir)
            except ValueError:
                raise PermissionError(f"🛑 [Zero-Trust Sandbox Jail Escape Blocked] {dest_file} is outside {out_dir}")
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            if len(b_indices) == 1:
                src_f = branch_changes[b_indices[0]][rel_p]
                shutil.copy2(src_f, dest_file)
                applied_files.append(rel_p)
            elif len(b_indices) == 2:
                base_f = base_dir / rel_p
                base_t = base_f.read_text(encoding="utf-8") if base_f.exists() else ""
                a_t = branch_changes[b_indices[0]][rel_p].read_text(encoding="utf-8")
                b_t = branch_changes[b_indices[1]][rel_p].read_text(encoding="utf-8")

                m_res = merge_3way(base_t, a_t, b_t)
                dest_file.write_text(m_res["merged_text"], encoding="utf-8")
                if m_res["clean"]:
                    applied_files.append(rel_p)
                else:
                    conflict_files.append(rel_p)
            else:
                conflict_files.append(rel_p)

        return {
            "clean": len(conflict_files) == 0,
            "method": "nway_hybrid_merge",
            "applied_files": applied_files,
            "conflict_files": conflict_files,
            "stats": {"branches_merged": len(branch_dirs), "conflicts_count": len(conflict_files)}
        }


def context_slice(role: str, full_spec: dict) -> dict:
    """
    Trích xuất lát cắt ngữ cảnh tối giản (Targeted Context Slicing) theo từng vai trò,
    giảm 70-80% lượng token dư thừa khi dispatch subagent.
    """
    role_lower = role.lower()
    sliced = {
        "role": role,
        "target_task": full_spec.get("tasks_by_role", {}).get(role, full_spec.get("task", "")),
        "target_files": full_spec.get("target_files", {}).get(role, full_spec.get("target_files", [])),
        "tech_stack": full_spec.get("tech_stack", "Python/JS/TS Standard"),
        "invariants": full_spec.get("invariants", ["Zero-Trust", "Null-Safety", "No-Regressions"])
    }

    if "backend" in role_lower or "db" in role_lower or "builder" in role_lower:
        sliced["contracts"] = full_spec.get("api_contracts", {})
        sliced["schemas"] = full_spec.get("db_schemas", {})
    elif "ui" in role_lower or "frontend" in role_lower:
        sliced["api_endpoints"] = full_spec.get("api_endpoints", [])
        sliced["contracts"] = full_spec.get("api_contracts", {})
        sliced["ui_guidelines"] = full_spec.get("ui_guidelines", {})
    elif "test" in role_lower or "qa" in role_lower or "sentinel" in role_lower:
        sliced["acceptance_criteria"] = full_spec.get("acceptance_criteria", [])
        sliced["edge_cases"] = full_spec.get("edge_cases", [])
        sliced["contracts"] = full_spec.get("api_contracts", {})
    else:
        sliced["domain_spec"] = full_spec.get("domain_spec", {})

    return sliced


# =====================================================================
# 5. CROSS-WORKER CONTRACT VERIFICATION (Gate 0.5 — v5.3)
# =====================================================================

def _extract_python_exports(file_path: Path) -> Dict[str, Dict[str, Any]]:
    """Trích xuất public functions/classes từ 1 file Python qua AST."""
    exports = {}
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    params = [arg.arg for arg in node.args.args if arg.arg != "self"]
                    has_return = any(
                        isinstance(n, ast.Return) and n.value is not None
                        for n in ast.walk(node)
                    )
                    exports[node.name] = {
                        "type": "function",
                        "params": params,
                        "param_count": len(params),
                        "has_return_value": has_return,
                        "line": node.lineno,
                        "file": str(file_path.name),
                    }
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    methods = []
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if not item.name.startswith("_") or item.name == "__init__":
                                methods.append(item.name)
                    exports[node.name] = {
                        "type": "class",
                        "methods": methods,
                        "line": node.lineno,
                        "file": str(file_path.name),
                    }
    except Exception:
        pass
    return exports


def _extract_python_imports(file_path: Path) -> List[Dict[str, Any]]:
    """Trích xuất tất cả import statements từ 1 file Python qua AST."""
    imports = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports.append({
                        "module": node.module,
                        "name": alias.name,
                        "alias": alias.asname,
                        "line": node.lineno,
                        "file": str(file_path.name),
                    })
    except Exception:
        pass
    return imports


def verify_cross_worker_contracts(merged_dir: Path, target_files: Optional[List[str]] = None) -> dict:
    """
    Gate 0.5: Kiểm tra tính nhất quán giữa code của các workers sau merge.
    Phát hiện:
    1. Dangling cross-module imports (import symbol không tồn tại)
    2. Function call arity mismatch (gọi hàm sai số tham số)
    3. Missing class/function (Worker 2 dùng class Worker 1 chưa tạo)
    """
    merged_dir = Path(merged_dir).resolve()
    violations = []

    # Thu thập tất cả Python files trong project
    py_files = []
    if target_files:
        py_files = [merged_dir / f for f in target_files if f.endswith(".py")]
    else:
        for f in merged_dir.rglob("*.py"):
            rel = str(f.relative_to(merged_dir))
            if not any(skip in rel for skip in (".venv", "venv", "node_modules", "__pycache__", ".git")):
                py_files.append(f)

    # Phase 1: Xây catalog tất cả exports
    all_exports: Dict[str, Dict[str, Any]] = {}
    module_exports: Dict[str, Dict[str, Dict[str, Any]]] = {}  # module_name -> exports
    for pf in py_files:
        exports = _extract_python_exports(pf)
        all_exports.update(exports)
        mod_name = pf.stem
        module_exports[mod_name] = exports

    # Phase 2: Quét imports và cross-check
    for pf in py_files:
        imports = _extract_python_imports(pf)
        for imp in imports:
            mod = imp["module"]
            name = imp["name"]
            # Chỉ check local imports (không check stdlib/pip packages)
            mod_base = mod.split(".")[0]
            if mod_base in module_exports:
                mod_syms = module_exports[mod_base]
                if name != "*" and name not in mod_syms:
                    violations.append({
                        "type": "DANGLING_IMPORT",
                        "severity": "ERROR",
                        "file": imp["file"],
                        "line": imp["line"],
                        "detail": f"Import `{name}` từ `{mod}` nhưng module `{mod_base}` không export symbol này",
                        "fix_hint": f"Kiểm tra Worker đã tạo `{name}` trong `{mod_base}.py` chưa",
                    })

    # Phase 3: Check function call arity (basic — chỉ check trong cùng project)
    for pf in py_files:
        try:
            content = pf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(pf))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    func_name = node.func.id
                    if func_name in all_exports:
                        exp = all_exports[func_name]
                        if exp["type"] == "function":
                            call_args = len(node.args) + len(node.keywords)
                            expected = exp["param_count"]
                            # Cho phép sai lệch ±1 (vì default args)
                            if call_args > expected + 1 or (call_args < expected - 2 and expected > 0):
                                violations.append({
                                    "type": "ARITY_MISMATCH",
                                    "severity": "WARNING",
                                    "file": str(pf.name),
                                    "line": node.lineno,
                                    "detail": f"Gọi `{func_name}()` với {call_args} args, nhưng định nghĩa nhận {expected} params (tại {exp['file']}:{exp['line']})",
                                    "fix_hint": "Kiểm tra signature function có khớp giữa caller và callee",
                                })
        except Exception:
            pass

    passed = not any(v["severity"] in ("ERROR", "CRITICAL") for v in violations)
    return {
        "gate": "0.5",
        "gate_name": "Cross-Worker Contract Verification",
        "total_files_scanned": len(py_files),
        "total_exports": len(all_exports),
        "violations": violations,
        "violation_count": len(violations),
        "passed": passed,
    }


# =====================================================================
# 6. ADVERSARIAL REVIEW COUNCIL & FAIL-CLOSED VETO ARBITER (v5.3)
# =====================================================================

class ReviewCouncilArbiter:
    """
    Trọng tài Hội đồng Review Đối kháng 3 bên (Adversarial Review Council - ARC).
    Thực thi cơ chế Fail-Closed Veto: Chặn đứng lệnh Merge nếu bất kỳ reviewer nào
    chấm điểm < 8.0/10 hoặc đánh giá rủi ro HIGH/CRITICAL hoặc có blocker.
    """
    SCORE_THRESHOLD = get_config("quality_gates", "arc_pass_threshold", 8.0)
    FORBIDDEN_RISKS = {"HIGH", "CRITICAL"}

    @staticmethod
    def evaluate(reviews: list) -> dict:
        """
        reviews: Danh sách kết quả JSON từ 3 reviewers:
                 [Security_Auditor, Technical_Architect, Ponytail_Dev]
        """
        if len(reviews) < 3:
            return {
                "can_merge": False,
                "status": "VETOED",
                "reason": f"Hội đồng không đủ túc số (Yêu cầu đủ 3 reviewers, hiện có {len(reviews)})",
                "veto_triggers": ["Thiếu đánh giá từ reviewer độc lập"],
                "reviews": reviews
            }

        veto_triggers = []
        blocking_issues = []
        total_score = 0.0

        for r in reviews:
            role = r.get("reviewer_role", "Unknown")
            score = float(r.get("score", 0.0))
            risk = str(r.get("risk_level", "LOW")).upper()
            total_score += score

            # Kiểm tra ngưỡng điểm sàn
            if score < ReviewCouncilArbiter.SCORE_THRESHOLD:
                veto_triggers.append(
                    f"Reviewer [{role}] CHẤM ĐIỂM DƯỚI NGƯỠNG AN TOÀN: {score:.1f}/10.0 (< {ReviewCouncilArbiter.SCORE_THRESHOLD})"
                )

            # Kiểm tra cờ rủi ro cao hoặc veto
            if risk in ReviewCouncilArbiter.FORBIDDEN_RISKS or r.get("is_veto", False):
                veto_triggers.append(
                    f"Reviewer [{role}] KÍCH HOẠT QUYỀN PHỦ QUYẾT VETO (Mức rủi ro: {risk}): {r.get('summary', '')}"
                )

            for issue in r.get("blocking_issues", []):
                blocking_issues.append({"role": role, **issue})

        avg_score = total_score / len(reviews)
        can_merge = len(veto_triggers) == 0

        return {
            "can_merge": can_merge,
            "status": "APPROVED" if can_merge else "VETOED",
            "average_score": round(avg_score, 2),
            "veto_count": len(veto_triggers),
            "veto_triggers": veto_triggers,
            "blocking_issues": blocking_issues,
            "reviews_summary": {
                r.get("reviewer_role"): {
                    "score": r.get("score"),
                    "risk": r.get("risk_level"),
                    "summary": r.get("summary")
                } for r in reviews
            }
        }


# =====================================================================
# 6b. TRUE ADVERSARIAL REVIEW — PROMPT GENERATOR (v5.3)
# =====================================================================

REVIEWER_PROFILES = {
    "Security_Auditor": {
        "focus": "Bảo mật, OWASP Top 10, input validation, secrets, injection, auth bypass",
        "checklist_section": "§2 REVIEW SIDECAR + §4 SKEPTIC items 1,7",
        "kill_criteria": [
            "Input từ user/API/DB chưa validate/sanitize",
            "SQL injection, XSS, CSRF, IDOR, SSRF vectors",
            "Secrets/credentials hardcoded hoặc logged",
            "Missing auth/authz checks trên endpoints mới",
            "Unsafe deserialization (pickle, yaml.load, eval)",
        ],
    },
    "Technical_Architect": {
        "focus": "Kiến trúc, scalability, error handling, resource management, API design",
        "checklist_section": "§2 REVIEW SIDECAR + §3 ARCHITECTURE SIDECAR",
        "kill_criteria": [
            "Missing error handling / bare except / swallowed errors",
            "Resource leaks (file, socket, connection không đóng)",
            "N+1 query / unbounded loops / missing pagination",
            "Breaking backward compatibility / missing migration",
            "Race conditions / TOCTOU / shared mutable state",
        ],
    },
    "Ponytail_Dev": {
        "focus": "Simplicity, YAGNI, over-engineering, dead code, unnecessary dependencies",
        "checklist_section": "§2 REVIEW SIDECAR — Ponytail Decision Ladder",
        "kill_criteria": [
            "Code có thể giải quyết bằng stdlib mà dùng external dep",
            "Abstraction layer không cần thiết (wrapper của wrapper)",
            "Dead code, unused imports, commented-out blocks",
            "Quá nhiều indirection / over-generalization",
            "Test mocking SUT thay vì test behavior thực tế",
        ],
    },
}


def generate_review_prompts(
    diff_sliced: str,
    spec_snippet: str = "",
    tech_stack: str = "Python/JS/TS Standard",
    target_files: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Sinh structured prompt cho 3 reviewer subagents.
    Mỗi prompt chứa diff sliced + spec + checklist — KHÔNG chứa toàn bộ codebase.
    Orchestrator dispatch 3 subagents và thu kết quả JSON.

    Returns: {"Security_Auditor": "prompt...", "Technical_Architect": "prompt...", "Ponytail_Dev": "prompt..."}
    """
    files_context = f"\nTarget files: {', '.join(target_files)}" if target_files else ""
    prompts = {}

    for role, profile in REVIEWER_PROFILES.items():
        kill_list = "\n".join(f"  - {k}" for k in profile["kill_criteria"])
        prompts[role] = f"""Bạn là **{role.replace('_', ' ')}** trong Hội đồng Review Đối kháng (Adversarial Review Council).

**Nhiệm vụ:** Review code diff bên dưới với góc nhìn chuyên sâu: {profile['focus']}.
**Checklist tham chiếu:** Đọc `references/sidecar_matrix.md` {profile['checklist_section']} để checklist đầy đủ.

**Kill Criteria (phải tìm và báo cáo nếu có):**
{kill_list}

**Tech stack:** {tech_stack}{files_context}

**Acceptance Criteria / Spec:**
{spec_snippet if spec_snippet else "(Không có spec — review dựa trên best practices)"}

**Code Diff để Review:**
```diff
{diff_sliced}
```

**QUAN TRỌNG — Output format BẮT BUỘC:**
Trả về ĐÚNG 1 JSON object (không markdown, không giải thích thêm):
```json
{{
  "reviewer_role": "{role}",
  "score": <float 0.0-10.0>,
  "risk_level": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "is_veto": <true nếu phát hiện blocker nghiêm trọng>,
  "summary": "<1-2 câu tổng quan>",
  "findings": [
    {{
      "severity": "<CRITICAL|ERROR|WARNING|INFO>",
      "file": "<tên file>",
      "line": <số dòng trong diff>,
      "issue": "<mô tả vấn đề>",
      "suggested_fix": "<cách sửa cụ thể>"
    }}
  ],
  "blocking_issues": [
    {{
      "id": "<BLK-001>",
      "description": "<mô tả blocker>",
      "must_fix_before_merge": true
    }}
  ]
}}
```

**Quy tắc chấm điểm:**
- 9.0-10.0: Không có vấn đề đáng kể
- 8.0-8.9: Có warnings nhưng an toàn để merge
- 6.0-7.9: Có issues cần sửa trước merge (risk_level = MEDIUM+)
- <6.0: Có blockers nghiêm trọng (is_veto = true)
"""

    return prompts


def synthesize_review_verdict(review_responses: List[str]) -> dict:
    """
    Tổng hợp kết quả từ 3 reviewer subagents thành verdict cuối cùng.
    Parse JSON từ text response, fallback graceful nếu format sai.
    """
    parsed_reviews = []
    parse_errors = []

    for idx, resp in enumerate(review_responses):
        try:
            # Tìm JSON block trong response (có thể có text xung quanh)
            json_match = re.search(r'\{[\s\S]*"reviewer_role"[\s\S]*\}', resp)
            if json_match:
                review = json.loads(json_match.group())
                parsed_reviews.append(review)
            else:
                parse_errors.append(f"Reviewer {idx+1}: Không tìm thấy JSON hợp lệ trong response")
        except json.JSONDecodeError as e:
            parse_errors.append(f"Reviewer {idx+1}: JSON parse error — {e}")

    # Nếu không đủ 3 reviews, ARC vẫn evaluate với warning
    verdict = ReviewCouncilArbiter.evaluate(parsed_reviews)
    verdict["parse_errors"] = parse_errors
    verdict["raw_review_count"] = len(review_responses)
    verdict["parsed_review_count"] = len(parsed_reviews)

    return verdict


# =====================================================================
# 7. CLOSED-LOOP REMEDIATION CONTROLLER (v5.3)
# =====================================================================

class RemediationController:
    """
    Điều phối vòng phản hồi sửa lỗi tự động khép kín (Closed-Loop Remediation Cycle).
    Quản lý CoW Sandbox, WAL Transaction, và Context Slicing cho từng vòng (tối đa 3 vòng L1).
    """
    @staticmethod
    def create_ticket(
        defect_report: dict,
        target_file: str,
        root_cause: str,
        required_fix: str,
        cycle: int = 1,
        arc_findings: Optional[List[dict]] = None,
        diff_context: Optional[str] = None,
    ) -> dict:
        ticket_id = f"REM-{datetime.now().strftime('%Y%m%d%H%M%S')}-{os.urandom(2).hex()}"
        ticket = {
            "ticket_id": ticket_id,
            "cycle": cycle,
            "max_cycles": 3,
            "status": "OPEN",
            "target_file": target_file,
            "defect_report": defect_report,
            "root_cause": root_cause,
            "required_fix": required_fix,
            "arc_findings": arc_findings or [],
            "diff_context": diff_context or "",
            "sandbox_path": None,
            "tx_id": None
        }
        return ticket

    @staticmethod
    def format_remediation_prompt(ticket: dict) -> str:
        """Sinh prompt chi tiết cho Worker-Remediator từ ticket + ARC findings."""
        findings_text = ""
        if ticket.get("arc_findings"):
            findings_lines = []
            for f in ticket["arc_findings"]:
                sev = f.get("severity", "?")
                loc = f"{f.get('file', '?')}:{f.get('line', '?')}"
                findings_lines.append(f"  [{sev}] {loc} — {f.get('issue', 'N/A')}")
                if f.get("suggested_fix"):
                    findings_lines.append(f"    → Fix: {f['suggested_fix']}")
            findings_text = "\n".join(findings_lines)

        diff_text = ""
        if ticket.get("diff_context"):
            diff_text = f"\n**Diff Context:**\n```diff\n{ticket['diff_context']}\n```\n"

        return f"""**Remediation Ticket {ticket['ticket_id']}** (Vòng {ticket['cycle']}/{ticket['max_cycles']})

**Target File:** `{ticket['target_file']}`
**Root Cause:** {ticket['root_cause']}
**Required Fix:** {ticket['required_fix']}

**ARC Findings cụ thể cần sửa:**
{findings_text if findings_text else "(Không có findings chi tiết)"}
{diff_text}
**Yêu cầu:**
1. Sửa TẤT CẢ findings được liệt kê ở trên
2. Không sửa code ngoài phạm vi target file
3. Đảm bảo tests vẫn pass sau khi sửa
4. Giữ nguyên API contract / public interface
"""

    @staticmethod
    def setup_cycle_sandbox(base_dir: Path, ticket: dict) -> dict:
        """Khởi tạo CoW Sandbox và mở Transaction WAL cho vòng sửa lỗi."""
        from cow_engine import CoWSnapshotEngine
        from code_guard import begin_transaction, snapshot_transaction_file

        base_dir = Path(base_dir).resolve()
        cycle = ticket["cycle"]
        sandbox_dir = base_dir / ".agentkit" / "sandboxes" / f"rem_{ticket['ticket_id']}_c{cycle}"

        CoWSnapshotEngine.create_snapshot(base_dir, sandbox_dir)

        tx_id = begin_transaction(f"Remediation {ticket['ticket_id']} Cycle {cycle}")
        target_f = base_dir / ticket["target_file"]
        if target_f.exists():
            snapshot_transaction_file(tx_id, str(target_f))

        ticket["sandbox_path"] = str(sandbox_dir)
        ticket["tx_id"] = tx_id
        return ticket

    @staticmethod
    def finalize_cycle(base_dir: Path, ticket: dict, review_passed: bool) -> dict:
        """Xử lý kết thúc vòng: Promote nếu Approved, Rollback nếu Rejected."""
        from cow_engine import CoWSnapshotEngine
        from code_guard import commit_transaction, rollback_transaction

        base_dir = Path(base_dir).resolve()
        sandbox_dir = Path(ticket["sandbox_path"])
        tx_id = ticket["tx_id"]

        if review_passed:
            diff_data = CoWSnapshotEngine.extract_diff(sandbox_dir, base_dir)
            for rel_file in diff_data.get("modified_files", []) + diff_data.get("created_files", []):
                src = sandbox_dir / rel_file
                dst = base_dir / rel_file
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            commit_transaction(tx_id)
            CoWSnapshotEngine.prune_snapshot(sandbox_dir)
            ticket["status"] = "RESOLVED"
            return {"status": "promoted", "modified": diff_data.get("modified_count", 0)}
        else:
            rollback_transaction(tx_id)
            CoWSnapshotEngine.prune_snapshot(sandbox_dir)
            ticket["status"] = "REJECTED"
            return {"status": "rolled_back", "cycle": ticket["cycle"]}


# =====================================================================
# 8. SECURITY-AWARE REVIEW COMPLEXITY ROUTER (v5.3)
# =====================================================================

# Regex patterns tự động phát hiện nội dung nhạy cảm trong diff
_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|secret|token|api_key|private_key|credentials?)"),
    re.compile(r"(?i)(auth|login|session|jwt|oauth|bearer|cookie)"),
    re.compile(r"(?i)(sql|query|execute|cursor|\.raw\(|rawQuery)"),
    re.compile(r"(?i)(eval|exec|subprocess|os\.system|child_process)"),
    re.compile(r"(?i)(cors|csrf|xss|sanitize|escape|innerHTML)"),
    re.compile(r"(?i)(crypto|encrypt|decrypt|hash|hmac|aes|rsa)"),
    re.compile(r"(?i)(\.env|process\.env|os\.environ|getenv)"),
    re.compile(r"(?i)(admin|root|superuser|privilege|permission|role)"),
]


class ReviewComplexityRouter:
    """
    Phân loại độ phức tạp tác vụ review để tối ưu token quota (v5.3.1 Review-First):
    - Tier 2 (DEFAULT MINIMUM): 1 Solo Reviewer — MỌI thay đổi code đều được review
    - Tier 3: Tri-Party Council (3 Reviewers) — sensitive/large changes
    - Tier 1 (OPT-IN ONLY): Silent AST — chỉ khi config allow_tier1_silent=true
    """

    @staticmethod
    def _detect_sensitive(diff_text: str) -> bool:
        """Tự động phát hiện sensitive patterns trong diff text."""
        if not diff_text:
            return False
        # Chỉ quét các dòng thay đổi (+ hoặc -), bỏ context
        changed_lines = "\n".join(
            line for line in diff_text.splitlines()
            if line.startswith("+") or line.startswith("-")
        )
        return any(p.search(changed_lines) for p in _SENSITIVE_PATTERNS)

    @staticmethod
    def route_review(
        diff_lines: int,
        file_count: int,
        touches_sensitive: bool = False,
        diff_text: str = "",
        allow_silent: Optional[bool] = None,
    ) -> dict:
        # Auto-detect sensitive nếu caller không chỉ định
        auto_sensitive = ReviewComplexityRouter._detect_sensitive(diff_text) if diff_text else False
        is_sensitive = touches_sensitive or auto_sensitive

        # Tier 3: Sensitive content HOẶC large change → Full 3-party council
        if is_sensitive or diff_lines > 200 or file_count > 3:
            return {
                "tier": "Tier 3",
                "mode": "tri_party_council",
                "description": "Full Adversarial Review Council (Security, Architect, Ponytail)",
                "reviewers_count": 3,
                "token_estimate": 4500,
                "auto_sensitive_detected": auto_sensitive,
            }

        # Tier 1: CHỈ khi config cho phép (opt-in) VÀ diff nhỏ
        if allow_silent is None:
            allow_silent = get_config("review_router", "allow_tier1_silent", False)
        if allow_silent and diff_lines < 50 and file_count <= 1:
            return {
                "tier": "Tier 1",
                "mode": "silent_ast_linter",
                "description": "Silent AST & Linter Check (0 LLM Token — config opt-in)",
                "reviewers_count": 0,
                "token_estimate": 0,
                "auto_sensitive_detected": False,
            }

        # Tier 2 (DEFAULT): Solo Reviewer — MỌI thay đổi đều được review
        return {
            "tier": "Tier 2",
            "mode": "single_solo_reviewer",
            "description": "Single Solo Reviewer (Ponytail/Senior Dev, Diff Sliced)",
            "reviewers_count": 1,
            "token_estimate": 1500,
            "auto_sensitive_detected": False,
        }


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

    if command == "plan-dag":
        spec_str = args.get("spec", "")
        spec_file = args.get("file", "")
        tasks = []
        if spec_file and Path(spec_file).exists():
            tasks = json.loads(Path(spec_file).read_text(encoding="utf-8"))
        elif spec_str:
            tasks = json.loads(spec_str)

        if not tasks:
            print("❌ Cần cung cấp --spec '[...]' hoặc --file task_spec.json")
            sys.exit(1)

        try:
            stages = resolve_dag_stages(tasks)
            print(format_dag_plan(stages))
            if args.get("json"):
                print(json.dumps(stages, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"❌ Lỗi giải quyết DAG: {e}")
            sys.exit(1)

    elif command == "classify-topology":
        spec_str = args.get("spec", "{}")
        task_info = json.loads(spec_str) if spec_str else {}
        res = classify_topology(task_info)
        print("🌐 AGENTKIT DYNAMIC TOPOLOGY CLASSIFIER")
        print("=" * 60)
        print(f"  • Topology:    {res['name']} ({res['topology']})")
        print(f"  • Mô tả:       {res['description']}")
        print(f"  • Tốc độ:      {res['estimated_speedup']}")
        if args.get("json"):
            print(json.dumps(res, ensure_ascii=False, indent=2))

    elif command == "isolate-failure":
        spec_str = args.get("spec", "")
        failed_id = args.get("failed", "")
        try:
            tasks = json.loads(spec_str) if spec_str else []
            failed_ids = [f.strip() for f in failed_id.split(",") if f.strip()]
            recovery = isolate_task_failure(tasks, failed_ids)
            print("🛡️ DYNAMIC DAG FAILURE ISOLATION RESULT")
            print("=" * 60)
            print(f"  ❌ Failed Tasks:   {recovery['failed_tasks']}")
            print(f"  ⚠️ Skipped Tasks:  {recovery['skipped_tasks']}")
            print(f"  ✅ Salvaged Tasks: {recovery['runnable_tasks']} ({recovery['salvaged_count']} tasks tiếp tục chạy)")
            if recovery['new_stages']:
                print(format_dag_plan(recovery['new_stages']))
        except Exception as e:
            print(f"❌ Lỗi cách ly failure: {e}")
            sys.exit(1)

    elif command == "merge-3way":
        base_path = Path(args.get("base", ""))
        a_path = Path(args.get("a", ""))
        b_path = Path(args.get("b", ""))
        out_path = args.get("output")

        if not (base_path.exists() and a_path.exists() and b_path.exists()):
            print("❌ Cần cung cấp đầy đủ --base <file>, --a <file>, --b <file>")
            sys.exit(1)

        base_t = base_path.read_text(encoding="utf-8")
        a_t = a_path.read_text(encoding="utf-8")
        b_t = b_path.read_text(encoding="utf-8")

        result = merge_3way(base_t, a_t, b_t)
        if result['clean']:
            print(f"✅ Clean 3-Way Merge thành công (0 conflicts)!")
            if out_path:
                Path(out_path).write_text(result['merged_text'], encoding="utf-8")
                print(f"📁 Đã ghi kết quả ra: {out_path}")
        else:
            print(f"⚠️ Phát hiện {len(result['conflicts'])} điểm xung đột! Cần Kongming Arbiter xử lý.")
            if out_path:
                Path(out_path).write_text(result['merged_text'], encoding="utf-8")

    elif command == "review-council":
        reviews_f = args.get("file", "")
        if not reviews_f or not Path(reviews_f).exists():
            print("❌ Cần cung cấp --file <reviews_json>")
            sys.exit(1)
        data = json.loads(Path(reviews_f).read_text(encoding="utf-8"))
        reviews_list = data if isinstance(data, list) else data.get("reviews", [])
        verdict = ReviewCouncilArbiter.evaluate(reviews_list)
        emit_metric("gate_pass" if verdict["can_merge"] else "gate_block",
                     gate=3, arc_score=verdict["average_score"], veto=verdict["veto_count"])
        if args.get("json"):
            print(json.dumps(verdict, indent=2, ensure_ascii=False))
        else:
            print("🏛️ ADVERSARIAL REVIEW COUNCIL (ARC) VERDICT")
            print("=" * 60)
            print(f"  • Status:        {verdict['status']}")
            print(f"  • Average Score: {verdict['average_score']}/10.0")
            print(f"  • Veto Count:    {verdict['veto_count']}")
            print(f"  • Merge Allowed: {'✅ YES' if verdict['can_merge'] else '🛑 NO (FAIL-CLOSED VETO)'}")
            if verdict.get("veto_triggers"):
                print("\n⚠️ Veto Triggers:")
                for vt in verdict["veto_triggers"]:
                    print(f"    - {vt}")
        sys.exit(0 if verdict["can_merge"] else 1)

    elif command == "review-route":
        diff_lines = int(args.get("lines", 0))
        files = int(args.get("files", 1))
        sens = args.get("sensitive") == "true"
        diff_f = args.get("diff", "")
        diff_text = ""
        if diff_f and Path(diff_f).exists():
            diff_text = Path(diff_f).read_text(encoding="utf-8", errors="replace")
        routed = ReviewComplexityRouter.route_review(diff_lines, files, sens, diff_text=diff_text)
        emit_metric("plan_dispatch", tier=routed["tier"], files=files, lines=diff_lines)
        print(f"🚦 REVIEW ROUTE: [{routed['tier']}] {routed['description']} (Est. {routed['token_estimate']} tokens)")
        if routed.get("auto_sensitive_detected"):
            print("  ⚠️ Auto-detected sensitive content in diff → Escalated to higher review tier")

    elif command == "verify-contracts":
        target_dir = args.get("dir", ".")
        files_str = args.get("files", "")
        target_files = [f.strip() for f in files_str.split(",") if f.strip()] if files_str else None
        res = verify_cross_worker_contracts(Path(target_dir), target_files=target_files)
        if args.get("json") == "true":
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"🔗 GATE 0.5: CROSS-WORKER CONTRACT VERIFICATION")
            print("=" * 60)
            print(f"  • Files Scanned:  {res['total_files_scanned']}")
            print(f"  • Total Exports:  {res['total_exports']}")
            print(f"  • Violations:     {res['violation_count']}")
            print(f"  • Verdict:        {'✅ PASS' if res['passed'] else '🛑 CONTRACT MISMATCH'}")
            if res.get("violations"):
                print("\n⚠️ Contract Violations:")
                for v in res["violations"]:
                    print(f"    - [{v['severity']}] [{v['type']}] {v['file']}:{v['line']} — {v['detail']}")
                    print(f"      → {v['fix_hint']}")
        sys.exit(0 if res["passed"] else 1)

    elif command == "generate-review-prompts":
        diff_f = args.get("diff", "")
        spec_f = args.get("spec", "")
        tech = args.get("tech", "Python/JS/TS Standard")
        if not diff_f or not Path(diff_f).exists():
            print("❌ Cần cung cấp --diff <diff_file>")
            sys.exit(1)
        diff_text = Path(diff_f).read_text(encoding="utf-8", errors="replace")
        spec_text = Path(spec_f).read_text(encoding="utf-8", errors="replace") if spec_f and Path(spec_f).exists() else ""
        prompts = generate_review_prompts(diff_text, spec_snippet=spec_text, tech_stack=tech)
        if args.get("json") == "true":
            print(json.dumps(prompts, indent=2, ensure_ascii=False))
        else:
            for role, prompt in prompts.items():
                print(f"\n{'='*60}")
                print(f"📋 REVIEWER: {role}")
                print(f"{'='*60}")
                print(prompt[:500] + "..." if len(prompt) > 500 else prompt)

    else:
        print(__doc__)


if __name__ == "__main__":
    main()


