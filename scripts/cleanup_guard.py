#!/usr/bin/env python3
"""
AgentKit Post-Task Ephemeral Cleanup Guard v1.1 (Zero-Bloat Janitor)
Quét, phân loại và dọn dẹp các tệp rác, logs, scratch scripts, version/backup cũ,
và ephemeral snapshots sau khi hoàn thành công việc.

Usage:
    python scripts/cleanup_guard.py scan [--target-dir <path>] [--verbose] [--json]
    python scripts/cleanup_guard.py prune [--target-dir <path>] [--dry-run] [--categories <list>] [--verbose]
    python scripts/cleanup_guard.py verify-clean [--target-dir <path>]
"""

import os
import sys
import shutil
import stat
import time
import re
import uuid
import json
import fnmatch
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# Đảm bảo import được _shared.py dù chạy từ bất kỳ đâu
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from _shared import (
        get_config, get_brain_dir, emit_metric, parse_cli_args,
        to_extended_windows_path, handle_remove_readonly, unlink_junctions_recursively, format_bytes,
        atomic_write_json
    )
except ImportError:
    # Fallback tối thiểu nếu chạy độc lập hoàn toàn
    def get_config(section, key, default=None): return default
    def emit_metric(event, **kwargs): return False
    def get_brain_dir(): return Path.cwd() / ".brain"
    def parse_cli_args(argv=None): return "help", {}
    def format_bytes(b): return f"{b} B"
    def to_extended_windows_path(p): return str(p.resolve())
    def handle_remove_readonly(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except Exception:
            pass
    def unlink_junctions_recursively(p): pass
    def atomic_write_json(file_path, data, **kwargs):
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# =====================================================================
# 1. ZERO-TRUST SAFETY WHITELIST & CONFIG LOADER
# =====================================================================

DEFAULT_PROTECTED_DIR_NAMES = {
    ".git", ".github", "node_modules", ".venv", "venv", "env",
    "skills", "workflows", "templates", "schemas", ".agentkit", ".codegraph"
}

DEFAULT_STALE_EXTENSIONS = {
    ".bak", ".old", ".orig", ".swp", ".tmp", ".temp"
}

DEFAULT_SCRATCH_PREFIXES = (
    "tmp_", "temp_", "scratch_", "test_temp_", "debug_probe_", "oneoff_",
    "inspect_", "probe_", "check_", "dump_", "trace_"
)

SENSITIVE_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.pfx", "*.p12",
    "*id_rsa*", "*id_ed25519*", "*credentials*.json",
    "*service_account*.json", ".npmrc", ".pypirc", "*.secret",
    "*password*", "*token*"
)


def get_cleanup_config() -> dict:
    """Đọc cấu hình dọn dẹp động từ config.yaml với fallback an toàn."""
    cfg_exts = get_config("cleanup", "stale_extensions", list(DEFAULT_STALE_EXTENSIONS))
    if isinstance(cfg_exts, (list, tuple, set)):
        stale_exts = {str(x).lower().strip() for x in cfg_exts}
    else:
        stale_exts = set(DEFAULT_STALE_EXTENSIONS)

    cfg_pfx = get_config("cleanup", "scratch_prefixes", list(DEFAULT_SCRATCH_PREFIXES))
    if isinstance(cfg_pfx, (list, tuple, set)):
        scratch_pfx = tuple(str(x).lower().strip() for x in cfg_pfx)
    else:
        scratch_pfx = DEFAULT_SCRATCH_PREFIXES

    cfg_dirs = get_config("cleanup", "protected_dirs", list(DEFAULT_PROTECTED_DIR_NAMES))
    if isinstance(cfg_dirs, (list, tuple, set)):
        protected_dirs = {str(x).lower().strip() for x in cfg_dirs}
    else:
        protected_dirs = set(DEFAULT_PROTECTED_DIR_NAMES)
    protected_dirs.add(".agentkit")

    return {
        "stale_extensions": stale_exts,
        "scratch_prefixes": scratch_pfx,
        "protected_dirs": protected_dirs,
        "retention_days_logs": int(get_config("cleanup", "retention_days_logs", 7)),
        "wal_retention_hours": int(get_config("cleanup", "wal_snapshot_retention_hours", 24)),
        "dry_run_default": bool(get_config("cleanup", "dry_run_default", False)),
        "quarantine": bool(get_config("cleanup", "quarantine", True)),
        "quarantine_dir": str(get_config("cleanup", "quarantine_dir", ".agentkit/trash")),
        "ttl_hours": int(get_config("cleanup", "ttl_hours", 24)),
        "secure_shred": bool(get_config("cleanup", "secure_shred", False)),
    }


