#!/usr/bin/env python3
"""AgentKit Health Check v1.0 — Smoke-test all 12 scripts in one go.

Usage:
    python scripts/health_check.py          # Run all checks
    python scripts/health_check.py --json   # Output JSON report

Exit codes:
    0 = All checks passed
    1 = One or more checks failed
    2 = Script error
"""
import sys, os, importlib.util, json, subprocess, pathlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent

# Each entry: (filename, description, check_type, check_args)
# check_type: "import" = importable, "cli" = runs with --help or exits cleanly
CHECKS = [
    ("code_guard.py",         "AST Gate & WAL Snapshot & Linter",     "import"),
    ("cow_engine.py",         "CoW Snapshot Engine",                   "import"),
    ("decision_log.py",       "Decision Logger",                       "import"),
    ("lessons.py",            "Dual-Tier Knowledge",                   "import"),
    ("orchestrate_engine.py", "Orchestration & Review Council",        "import"),
    ("sde_guard.py",          "Gate 0: SDE Contract",                  "import"),
    ("security_guard.py",     "Zero-Trust Scanner",                    "import"),
    ("test_guard.py",         "Gate 2: Test Rigor & Mutation",         "import"),
    ("task_watchdog.py",      "Anti-Hang Log Diagnostics",             "import"),
    ("worktree_gc.py",        "Worktree GC & Anti-Lock",               "import"),
    ("cleanup_guard.py",      "Post-Task Ephemeral Cleanup Guard",     "import"),
    ("content_filter.py",     "Content Filter & Prompt Sanitizer",     "import"),
    ("test_agentkit.py",      "Comprehensive Test Suite",              "import"),
    ("test_cow_engine.py",    "CoW Engine Test Suite",                 "import"),
]


def check_import(script_path: pathlib.Path) -> tuple[bool, str]:
    """Try to import a script as a module. Returns (ok, detail)."""
    try:
        spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
        if spec is None or spec.loader is None:
            return False, "spec_from_file_location returned None"
        # Only load the spec to check syntax, don't execute main
        mod = importlib.util.module_from_spec(spec)
        # We do a compile check instead of full exec to avoid side effects
        with open(script_path, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, str(script_path), "exec")
        return True, "syntax OK"
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_cli(script_path: pathlib.Path) -> tuple[bool, str]:
    """Try to run script with --help. Returns (ok, detail)."""
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace"
        )
        if result.returncode in (0, 2):  # 0=ok, 2=argparse error is fine
            return True, f"exit {result.returncode}"
        return False, f"exit {result.returncode}: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "timeout (>10s)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_config() -> tuple[bool, str]:
    """Check config.yaml exists and is valid YAML."""
    config_path = SCRIPTS_DIR.parent / "config.yaml"
    if not config_path.exists():
        return False, "config.yaml not found"
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return False, "config.yaml is not a dict"
        required_keys = ["watchdog", "retry", "quality_gates", "planning"]
        missing = [k for k in required_keys if k not in data]
        if missing:
            return False, f"missing keys: {missing}"
        return True, f"{len(data)} top-level sections"
    except ImportError:
        # yaml not installed — just check file is readable
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 50:
            return True, f"readable ({len(content)} bytes, yaml not installed for validation)"
        return False, "file too small"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_references() -> list[tuple[str, bool, str]]:
    """Check reference docs exist."""
    refs_dir = SCRIPTS_DIR.parent / "references"
    results = []
    for name in ["protocols.md", "sidecar_matrix.md", "cli_engines.md", "safe_vocabulary.yaml"]:
        path = refs_dir / name
        if path.exists():
            size = path.stat().st_size
            results.append((f"references/{name}", True, f"{size} bytes"))
        else:
            results.append((f"references/{name}", False, "NOT FOUND"))
    return results


def main():
    json_mode = "--json" in sys.argv
    results = []
    passed = 0
    failed = 0

    # Check config.yaml
    ok, detail = check_config()
    results.append(("config.yaml", ok, detail))
    if ok:
        passed += 1
    else:
        failed += 1

    # Check scripts
    for filename, desc, check_type in CHECKS:
        script_path = SCRIPTS_DIR / filename
        if not script_path.exists():
            results.append((filename, False, "FILE NOT FOUND"))
            failed += 1
            continue

        if check_type == "import":
            ok, detail = check_import(script_path)
        else:
            ok, detail = check_cli(script_path)

        results.append((filename, ok, f"{desc} — {detail}"))
        if ok:
            passed += 1
        else:
            failed += 1

    # Check reference docs
    for name, ok, detail in check_references():
        results.append((name, ok, detail))
        if ok:
            passed += 1
        else:
            failed += 1

    total = passed + failed

    if json_mode:
        report = {
            "passed": passed,
            "failed": failed,
            "total": total,
            "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in results]
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"  AgentKit Dual-Control — Health Check")
        print(f"{'='*60}\n")
        for name, ok, detail in results:
            icon = "\u2705" if ok else "\u274c"
            print(f"  {icon} {name:<30s} {detail}")
        print(f"\n{'='*60}")
        print(f"  Result: {passed}/{total} passed", end="")
        if failed:
            print(f", {failed} FAILED")
        else:
            print(" \u2014 ALL OK!")
        print(f"{'='*60}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
