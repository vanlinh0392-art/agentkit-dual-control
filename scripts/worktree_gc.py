#!/usr/bin/env python3
"""
AgentKit Windows Worktree GC & Anti-Lock Pruner v1.0
Dọn dẹp các branch workspaces tạm thời và giải phóng file locks an toàn trên Windows.

Usage:
    python worktree_gc.py scan     # Quét danh sách worktrees/branches tạm
    python worktree_gc.py prune    # Dọn dẹp an toàn kèm retry unlock
"""

import os
import shutil
import stat
import sys
import time
from pathlib import Path

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# =====================================================================
# SAFETY GUARDS
# =====================================================================

PROTECTED_NAMES = {
    "", "c:", "d:", "c:\\", "d:\\", "users", "windows", "program files",
    ".git", ".gemini", "antigravity", "skills", "workflows", "node_modules"
}

def is_safe_to_delete(path: Path) -> bool:
    """Kiểm tra đường dẫn có an toàn để xóa không (Zero-Trust Check)."""
    p_str = str(path).lower().strip().replace("/", "\\")
    p_name = path.name.lower()

    # Chặn root ổ đĩa hoặc protected system folders
    if p_name in PROTECTED_NAMES or len(p_str) <= 3:
        return False

    if path == Path.home() or path == Path.home().parent:
        return False

    # 1. Bắt buộc có CoW manifest file rõ ràng (an toàn 100%)
    if (path / ".agentkit_cow_manifest.json").exists():
        return True

    # 2. Hoặc tên thư mục bắt đầu bằng các prefix độc quyền do AgentKit sinh ra
    strict_prefixes = ("agentkit_ws_", "agentkit_tmp_", "ws_subagent_", ".worktree_", "cow_ws_", ".cow_")
    if p_name.startswith(strict_prefixes):
        return True

    return False


# =====================================================================
# WINDOWS ANTI-LOCK UNLOCK HANDLER & SAFE JUNCTION UNLINKER
# =====================================================================

def to_extended_windows_path(path: Path) -> str:
    """Chuyển đổi đường dẫn sang dạng Extended-Length Path (\\\\?\\) trên Windows chống lỗi >260 ký tự."""
    p_str = str(path.resolve()).replace("/", "\\")
    if sys.platform == "win32" and not p_str.startswith("\\\\?\\") and len(p_str) > 200:
        if p_str.startswith("\\\\"):
            return "\\\\?\\UNC\\" + p_str[2:]
        return "\\\\?\\" + p_str
    return p_str


def unlink_junctions_recursively(target_path: Path):
    """
    Gỡ bỏ an toàn tất cả Junctions / Symlinks (Top-Down) trước khi rmtree
    để ngăn chặn rủi ro xóa lan hoặc đệ quy vào Shared Master Base!
    """
    try:
        for root, dirs, files in list(os.walk(target_path, topdown=True)):
            for d in list(dirs):
                dir_p = Path(root) / d
                if dir_p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(dir_p)):
                    try:
                        if sys.platform == "win32":
                            os.rmdir(dir_p)
                        else:
                            dir_p.unlink()
                        dirs.remove(d)
                    except Exception:
                        pass
    except Exception:
        pass


def handle_remove_readonly(func, path, exc_info):
    """
    Error handler cho shutil.rmtree trên Windows.
    Tự động gỡ cờ Readonly (stat.S_IWRITE) và thử xóa lại.
    """
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


def safe_delete_tree(target_path: Path, max_retries: int = 3, retry_delay: float = 0.3) -> bool:
    """Xóa thư mục đệ quy an toàn trên Windows với cơ chế retry chống file lock, Safe Junction Unlink và Long-Path."""
    if not target_path.exists():
        return True

    if not is_safe_to_delete(target_path):
        print(f"🛑 Từ chối xóa vì lý do an toàn (Zero-Trust): {target_path}")
        return False

    # 1. Gỡ bỏ junctions trước để bảo vệ Master Base
    unlink_junctions_recursively(target_path)

    extended_path = to_extended_windows_path(target_path)

    for attempt in range(1, max_retries + 1):
        try:
            shutil.rmtree(extended_path, onerror=handle_remove_readonly)
            if not target_path.exists():
                return True
        except PermissionError:
            time.sleep(retry_delay * attempt)
        except Exception:
            pass

    # Nếu vẫn còn tồn tại sau retries
    return not target_path.exists()


# =====================================================================
# SCANNER & PRUNER
# =====================================================================

def scan_worktrees() -> list:
    """Quét tìm các branch workspaces tạm thời."""
    candidates = []
    cwd = Path.cwd()
    
    # 1. Quét trong CWD và workspace parent
    search_dirs = [cwd]
    if cwd.parent != cwd:
        search_dirs.append(cwd.parent)

    # 2. Quét trong Antigravity brain / scratch
    antigravity_brain = Path.home() / ".gemini" / "antigravity" / "brain"
    if antigravity_brain.is_dir():
        search_dirs.append(antigravity_brain)

    for base in search_dirs:
        try:
            for item in base.iterdir():
                if item.is_dir() and is_safe_to_delete(item):
                    candidates.append(item)
        except Exception:
            pass

    return list(set(candidates))


def prune_worktrees() -> dict:
    """Dọn dẹp toàn bộ worktrees tạm."""
    candidates = scan_worktrees()
    cleaned = []
    failed = []

    for item in candidates:
        success = safe_delete_tree(item)
        if success:
            cleaned.append(item)
        else:
            failed.append(item)

    return {
        "found": len(candidates),
        "cleaned": cleaned,
        "failed": failed
    }


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "scan"

    if cmd == "scan":
        found = scan_worktrees()
        print(f"🔍 Quét thấy {len(found)} branch/worktree tạm thời:")
        for item in found:
            print(f"  • {item}")
    elif cmd == "prune":
        res = prune_worktrees()
        print(f"🧹 Kết quả dọn dẹp Worktree GC:")
        print(f"  • Đã xóa thành công: {len(res['cleaned'])}/{res['found']} thư mục")
        for item in res['cleaned']:
            print(f"    ✅ {item.name}")
        if res['failed']:
            print(f"  ⚠️ Thất bại (đang bị lock): {len(res['failed'])}")
            for item in res['failed']:
                print(f"    ❌ {item}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