def is_sensitive_file(file_path: Path) -> bool:
    """Kiểm tra tệp tin có chứa thông tin nhạy cảm/bí mật cần shredding không."""
    name = Path(file_path).name.lower()
    for pat in SENSITIVE_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def secure_shred_file(file_path: Path) -> bool:
    """Ghi đè byte 0x00 lên file trước khi xóa để chống phục hồi dữ liệu nhạy cảm."""
    try:
        p = Path(file_path)
        if not p.is_file() or p.is_symlink():
            return False
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        size = p.stat().st_size
        if size > 0:
            with open(p, "r+b") as f:
                f.seek(0)
                chunk_size = 65536
                remaining = size
                zero_chunk = b"\x00" * min(chunk_size, remaining)
                while remaining > 0:
                    write_size = min(chunk_size, remaining)
                    f.write(zero_chunk[:write_size])
                    remaining -= write_size
                f.flush()
                os.fsync(f.fileno())
        return True
    except Exception:
        return False


def resolve_workspace_root(given_path=None) -> Path:
    """Xác định workspace root: ưu tiên path truyền vào -> tìm cha có .git -> .brain/ parent -> CWD."""
    if given_path:
        return Path(given_path).resolve()
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        if (p / ".git").exists():
            return p
    try:
        brain = get_brain_dir()
        if brain.parent != brain and (brain.parent / ".git").exists():
            return brain.parent
    except Exception:
        pass
    return cwd


def is_protected(path: Path, workspace_root: Path, protected_dirs: set = None) -> bool:
    """Kiểm tra đường dẫn có thuộc diện bảo vệ nghiêm ngặt không (Zero-Trust)."""
    try:
        p_abs = path.resolve()
        ws_abs = workspace_root.resolve()
    except Exception:
        return True

    # Không được xóa workspace root hoặc thư mục cha
    if p_abs == ws_abs or p_abs in ws_abs.parents:
        return True

    # Chặn root hệ thống và thư mục home
    if p_abs == Path.home() or p_abs.parent == p_abs:
        return True

    active_protected = protected_dirs if protected_dirs is not None else DEFAULT_PROTECTED_DIR_NAMES

    # Kiểm tra các phần tử nằm bên trong workspace_root
    try:
        rel = p_abs.relative_to(ws_abs)
        for part in rel.parts:
            if part.lower() in active_protected:
                return True
    except ValueError:
        # Nằm ngoài workspace root: Chặn nghiêm ngặt các thư mục cốt lõi
        critical_dirs = {".git", "node_modules", ".venv", "venv", "windows", "system32", "program files"}
        for part in p_abs.parts:
            if part.lower() in critical_dirs:
                return True

    return False


# =====================================================================
# 2. BATCH GIT STATUS TRACKING (O(1) Lookup)
# =====================================================================

_git_tracked_cache = {}


def get_git_tracked_files(workspace_root: Path) -> set:
    """Chạy duy nhất 1 lần git ls-files và cache lại tập tệp được theo dõi để tra cứu O(1)."""
    ws_key = str(workspace_root.resolve()).lower()
    if ws_key in _git_tracked_cache:
        return _git_tracked_cache[ws_key]

    tracked = set()
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                line = line.strip()
                if line:
                    tracked.add(str(Path(line)).replace("/", "\\").lower())
    except Exception:
        pass
    _git_tracked_cache[ws_key] = tracked
    return tracked


