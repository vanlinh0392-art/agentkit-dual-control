#!/usr/bin/env python3
"""
AgentKit Shared Utilities v1.0
Tập trung các hàm dùng chung giữa tất cả scripts AgentKit.

Import:
    from _shared import atomic_write_json, parse_cli_args, get_brain_dir, load_config, emit_metric
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# =====================================================================
# 1. BRAIN DIRECTORY DISCOVERY
# =====================================================================

def get_brain_dir() -> Path:
    """Tìm .brain/ trong workspace hiện tại hoặc fallback về global Antigravity brain."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        brain = parent / ".brain"
        if brain.is_dir():
            return brain
        if parent == Path.home() or parent.parent == parent:
            break
    global_brain = Path.home() / ".gemini" / "antigravity" / "brain"
    if global_brain.is_dir():
        return global_brain
    brain = cwd / ".brain"
    brain.mkdir(parents=True, exist_ok=True)
    return brain


def get_brain_subdir(name: str) -> Path:
    """Lấy subdirectory trong .brain/, tự tạo nếu chưa có."""
    d = get_brain_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# =====================================================================
# 2. ATOMIC FILE WRITES (Windows-Safe, Retry Loop)
# =====================================================================

def atomic_write_json(file_path: Path, data, retries: int = 3, backoff: float = 0.05) -> bool:
    """Ghi JSON nguyên tử (Write-to-Temp + os.replace) với retry loop chống WinError 32."""
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
                if attempt < retries - 1:
                    time.sleep(backoff * (2 ** attempt))
                else:
                    raise
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise e
    return False


def atomic_append_jsonl(file_path: Path, lines: list, retries: int = 3, backoff: float = 0.05) -> bool:
    """Ghi nối dòng JSONL với retry loop chống xung đột WinError 32."""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    text_chunk = "\n".join(lines) + "\n"
    for attempt in range(retries):
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(text_chunk)
            return True
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
            else:
                raise
    return False


# =====================================================================
# 3. CLI ARGUMENT PARSER (Minimal, No Dependencies)
# =====================================================================

def parse_cli_args(argv: list = None) -> tuple:
    """Parse CLI args đơn giản: command --key value --flag."""
    if argv is None:
        argv = sys.argv
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


# =====================================================================
# 4. CONFIG.YAML READER (Lazy Load, Cached)
# =====================================================================

_config_cache = None


def load_config() -> dict:
    """Đọc config.yaml từ thư mục skill root. Cache sau lần đầu."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        _config_cache = {}
        return _config_cache

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
    except ImportError:
        # yaml not installed — manual parse for simple key: value
        _config_cache = _parse_yaml_lite(config_path)
    except Exception:
        _config_cache = {}

    return _config_cache


def _parse_yaml_lite(path: Path) -> dict:
    """Ultra-minimal YAML parser for flat key-value (no nested, no lists)."""
    result = {}
    current_section = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith(" ") and stripped.endswith(":"):
                current_section = stripped[:-1]
                result[current_section] = {}
            elif current_section and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val.startswith("[") and val.endswith("]"):
                    val = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
                # Try numeric conversion
                elif val.replace(".", "").replace("-", "").isdigit():
                    try:
                        val = int(val) if "." not in val else float(val)
                    except ValueError:
                        pass
                elif val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                result[current_section][key] = val
    except Exception:
        pass
    return result


def get_config(section: str, key: str, default=None):
    """Lấy giá trị config cụ thể. Ví dụ: get_config('watchdog', 'ceiling_seconds', 900)"""
    cfg = load_config()
    return cfg.get(section, {}).get(key, default)


# =====================================================================
# 5. METRICS EMITTER (Lightweight, Append-Only JSONL)
# =====================================================================

def emit_metric(event: str, **kwargs) -> bool:
    """Ghi 1 metric event vào .brain/agentkit_metrics.jsonl.

    Usage:
        emit_metric("gate_block", gate=1, detail="bare except in utils.py")
        emit_metric("watchdog_kill", task_id="abc123", cause="NETWORK_FREEZE")
    """
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **kwargs
        }
        metrics_path = get_brain_dir() / "agentkit_metrics.jsonl"
        with open(metrics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False  # Metrics failure should never break main flow


# =====================================================================
# 6. SECURITY SANDBOX GUARD
# =====================================================================

def assert_within_sandbox(target_path, sandbox_root) -> Path:
    """Bắt buộc đường dẫn sau resolve phải nằm trong sandbox_root (chặn ../.. và symlink escape)."""
    resolved_target = Path(target_path).resolve()
    resolved_root = Path(sandbox_root).resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise PermissionError(
            f"[Zero-Trust Sandbox] Path '{target_path}' nằm ngoài sandbox '{sandbox_root}'!"
        )
    return resolved_target


# =====================================================================
# 7. WINDOWS ANTI-LOCK & SAFE FILE OPERATIONS
# =====================================================================

def to_extended_windows_path(path: Path) -> str:
    """Chuyển đổi đường dẫn sang dạng Extended-Length Path (\\\\?\\) trên Windows chống lỗi >260 ký tự."""
    p_str = str(path.resolve()).replace("/", "\\")
    if sys.platform == "win32" and not p_str.startswith("\\\\?\\") and len(p_str) > 200:
        if p_str.startswith("\\\\"):
            return "\\\\?\\UNC\\" + p_str[2:]
        return "\\\\?\\" + p_str
    return p_str


def handle_remove_readonly(func, path, exc_info):
    """Error handler cho rmtree gỡ cờ Readonly (stat.S_IWRITE) và thử xóa lại."""
    try:
        import stat
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


def unlink_junctions_recursively(target_path: Path):
    """Gỡ bỏ an toàn tất cả Junctions / Symlinks (Top-Down) trước khi rmtree."""
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


def format_bytes(size_bytes: int) -> str:
    """Định dạng dung lượng bytes sang chuỗi dễ đọc (B, KB, MB, GB)."""
    try:
        size = float(size_bytes)
    except (ValueError, TypeError):
        return "0 B"
    if size < 1024:
        return f"{int(size)} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"

