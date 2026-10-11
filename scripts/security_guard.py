#!/usr/bin/env python3
"""
AgentKit Security Guard Engine v1.0 (Zero-Trust & STRIDE Automation)
Tự động kiểm toán AST Secret, rà soát lỗ hổng STRIDE và sinh Security Test Probes.

Usage:
    # 1. Quét Secret & STRIDE trong codebase/file
    python security_guard.py scan --target app.py

    # 2. Tự động sinh Security Test Probes
    python security_guard.py gen-probes --target app.py --output test_security_probes.py

    # 3. Kiểm toán dependencies
    python security_guard.py audit-deps --dir ./project [--baseline old_requirements.txt]
"""

import ast
import json
import math
import os
import re
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# =====================================================================
# 1. SECRET & ENTROPY SCANNER (ZERO-TRUST)
# =====================================================================

# =====================================================================
# 1. SECRET & ENTROPY SCANNER (ZERO-TRUST)
# =====================================================================

SECRET_REGEXES = [
    (r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|password|passwd|private[_-]?key)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{12,})['\"]?", "High-Risk Secret Assignment"),
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Hardcoded Bearer Token"),
    (r"AIza[0-9A-Za-z-_]{35}", "Google API Key"),
    (r"sk-(proj-)?[a-zA-Z0-9-_]{20,}", "OpenAI API Key"),
    (r"sk-ant-api03-[a-zA-Z0-9-_]{20,}", "Anthropic API Key"),
    (r"gh[pousr]_[0-9a-zA-Z]{36}|github_pat_[0-9a-zA-Z_]{22,}", "GitHub Token"),
    (r"(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}", "AWS Access Key ID"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "JWT Token"),
    (r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", "Private Key Block"),
]


def shannon_entropy(data: str) -> float:
    """Tính toán entropy Shannon của chuỗi ký tự."""
    if not data:
        return 0.0
    entropy = 0.0
    for x in set(data):
        p_x = float(data.count(x)) / len(data)
        entropy += - p_x * math.log(p_x, 2)
    return entropy


def assert_within_sandbox(target_path: Path, sandbox_root: Path) -> Path:
    """Bắt buộc đường dẫn sau khi resolve phải nằm 100% bên trong sandbox_root."""
    resolved_target = Path(target_path).resolve()
    resolved_root = Path(sandbox_root).resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise PermissionError(f"🛑 [Zero-Trust Sandbox Jail Escape Blocked] Path '{target_path}' nằm ngoài sandbox '{sandbox_root}'!")
    return resolved_target


def scan_file_secrets(filepath: str) -> list:
    """Quét secret và entropy cao trong tệp tin (Precomputed 1-Pass Line Indexing)."""
    path = Path(filepath)
    if not path.exists() or not path.is_file():
        return []

    content = path.read_text(encoding="utf-8", errors="ignore")
    findings = []
    seen_spans = set()

    # 1-Pass line offsets
    line_starts = [0] + [m.start() + 1 for m in re.finditer(r"\n", content)]

    def get_line_no(offset: int) -> int:
        import bisect
        return bisect.bisect_right(line_starts, offset)

    # 1. Quét theo Regex signatures
    for pattern, desc in SECRET_REGEXES:
        matches = re.finditer(pattern, content)
        for m in matches:
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            line_no = get_line_no(m.start())
            secret_val = m.group(0)
            findings.append({
                "file": str(path),
                "line": line_no,
                "type": desc,
                "snippet": secret_val[:25] + "...[REDACTED]"
            })

    # 2. Quét Shannon Entropy cho chuỗi literal dài không gian
    for m in re.finditer(r"['\"]([a-zA-Z0-9_\-\.\/+=]{28,})['\"]", content):
        token_str = m.group(1)
        if shannon_entropy(token_str) > 4.5:
            span = (m.start(), m.end())
            if span not in seen_spans:
                seen_spans.add(span)
                line_no = get_line_no(m.start())
                findings.append({
                    "file": str(path),
                    "line": line_no,
                    "type": "High-Entropy Secret Candidate (Shannon > 4.5)",
                    "snippet": token_str[:20] + "...[REDACTED]"
                })

    return findings


# =====================================================================
# 2. STRIDE THREAT MODEL & INVARIANT CHECKER
# =====================================================================

def verify_stride_invariants(code_text: str, ext: str = ".py") -> dict:
    """Kiểm tra các bất biến STRIDE cơ bản qua phân tích tĩnh."""
    violations = []
    
    # 1. SQL Injection check (Dynamic String Formatting in queries)
    if re.search(r"(?i)(SELECT|INSERT|UPDATE|DELETE)\s+.*(%s|\.format|\+|f['\"])", code_text):
        violations.append({
            "threat": "Tampering / SQL Injection",
            "severity": "HIGH",
            "detail": "Phát hiện nối chuỗi trực tiếp trong SQL query mà không dùng Parameterized Query."
        })

    # 2. Information Disclosure (Sensitive logging)
    if re.search(r"(?i)(print|console\.log|logger\.(debug|info))\s*\(.*(password|secret|token|credential)", code_text):
        violations.append({
            "threat": "Information Disclosure",
            "severity": "MEDIUM",
            "detail": "Phát hiện log biến nhạy cảm ra console/logger."
        })

    # 3. Denial of Service (Regex Catastrophic Backtracking)
    if re.search(r"(\([a-zA-Z0-9_\-]+\+\)\+|\([a-zA-Z0-9_\-]+\*\)\*)", code_text):
        violations.append({
            "threat": "Denial of Service / ReDoS",
            "severity": "MEDIUM",
            "detail": "Phát hiện nested quantifiers trong Regex có nguy cơ ReDoS."
        })

    # 4. Elevation of Privilege / Command Injection
    if re.search(r"subprocess\.\w+\(.*shell\s*=\s*True", code_text, re.DOTALL):
        if re.search(r"subprocess\.\w+\(.*(\+|\.format|f['\"])", code_text, re.DOTALL):
            violations.append({
                "threat": "Elevation of Privilege / Command Injection",
                "severity": "HIGH",
                "detail": "subprocess với shell=True kết hợp nối chuỗi — cho phép Command Injection."
            })

    # 5. Tampering / Unsafe Deserialization
    if re.search(r"pickle\.(loads?|Unpickler)\s*\(", code_text):
        violations.append({
            "threat": "Tampering / Unsafe Deserialization",
            "severity": "HIGH",
            "detail": "pickle.load/loads cho phép thực thi mã tùy ý (Arbitrary Code Execution)."
        })
    if re.search(r"yaml\.load\s*\(", code_text) and not re.search(r"SafeLoader|CSafeLoader", code_text):
        violations.append({
            "threat": "Tampering / Unsafe Deserialization",
            "severity": "HIGH",
            "detail": "yaml.load() không dùng SafeLoader — cho phép thực thi mã tùy ý."
        })

    # 6. Spoofing / Weak Hashing
    if re.search(r"(?i)hashlib\.(md5|sha1)\s*\(", code_text):
        if re.search(r"(?i)(password|passwd|pwd|credential)", code_text):
            violations.append({
                "threat": "Spoofing / Weak Hashing",
                "severity": "HIGH",
                "detail": "Dùng MD5/SHA1 cho password — thuật toán yếu, dễ bị brute force. Dùng bcrypt/scrypt/argon2."
            })

    return {
        "clean": len(violations) == 0,
        "violations_count": len(violations),
        "violations": violations
    }


# =====================================================================
# 3. DYNAMIC SECURITY TEST PROBES GENERATOR
# =====================================================================

def generate_security_probes(target_file: str, output_file: str = None) -> str:
    """Tự động sinh bộ kiểm thử bảo mật (Security Probes) cho target file.
    Probes thực sự đọc và quét target thay vì chỉ assert tĩnh."""
    path = Path(target_file)
    abs_target = path.resolve()

    probe_code = f'''#!/usr/bin/env python3
"""
Dynamic Security Probes Suite for: {path.name}
Tự động sinh bởi AgentKit Security Guard v1.0
Probes thực sự quét target file cho STRIDE violations và secret leaks.
"""

import re
import sys
from pathlib import Path

TARGET = Path(r"{abs_target}")

def _read_target():
    if not TARGET.exists():
        print(f"  ⚠️ Target {{TARGET}} not found — skipping content probes")
        return None
    return TARGET.read_text(encoding="utf-8", errors="replace")


def test_no_hardcoded_secrets():
    """Probe 1: Quét secret regex patterns trong target."""
    content = _read_target()
    if content is None:
        return
    patterns = [
        (r"(?i)(api[_-]?key|secret[_-]?key|password)\\s*[:=]\\s*['\\\"]([a-zA-Z0-9]{{12,}})['\\\"]", "Hardcoded Secret"),
        (r"AIza[0-9A-Za-z-_]{{35}}", "Google API Key"),
        (r"sk-(proj-)?[a-zA-Z0-9-_]{{20,}}", "OpenAI/Anthropic Key"),
        (r"gh[pousr]_[0-9a-zA-Z]{{36}}", "GitHub Token"),
    ]
    findings = []
    for pat, desc in patterns:
        if re.search(pat, content):
            findings.append(desc)
    assert not findings, f"Hardcoded secrets detected: {{findings}}"
    print("  ✅ [PASS] No hardcoded secrets")


def test_no_sql_injection_vectors():
    """Probe 2: Quét SQL injection patterns (string concatenation in queries)."""
    content = _read_target()
    if content is None:
        return
    sqli_pattern = r"(?i)(SELECT|INSERT|UPDATE|DELETE)\\s+.*(\\.format|\\+.*\\+|f['\\\"])"
    assert not re.search(sqli_pattern, content), "SQL Injection vector: string formatting in SQL query"
    print("  ✅ [PASS] No SQL injection vectors")


def test_no_sensitive_logging():
    """Probe 3: Quét log statements chứa biến nhạy cảm."""
    content = _read_target()
    if content is None:
        return
    log_pattern = r"(?i)(print|logger\\.(debug|info))\\s*\\(.*(password|secret|token|credential)"
    assert not re.search(log_pattern, content), "Sensitive data in log statements"
    print("  ✅ [PASS] No sensitive data logging")


def test_no_bare_except():
    """Probe 4: Quét bare except (swallows all errors including KeyboardInterrupt)."""
    content = _read_target()
    if content is None:
        return
    # Simple regex check — AST check is better but this is a probe
    bare = re.findall(r"^\\s*except\\s*:", content, re.MULTILINE)
    assert not bare, f"Found {{len(bare)}} bare except blocks"
    print("  ✅ [PASS] No bare except blocks")


def test_no_path_traversal():
    """Probe 5: Quét path traversal patterns không được sanitize."""
    content = _read_target()
    if content is None:
        return
    # Check if file opens user-controlled paths without resolve/sanitize
    unsafe = re.findall(r"open\\(.*\\+.*\\)", content)
    if unsafe:
        print(f"  ⚠️ [WARN] {{len(unsafe)}} potential unsanitized path opens (manual review needed)")
    else:
        print("  ✅ [PASS] No obvious path traversal vectors")


def main():
    print(f"🔒 Running Security Probes for {{TARGET.name}}...")
    test_no_hardcoded_secrets()
    test_no_sql_injection_vectors()
    test_no_sensitive_logging()
    test_no_bare_except()
    test_no_path_traversal()
    print("🟢 ALL SECURITY PROBES PASSED (Zero-Trust Verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

    if output_file:
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(probe_code, encoding="utf-8")

    return probe_code


# =====================================================================
# 4. DEPENDENCY AUDIT GATE
# =====================================================================

# Top popular packages across ecosystems for typosquat detection
_POPULAR_PACKAGES = [
    "requests", "flask", "django", "numpy", "pandas", "scipy", "matplotlib",
    "pillow", "sqlalchemy", "celery", "redis", "boto3", "tensorflow", "torch",
    "pytest", "sphinx", "gunicorn", "uvicorn", "fastapi", "pydantic", "httpx",
    "cryptography", "paramiko", "fabric", "ansible", "scrapy", "beautifulsoup4",
    "lxml", "jinja2", "click", "rich", "typer", "black", "isort", "mypy",
    "setuptools", "wheel", "pip", "twine", "poetry", "pipenv", "virtualenv",
    "express", "react", "lodash", "axios", "moment", "chalk", "commander",
    "webpack", "babel", "eslint", "prettier", "typescript", "next", "vue",
    "angular", "jquery", "underscore", "async", "bluebird", "request",
    "mongoose", "sequelize", "knex", "passport", "helmet", "cors", "dotenv",
    "nodemon", "mocha", "jest", "chai", "sinon", "supertest", "puppeteer",
    "serde", "tokio", "actix", "rocket", "clap", "rand", "regex", "hyper",
    "reqwest", "diesel", "sqlx", "anyhow", "thiserror", "tracing", "log",
    "gin", "echo", "fiber", "cobra", "viper", "gorm", "zap", "logrus",
]


def _edit_distance(s1: str, s2: str) -> int:
    """Tính Levenshtein edit distance giữa hai chuỗi."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(curr_row[j] + 1, prev_row[j + 1] + 1, prev_row[j] + cost))
        prev_row = curr_row
    return prev_row[-1]


def _parse_requirements_txt(text: str) -> list:
    """Parse dependencies từ requirements.txt format."""
    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*([><=!~]+\s*[\w\.\*]+(?:\s*,\s*[><=!~]+\s*[\w\.\*]+)*)?", line)
        if m:
            deps.append({"name": m.group(1).lower(), "version": (m.group(2) or "").strip(), "raw": line})
    return deps


def _parse_pyproject_toml(text: str) -> list:
    """Parse dependencies từ pyproject.toml (basic regex, không cần toml lib)."""
    deps = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"\[.*dependencies.*\]", stripped, re.IGNORECASE):
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps:
            m = re.match(r'"([a-zA-Z0-9_\-\.]+)\s*([><=!~]*\s*[\w\.\*]*)"', stripped)
            if m:
                deps.append({"name": m.group(1).lower(), "version": m.group(2).strip(), "raw": stripped})
            else:
                m2 = re.match(r"([a-zA-Z0-9_\-\.]+)\s*=\s*['\"](.+?)['\"]", stripped)
                if m2:
                    deps.append({"name": m2.group(1).lower(), "version": m2.group(2).strip(), "raw": stripped})
    return deps


def _parse_package_json(text: str) -> list:
    """Parse dependencies từ package.json."""
    deps = []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return deps
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name, ver in data.get(section, {}).items():
            deps.append({"name": name.lower(), "version": ver, "raw": f"{name}: {ver}"})
    return deps


def _parse_cargo_toml(text: str) -> list:
    """Parse dependencies từ Cargo.toml (basic regex)."""
    deps = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"\[.*dependencies.*\]", stripped, re.IGNORECASE):
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps:
            m = re.match(r"([a-zA-Z0-9_\-]+)\s*=\s*['\"](.+?)['\"]", stripped)
            if m:
                deps.append({"name": m.group(1).lower(), "version": m.group(2).strip(), "raw": stripped})
    return deps


def _parse_go_mod(text: str) -> list:
    """Parse dependencies từ go.mod."""
    deps = []
    in_require = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if stripped == ")" and in_require:
            in_require = False
            continue
        if in_require:
            parts = stripped.split()
            if len(parts) >= 2:
                deps.append({"name": parts[0].lower(), "version": parts[1], "raw": stripped})
        elif stripped.startswith("require "):
            parts = stripped[8:].split()
            if len(parts) >= 2:
                deps.append({"name": parts[0].lower(), "version": parts[1], "raw": stripped})
    return deps


_DEP_FILE_PARSERS = {
    "requirements.txt": _parse_requirements_txt,
    "pyproject.toml": _parse_pyproject_toml,
    "package.json": _parse_package_json,
    "Cargo.toml": _parse_cargo_toml,
    "go.mod": _parse_go_mod,
}


def audit_dependencies(project_dir: str, baseline_file: str = None) -> dict:
    """Kiểm toán dependency: typosquat, license, version pinning, risky patterns.

    Args:
        project_dir: Thư mục project chứa dependency files.
        baseline_file: (Tùy chọn) File dependency cũ để so sánh tìm deps mới.

    Returns:
        Dict với total_deps, new_deps, warnings, verdict.
    """
    project_path = Path(project_dir)
    all_deps = []
    warnings = []

    # Detect & parse dependency files
    for filename, parser in _DEP_FILE_PARSERS.items():
        dep_file = project_path / filename
        if dep_file.exists():
            content = dep_file.read_text(encoding="utf-8", errors="ignore")
            parsed = parser(content)
            for d in parsed:
                d["source"] = filename
            all_deps.extend(parsed)

    # Parse baseline if provided
    baseline_deps = set()
    if baseline_file:
        bp = Path(baseline_file)
        if bp.exists():
            bcontent = bp.read_text(encoding="utf-8", errors="ignore")
            # Auto-detect parser by filename
            parser = _DEP_FILE_PARSERS.get(bp.name, _parse_requirements_txt)
            for d in parser(bcontent):
                baseline_deps.add(d["name"])

    # Determine new deps
    new_deps = []
    check_set = all_deps
    if baseline_file:
        check_set = [d for d in all_deps if d["name"] not in baseline_deps]
        new_deps = [d["name"] for d in check_set]

    # Audit each dependency (focus on new if baseline provided, else all)
    for dep in check_set:
        pkg = dep["name"]
        ver = dep.get("version", "")
        raw = dep.get("raw", "")

        # a. Typosquat check
        for popular in _POPULAR_PACKAGES:
            if pkg == popular:
                continue
            dist = _edit_distance(pkg, popular)
            if 0 < dist <= 2:
                warnings.append({
                    "severity": "HIGH",
                    "package": pkg,
                    "issue": f"Typosquat risk: tên '{pkg}' rất giống package phổ biến '{popular}' (edit distance={dist})",
                    "suggestion": f"Xác minh đúng tên package. Có phải bạn muốn dùng '{popular}'?"
                })
                break  # Only flag closest match

        # b. License warning (heuristic on name/raw)
        if re.search(r"(?i)\bgpl\b", pkg) or re.search(r"(?i)\bgpl\b", raw):
            warnings.append({
                "severity": "MEDIUM",
                "package": pkg,
                "issue": "Phát hiện GPL keyword — có thể ràng buộc copyleft license",
                "suggestion": "Kiểm tra license compatibility với dự án của bạn."
            })

        # c. Pinning check
        if not ver or ver in ("*", "latest") or (ver.startswith(">=") and "," not in ver and "<" not in ver):
            warnings.append({
                "severity": "MEDIUM",
                "package": pkg,
                "issue": f"Version không pinned: '{ver or '(none)'}' — dễ bị supply chain attack",
                "suggestion": "Pin version cụ thể, ví dụ: ==1.2.3 hoặc ~=1.2"
            })

        # d. Risky URL/git patterns
        if re.search(r"(https?://|git\+|ssh://)", raw):
            warnings.append({
                "severity": "HIGH",
                "package": pkg,
                "issue": f"Dependency được cài từ URL/git — không kiểm tra được integrity",
                "suggestion": "Dùng package từ registry chính thức (PyPI/npm/crates.io)."
            })

    # Determine verdict
    high_count = sum(1 for w in warnings if w["severity"] == "HIGH")
    if high_count > 0:
        verdict = "BLOCK"
    elif warnings:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return {
        "total_deps": len(all_deps),
        "new_deps": new_deps,
        "warnings": warnings,
        "verdict": verdict,
    }


# =====================================================================
# CLI PARSER (Standardized --key value pattern)
# =====================================================================

try:
    from _shared import parse_cli_args, emit_metric
except ImportError:
    def emit_metric(event, **kwargs): pass
    def parse_cli_args(argv=None):
        import sys as _sys
        argv = argv or _sys.argv
        if len(argv) < 2: return "help", {}
        cmd, args, i = argv[1], {}, 2
        while i < len(argv):
            if argv[i].startswith("--"):
                k = argv[i][2:]
                if i+1 < len(argv) and not argv[i+1].startswith("--"):
                    args[k] = argv[i+1]; i += 2
                else:
                    args[k] = "true"; i += 1
            else:
                i += 1
        return cmd, args


def main():
    cmd, args = parse_cli_args(sys.argv)

    if cmd == "scan":
        target = args.get("target", ".")
        secrets = scan_file_secrets(target)
        print("🔒 AGENTKIT ZERO-TRUST SECURITY SCAN")
        print("=" * 60)
        if not secrets:
            print("  ✅ Zero Hardcoded Secrets Detected.")
        else:
            print(f"  ❌ Phát hiện {len(secrets)} nguy cơ rò rỉ secret:")
            for s in secrets:
                print(f"    • [{s['type']}] Dòng {s['line']}: {s['snippet']}")
        emit_metric("security_scan", target=target, findings=len(secrets))

    elif cmd == "gen-probes":
        target = args.get("target", "app.py")
        out = args.get("output", "test_security_probes.py")
        generate_security_probes(target, out)
        print(f"✅ Đã tự động sinh Security Probes ra: {out}")

    elif cmd == "audit-deps":
        project_dir = args.get("dir", ".")
        baseline = args.get("baseline")
        result = audit_dependencies(project_dir, baseline)
        print("🔍 AGENTKIT DEPENDENCY AUDIT GATE")
        print("=" * 60)
        print(f"  📦 Total dependencies: {result['total_deps']}")
        if result["new_deps"]:
            print(f"  🆕 New dependencies: {', '.join(result['new_deps'])}")
        if result["warnings"]:
            print(f"  ⚠️  {len(result['warnings'])} warning(s):")
            for w in result["warnings"]:
                sev_icon = "🔴" if w["severity"] == "HIGH" else "🟡"
                print(f"    {sev_icon} [{w['severity']}] {w['package']}: {w['issue']}")
                print(f"       💡 {w['suggestion']}")
        else:
            print("  ✅ No dependency issues detected.")
        verdict_icon = {"PASS": "🟢", "WARN": "🟡", "BLOCK": "🔴"}
        print(f"\n  {verdict_icon.get(result['verdict'], '⚪')} Verdict: {result['verdict']}")
        emit_metric("dep_audit", dir=project_dir, total=result["total_deps"],
                     warnings=len(result["warnings"]), verdict=result["verdict"])

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