def is_git_tracked(path: Path, workspace_root: Path) -> bool:
    """Kiểm tra xem tệp tin có đang được git theo dõi không (O(1) hash set lookup)."""
    try:
        tracked = get_git_tracked_files(workspace_root)
        if tracked:
            rel = str(path.resolve().relative_to(workspace_root.resolve())).replace("/", "\\").lower()
            return rel in tracked
    except Exception:
        pass
    return False


# =====================================================================
# 3. TRASH TAXONOMY & CLASSIFICATION
# =====================================================================

def get_dir_size(d_path: Path) -> int:
    """Tính tổng dung lượng thư mục nhanh chóng."""
    total = 0
    try:
        for entry in os.scandir(d_path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_dir_size(Path(entry.path))
    except Exception:
        pass
    return total


def classify_trash(target_dir: Path, config: dict = None) -> dict:
    """
    Quét và phân loại tệp rác thành 5 danh mục:
      1. stale_logs
      2. scratch_scripts
      3. old_versions
      4. ephemeral_snapshots
      5. dead_cache
    """
    target_dir = Path(target_dir).resolve()
    results = {
        "stale_logs": [],
        "scratch_scripts": [],
        "old_versions": [],
        "ephemeral_snapshots": [],
        "dead_cache": []
    }

    if not target_dir.exists():
        return results

    cfg = config or get_cleanup_config()
    stale_extensions = cfg["stale_extensions"]
    scratch_prefixes = cfg["scratch_prefixes"]
    protected_dirs = cfg["protected_dirs"]

    now = time.time()
    retention_days_logs = cfg["retention_days_logs"]
    log_expiry_seconds = retention_days_logs * 86400

    wal_retention_hours = cfg["wal_retention_hours"]
    wal_expiry_seconds = wal_retention_hours * 3600

    # 1. Quét tìm ephemeral snapshots trong .brain / .code_guard_snapshots / .agentkit_wal
    wal_dirs = [
        target_dir / ".agentkit_wal",
        target_dir / ".code_guard_snapshots",
        target_dir / ".brain" / "snapshots"
    ]
    for wdir in wal_dirs:
        if wdir.is_dir():
            for item in wdir.glob("*"):
                try:
                    mtime = item.stat().st_mtime
                    if now - mtime > wal_expiry_seconds:
                        sz = item.stat().st_size if item.is_file() else get_dir_size(item)
                        results["ephemeral_snapshots"].append({
                            "path": str(item),
                            "name": item.name,
                            "size": sz,
                            "is_dir": item.is_dir(),
                            "reason": f"Snapshot cũ hơn {wal_retention_hours}h"
                        })
                except Exception:
                    pass

    # Quét dọn tệp nguyên tử tạm thời (.tmp.*) bị rớt lại trong .brain hoặc target_dir
    tmp_candidates = list(target_dir.glob(".tmp.*.tmp"))
    brain_dir = target_dir / ".brain"
    if brain_dir.is_dir():
        tmp_candidates.extend(brain_dir.glob(".tmp.*.tmp"))
    for tmp_f in set(tmp_candidates):
        try:
            if tmp_f.is_file() and (now - tmp_f.stat().st_mtime > 3600):
                results["ephemeral_snapshots"].append({
                    "path": str(tmp_f),
                    "name": tmp_f.name,
                    "size": tmp_f.stat().st_size,
                    "is_dir": False,
                    "reason": "Tệp atomic write tạm thời bị bỏ quên (>1h)"
                })
        except Exception:
            pass

    # 2. Duyệt cây thư mục (bỏ qua protected dirs)
    for root, dirs, files in os.walk(target_dir, topdown=True):
        root_path = Path(root)

        # Lọc dirs để không duyệt sâu vào thư mục được bảo vệ
        dirs[:] = [d for d in dirs if d.lower() not in protected_dirs]

        # Kiểm tra dead_cache directories
        for d in list(dirs):
            d_path = root_path / d
            d_name = d.lower()
            if d_name in ("__pycache__", ".pytest_cache", ".coverage_cache"):
                results["dead_cache"].append({
                    "path": str(d_path),
                    "name": d,
                    "size": get_dir_size(d_path),
                    "is_dir": True,
                    "reason": "Temporary runtime cache directory"
                })

        for f in files:
            file_path = root_path / f
            file_name = f
            f_lower = f.lower()

            if is_protected(file_path, target_dir, protected_dirs):
                continue

            try:
                stat_info = file_path.stat()
                file_size = stat_info.st_size
                mtime = stat_info.st_mtime
            except Exception:
                continue

            # A. STALE & EPHEMERAL LOGS
            is_log = (
                f_lower.endswith((".log", ".log.tmp")) or
                "pytest_run" in f_lower or
                "test_output_" in f_lower
            )
            is_ephemeral_log = any(
                f_lower.startswith(p) for p in ("tmp_log_", "debug_temp_", "test_temp_", "build_tmp_", "session_log_")
            )

            if is_log:
                if is_ephemeral_log or (now - mtime > log_expiry_seconds):
                    results["stale_logs"].append({
                        "path": str(file_path),
                        "name": file_name,
                        "size": file_size,
                        "is_dir": False,
                        "reason": f"Ephemeral log hoặc log cũ > {retention_days_logs} ngày"
                    })
                    continue

            # B. OLD VERSIONS & BACKUPS
            ext = file_path.suffix.lower()
            is_backup = (
                ext in stale_extensions or
                f_lower.endswith("~") or
                bool(re.search(r"(_backup|_bak|_old|_v\d+)\.[a-zA-Z0-9]+$", f_lower))
            )
            if is_backup and not is_git_tracked(file_path, target_dir):
                results["old_versions"].append({
                    "path": str(file_path),
                    "name": file_name,
                    "size": file_size,
                    "is_dir": False,
                    "reason": f"Tệp sao lưu / version cũ không thuộc git ({ext})"
                })
                continue

            # C. SCRATCH & ONE-OFF SCRIPTS
            is_scratch = False
            parts_lower = [p.lower() for p in file_path.parts]
            # Nằm trong thư mục scratch/
            if "scratch" in parts_lower:
                is_scratch = True
            # Hoặc tên tệp bắt đầu bằng prefix tạm thời và ở ngoài thư mục tests/
            elif any(f_lower.startswith(prefix) for prefix in scratch_prefixes):
                if "tests" not in parts_lower:
                    is_scratch = True

            if is_scratch and not is_git_tracked(file_path, target_dir):
                results["scratch_scripts"].append({
                    "path": str(file_path),
                    "name": file_name,
                    "size": file_size,
                    "is_dir": False,
                    "reason": "Script tạm thời / nháp một lần không thuộc git"
                })
                continue

    return results


# =====================================================================
# 4. SAFE DELETION ENGINE (Windows Anti-Lock & Junction Unlink)
# =====================================================================

def safe_delete_path(path: Path, max_retries: int = 3, delay: float = 0.1, shred: bool = False) -> bool:
    """
    Xóa file hoặc directory an toàn trên Windows với junction unlinking,
    readonly removal và retry loop chống WinError 32 (file locks).
    Nếu shred=True, ghi đè byte 0x00 trước khi xóa file nhạy cảm/tệp tin.
    """
    if not path.exists() and not path.is_symlink():
        return True

    ext_path = to_extended_windows_path(path)
    for attempt in range(max_retries):
        try:
            if path.is_file() or path.is_symlink():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                if shred and path.is_file() and not path.is_symlink():
                    secure_shred_file(path)
                path.unlink()
            elif path.is_dir():
                unlink_junctions_recursively(path)
                if shred:
                    for sub_file in path.glob("**/*"):
                        if sub_file.is_file() and not sub_file.is_symlink():
                            secure_shred_file(sub_file)
                shutil.rmtree(ext_path, onerror=handle_remove_readonly)

            if not path.exists():
                return True
        except PermissionError:
            time.sleep(delay * (attempt + 1))
        except Exception:
            pass

    return not path.exists()


# Alias tương thích ngược hoàn toàn
safe_delete_file = safe_delete_path


# =====================================================================
# 5. PRUNE & QUARANTINE ENGINE
# =====================================================================

def prune_trash(
    target_dir: Path,
    dry_run: bool = False,
    categories: list = None,
    config: dict = None,
    quarantine: bool = None,
    shred: bool = None
) -> dict:
    """
    Dọn dẹp các tệp tin rác theo danh mục chỉ định.
    Hỗ trợ Quarantine Soft-Delete (di chuyển vào .agentkit/trash/<tx_id>/payload/ và ghi wal.json)
    và shredding (ghi đè byte 0x00 trước khi xóa file nhạy cảm).
    """
    target_dir = Path(target_dir).resolve()
    cfg = config or get_cleanup_config()
    classified = classify_trash(target_dir, config=cfg)

    use_quarantine = quarantine if quarantine is not None else cfg.get("quarantine", True)
    use_shred = shred if shred is not None else cfg.get("secure_shred", False)

    target_categories = categories or list(classified.keys())
    total_found = 0
    cleaned = []
    failed = []
    skipped = []
    freed_bytes = 0

    tx_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    q_dir_rel = cfg.get("quarantine_dir", ".agentkit/trash")
    trash_root = (target_dir / q_dir_rel).resolve()
    tx_dir = trash_root / tx_id
    payload_dir = tx_dir / "payload"
    wal_items = []

    for cat in target_categories:
        items = classified.get(cat, [])
        total_found += len(items)

        for item_info in items:
            item_path = Path(item_info["path"])

            # Double check safety
            if is_protected(item_path, target_dir, cfg["protected_dirs"]):
                skipped.append({"path": str(item_path), "reason": "Protected by Zero-Trust Whitelist"})
                continue

            sz = item_info.get("size", 0)
            is_dir = item_info.get("is_dir", False)
            item_sensitive = is_sensitive_file(item_path)

            if dry_run:
                action = "quarantine" if use_quarantine else "delete"
                if use_shred and item_sensitive:
                    action = "shred_delete"
                cleaned.append({
                    "path": str(item_path),
                    "category": cat,
                    "size": sz,
                    "reason": item_info.get("reason", ""),
                    "action": action,
                    "dry_run": True
                })
                freed_bytes += sz
            else:
                # Shredding nếu file nhạy cảm và có cờ shred
                if use_shred and item_sensitive:
                    success = safe_delete_path(item_path, shred=True)
                    if success:
                        cleaned.append({
                            "path": str(item_path),
                            "category": cat,
                            "size": sz,
                            "action": "shredded"
                        })
                        freed_bytes += sz
                    else:
                        failed.append({
                            "path": str(item_path),
                            "category": cat,
                            "error": "File locked or access denied during shred"
                        })
                elif use_quarantine:
                    try:
                        try:
                            rel_path = item_path.resolve().relative_to(target_dir.resolve())
                        except ValueError:
                            rel_path = Path(item_path.name)

                        dest_path = payload_dir / rel_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)

                        if item_path.exists() or item_path.is_symlink():
                            os.chmod(item_path, stat.S_IWRITE | stat.S_IREAD)
                            if dest_path.exists():
                                safe_delete_path(dest_path)
                            shutil.move(str(item_path), str(dest_path))

                            wal_items.append({
                                "original_path": str(item_path.resolve()),
                                "stored_path": str(dest_path.resolve()),
                                "payload_rel": str(rel_path).replace("\\", "/"),
                                "size": sz,
                                "timestamp": time.time(),
                                "is_dir": is_dir
                            })
                            cleaned.append({
                                "path": str(item_path),
                                "category": cat,
                                "size": sz,
                                "action": "quarantined",
                                "tx_id": tx_id
                            })
                            freed_bytes += sz
                        else:
                            failed.append({
                                "path": str(item_path),
                                "category": cat,
                                "error": "File not found"
                            })
                    except Exception as e:
                        failed.append({
                            "path": str(item_path),
                            "category": cat,
                            "error": f"Failed to quarantine: {e}"
                        })
                else:
                    success = safe_delete_path(item_path, shred=use_shred)
                    if success:
                        cleaned.append({
                            "path": str(item_path),
                            "category": cat,
                            "size": sz,
                            "action": "deleted"
                        })
                        freed_bytes += sz
                    else:
                        failed.append({
                            "path": str(item_path),
                            "category": cat,
                            "error": "File locked or access denied"
                        })

    if not dry_run and use_quarantine and wal_items:
        wal_file = tx_dir / "wal.json"
        wal_data = {
            "tx_id": tx_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "created_at": time.time(),
            "target_dir": str(target_dir),
            "items_count": len(wal_items),
            "items": wal_items
        }
        try:
            atomic_write_json(wal_file, wal_data)
        except Exception:
            tx_dir.mkdir(parents=True, exist_ok=True)
            wal_file.write_text(json.dumps(wal_data, ensure_ascii=False, indent=2), encoding="utf-8")

    if not dry_run and cleaned:
        emit_metric("cleanup_prune", count=len(cleaned), bytes_freed=freed_bytes, target=str(target_dir))

    return {
        "target_dir": str(target_dir),
        "dry_run": dry_run,
        "quarantine": use_quarantine,
        "tx_id": tx_id if (not dry_run and use_quarantine and wal_items) else None,
        "found": total_found,
        "cleaned_count": len(cleaned),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "freed_bytes": freed_bytes,
        "freed_human": format_bytes(freed_bytes),
        "cleaned": cleaned,
        "failed": failed,
        "skipped": skipped
    }


def undo_prune(tx_id: str = None, target_dir: Path = None, config: dict = None) -> dict:
    """
    Hoàn tác phục hồi file về vị trí ban đầu từ quarantine WAL.
    Nếu tx_id=None, tự động phục hồi transaction gần nhất.
    """
    ws_root = resolve_workspace_root(target_dir)
    cfg = config or get_cleanup_config()
    q_dir_rel = cfg.get("quarantine_dir", ".agentkit/trash")
    trash_root = (ws_root / q_dir_rel).resolve()

    if not trash_root.exists():
        return {
            "status": "NOT_FOUND",
            "message": f"Thư mục quarantine không tồn tại: {trash_root}",
            "tx_id": tx_id,
            "restored_count": 0,
            "failed_count": 0,
            "restored": [],
            "failed": []
        }

    # Tìm các transaction
    tx_dirs = [d for d in trash_root.iterdir() if d.is_dir() and (d / "wal.json").exists()]
    if not tx_dirs:
        return {
            "status": "NOT_FOUND",
            "message": "Không tìm thấy transaction quarantine nào để hoàn tác.",
            "tx_id": tx_id,
            "restored_count": 0,
            "failed_count": 0,
            "restored": [],
            "failed": []
        }

    # Sắp xếp theo tên giảm dần (chứa timestamp) -> mới nhất trước
    tx_dirs.sort(key=lambda d: d.name, reverse=True)

    selected_dir = None
    if tx_id:
        target_name = str(tx_id).strip()
        for d in tx_dirs:
            if d.name == target_name or d.name.endswith(target_name) or d.name.startswith(target_name):
                selected_dir = d
                break
        if not selected_dir:
            return {
                "status": "NOT_FOUND",
                "message": f"Không tìm thấy transaction với id: {tx_id}",
                "tx_id": tx_id,
                "restored_count": 0,
                "failed_count": 0,
                "restored": [],
                "failed": []
            }
    else:
        selected_dir = tx_dirs[0]

    wal_path = selected_dir / "wal.json"
    try:
        wal_data = json.loads(wal_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Không thể đọc wal.json từ {selected_dir.name}: {e}",
            "tx_id": selected_dir.name,
            "restored_count": 0,
            "failed_count": 0,
            "restored": [],
            "failed": []
        }

    payload_dir = selected_dir / "payload"
    items = wal_data.get("items", [])
    restored = []
    failed = []

    for item in items:
        orig_path = Path(item["original_path"])
        stored_path = Path(item["stored_path"]) if "stored_path" in item else None

        if not stored_path or not stored_path.exists():
            payload_rel = item.get("payload_rel")
            if payload_rel and (payload_dir / payload_rel).exists():
                stored_path = payload_dir / payload_rel
            elif (payload_dir / orig_path.name).exists():
                stored_path = payload_dir / orig_path.name

        if not stored_path or not stored_path.exists():
            failed.append({
                "path": str(orig_path),
                "error": "Tệp trong payload quarantine không tồn tại"
            })
            continue

        # Defense-in-depth: Đảm bảo original_path nằm trong workspace bounds
        try:
            resolved_orig = orig_path.resolve()
            allowed_roots = [ws_root.resolve()]
            if "target_dir" in wal_data and wal_data["target_dir"]:
                allowed_roots.append(Path(wal_data["target_dir"]).resolve())
            if not any(r == resolved_orig or r in resolved_orig.parents for r in allowed_roots):
                failed.append({
                    "path": str(orig_path),
                    "error": f"Security restriction: original_path must reside within workspace root ({ws_root})"
                })
                continue
        except Exception as e:
            failed.append({
                "path": str(orig_path),
                "error": f"Invalid path resolution: {e}"
            })
            continue

        try:
            orig_path.parent.mkdir(parents=True, exist_ok=True)
            if orig_path.exists():
                safe_delete_path(orig_path)
            shutil.move(str(stored_path), str(orig_path))
            restored.append({
                "path": str(orig_path),
                "size": item.get("size", 0)
            })
        except Exception as e:
            failed.append({
                "path": str(orig_path),
                "error": str(e)
            })

    # Dọn dẹp transaction directory nếu đã hoàn tác thành công hoàn toàn
    if len(restored) > 0 and len(failed) == 0:
        try:
            safe_delete_path(selected_dir)
        except Exception:
            pass

    status = "SUCCESS" if len(failed) == 0 and len(restored) > 0 else ("PARTIAL" if len(restored) > 0 else "ERROR")
    return {
        "status": status,
        "tx_id": selected_dir.name,
        "restored_count": len(restored),
        "failed_count": len(failed),
        "restored": restored,
        "failed": failed
    }


# =====================================================================
# 6. CLI INTERFACE
# =====================================================================

def print_help():
    print("""
AgentKit Cleanup Guard v1.2 — Zero-Bloat Janitor
=================================================
Lệnh:
  scan          Quét và phân loại các tệp rác trong workspace
  prune         Dọn dẹp các tệp rác (hỗ trợ --dry-run, --quarantine, --shred)
  undo-prune    Hoàn tác phục hồi file về vị trí ban đầu từ quarantine WAL
  verify-clean  Kiểm tra workspace đã sạch bóng chưa (Exit 0 = Clean, 1 = Còn rác)

Tùy chọn:
  --target-dir <path>             Thư mục mục tiêu (mặc định: workspace root)
  --dry-run                       Chỉ mô phỏng quét xóa, không can thiệp đĩa
  --quarantine / --no-quarantine  Bật/tắt chế độ di chuyển vào quarantine thay vì xóa hẳn
  --shred / --no-shred            Ghi đè byte 0x00 trước khi xóa file nhạy cảm
  --tx <id>                       Transaction ID cần phục hồi (dùng cho undo-prune)
  --categories <c1,c2>            Chỉ định danh mục (stale_logs, scratch_scripts, old_versions, ephemeral_snapshots, dead_cache)
  --verbose                       Hiển thị đầy đủ danh sách tệp thay vì rút gọn
  --json                          Xuất kết quả định dạng JSON
""")


def main():
    command, args = parse_cli_args(sys.argv)
    target_dir = resolve_workspace_root(args.get("target-dir"))
    verbose = args.get("verbose") == "true"

    if command == "scan":
        results = classify_trash(target_dir)
        total_count = sum(len(v) for v in results.values())
        total_size = sum(x.get("size", 0) for v in results.values() for x in v)

        if args.get("json") == "true":
            import json
            payload = {
                "target_dir": str(target_dir),
                "total_count": total_count,
                "total_size": total_size,
                "total_size_human": format_bytes(total_size),
                "categories": results
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"🔍 Quét rác kỹ thuật tại: {target_dir}")
            print(f"📊 Tổng số tệp rác phát hiện: {total_count} mục ({format_bytes(total_size)})")
            for cat, items in results.items():
                if items:
                    cat_size = sum(it.get("size", 0) for it in items)
                    print(f"\n  📁 [{cat.upper()}] ({len(items)} mục - {format_bytes(cat_size)}):")
                    display_items = items if verbose else items[:10]
                    for it in display_items:
                        sz_str = f" [{format_bytes(it.get('size', 0))}]" if it.get("size") else ""
                        print(f"    • {it['name']}{sz_str} ({it.get('reason', '')})")
                    if not verbose and len(items) > 10:
                        print(f"    ... và {len(items) - 10} mục khác (dùng --verbose để xem đầy đủ).")

    elif command == "prune":
        dry_run = args.get("dry-run") == "true"
        cat_arg = args.get("categories")
        categories = cat_arg.split(",") if cat_arg else None

        quarantine = None
        if "no-quarantine" in args or args.get("quarantine") == "false":
            quarantine = False
        elif "quarantine" in args or args.get("quarantine") == "true":
            quarantine = True

        shred = None
        if "no-shred" in args or args.get("shred") == "false":
            shred = False
        elif "shred" in args or args.get("shred") == "true":
            shred = True

        res = prune_trash(target_dir, dry_run=dry_run, categories=categories,
                          quarantine=quarantine, shred=shred)
        if args.get("json") == "true":
            import json
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            mode_parts = []
            if dry_run:
                mode_parts.append("DRY-RUN")
            if res.get("quarantine"):
                mode_parts.append("QUARANTINE")
            mode_str = f" [{', '.join(mode_parts)}]" if mode_parts else ""
            print(f"🧹 Kết quả dọn dẹp{mode_str}:")
            print(f"  • Đã giải phóng: {res['freed_human']} ({res['cleaned_count']}/{res['found']} mục)")
            if res.get("tx_id"):
                print(f"  • Quarantine Transaction ID: {res['tx_id']} (có thể undo bằng: undo-prune --tx {res['tx_id']})")
            if res['failed_count'] > 0:
                print(f"  • Thất bại (file lock): {res['failed_count']} mục")
            if res['skipped_count'] > 0:
                print(f"  • Bỏ qua (an toàn): {res['skipped_count']} mục")

            display_cleaned = res['cleaned'] if verbose else res['cleaned'][:10]
            for item in display_cleaned:
                sz_str = f" [{format_bytes(item.get('size', 0))}]" if item.get("size") else ""
                action_str = f" ({item.get('action', 'cleaned')})" if item.get("action") else ""
                print(f"    ✅ {item['path']}{sz_str}{action_str}")
            if not verbose and len(res['cleaned']) > 10:
                print(f"    ... và {len(res['cleaned']) - 10} mục khác (dùng --verbose để xem đầy đủ).")

    elif command in ("undo-prune", "undo_prune"):
        tx_id = args.get("tx")
        res = undo_prune(tx_id=tx_id, target_dir=target_dir)
        if args.get("json") == "true":
            import json
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            if res["status"] in ("SUCCESS", "PARTIAL"):
                print(f"🔄 Hoàn tác dọn dẹp [{res['status']}]: Transaction {res['tx_id']}")
                print(f"  • Đã phục hồi: {res['restored_count']} mục")
                if res['failed_count'] > 0:
                    print(f"  • Thất bại: {res['failed_count']} mục")
                display_items = res['restored'] if verbose else res['restored'][:10]
                for item in display_items:
                    print(f"    ↩️ {item['path']}")
                if not verbose and len(res['restored']) > 10:
                    print(f"    ... và {len(res['restored']) - 10} mục khác.")
            else:
                print(f"❌ Hoàn tác thất bại: {res.get('message', 'Không rõ lỗi')}")

    elif command == "verify-clean":
        results = classify_trash(target_dir)
        total = sum(len(v) for v in results.values())
        total_size = sum(x.get("size", 0) for v in results.values() for x in v)
        if total == 0:
            print("✅ Workspace hoàn toàn sạch bóng! Zero-Bloat verified.")
            sys.exit(0)
        else:
            print(f"⚠️ Phát hiện {total} tệp rác còn sót lại ({format_bytes(total_size)})!")
            sys.exit(1)

    else:
        print_help()


if __name__ == "__main__":
    main()
