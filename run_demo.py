#!/usr/bin/env python3
"""
Master Demo Script - AI-Assisted Unit Test Generation
Runs end-to-end pipeline: Analyze → Generate → Compile → Execute
"""

import os
import sys
import subprocess
import time
import threading
from pathlib import Path
import argparse
import getpass
import datetime
import re
import json
import hashlib
import importlib
import shutil
from dataclasses import dataclass
from typing import Any


def _get_safety_policy_class():
    try:
        mod = importlib.import_module('ai_c_test_generator.safety_policy')
        return getattr(mod, 'SafetyPolicy')
    except ImportError:
        # Fallback: allow running from repo root without installing CW_Test_Gen.
        workspace_root = Path(__file__).parent.resolve()
        cw_test_gen_root = workspace_root / 'CW_Test_Gen'
        if str(cw_test_gen_root) not in sys.path:
            sys.path.insert(0, str(cw_test_gen_root))
        mod = importlib.import_module('ai_c_test_generator.safety_policy')
        return getattr(mod, 'SafetyPolicy')


def _import_cw_coverage_module():
    try:
        return importlib.import_module('ai_c_test_coverage.coverage')
    except ImportError:
        workspace_root = Path(__file__).parent.resolve()
        cw_test_cov_root = workspace_root / 'CW_Test_Cov'
        if cw_test_cov_root.exists():
            sys_path_entry = str(cw_test_cov_root)
            if sys_path_entry not in sys.path:
                sys.path.insert(0, sys_path_entry)
            try:
                return importlib.import_module('ai_c_test_coverage.coverage')
            except Exception:
                return None
        return None
    except Exception:
        return None
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


# Demo UI mode flag.
# - False: engineering/internal (shows PHASE numbers, more technical copy)
# - True: client-facing (shows step titles, less phase-oriented)
_DEMO_CLIENT_MODE = True

def print_phase(phase_num, title):
    print(f"\n{Colors.HEADER}{'='*70}{Colors.ENDC}")
    if _DEMO_CLIENT_MODE:
        print(f"{Colors.BOLD}{Colors.OKCYAN}{title}{Colors.ENDC}")
    else:
        print(f"{Colors.BOLD}{Colors.OKCYAN}PHASE {phase_num}: {title}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*70}{Colors.ENDC}\n")

def print_success(msg):
    print(f"{Colors.OKGREEN}✅ {msg}{Colors.ENDC}")

def print_error(msg):
    print(f"{Colors.FAIL}❌ {msg}{Colors.ENDC}")

def print_info(msg):
    print(f"{Colors.OKBLUE}ℹ️  {msg}{Colors.ENDC}")


def _verify_per_function_blocks(master_test_path: Path) -> tuple[bool, str]:
    if not master_test_path.exists():
        return False, f"Master test file not found: {master_test_path}"

    try:
        content = master_test_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Failed to read master test file: {e}"

    blocks = re.findall(r"^// === BEGIN TESTS: .+? ===$", content, flags=re.MULTILINE)
    if not blocks:
        return False, "No per-function test blocks found in master test file."

    return True, f"Verified {len(blocks)} per-function test blocks."

def run_command(cmd, cwd=None, description="Running command", stream_output: bool = False):
    """Execute a command and return success status.

    By default, output is captured and only shown on failure (keeps logs clean, avoids leaking secrets).
    If stream_output=True, stdout/stderr are streamed live (useful for build progress like Ninja "Linking x/y").
    """
    print_info(f"{description}...")

    # Demo-bundle robustness: when tools are installed from wheels, the CW_Test_* source folders
    # are not present next to run_demo.py. Avoid failing with WinError 267 by dropping invalid cwd.
    if cwd is not None:
        try:
            cwd_path = Path(cwd)
            if not cwd_path.is_dir():
                cwd = None
        except Exception:
            cwd = None
    
    # Sanitize command for display (hide API keys)
    def sanitize_cmd(cmd_list):
        sanitized: list[str] = []
        redact_next = False
        for arg in cmd_list:
            if redact_next:
                sanitized.append("***")
                redact_next = False
                continue

            # If the previous arg is --api-key, redact this value.
            if arg == '--api-key':
                sanitized.append(arg)
                redact_next = True
                continue

            # Hide inline key values and common token formats.
            if '--api-key' in arg:
                if '=' in arg:
                    key, _value = arg.split('=', 1)
                    sanitized.append(f"{key}=***")
                else:
                    sanitized.append("***")
                continue

            if arg.startswith(('AIza', 'sk-', 'gsk_', 'xoxp-', 'xoxb-', 'ghp_', 'github_pat_')):
                sanitized.append("***")
                continue

            sanitized.append(arg)
        return sanitized
    
    # Demo-safe: do not print internal command lines.

    def _redact_sensitive_text(text: str) -> str:
        if not text:
            return ""
        redacted = text
        # Basic redaction for common API key formats.
        for prefix in ("AIza", "sk-", "gsk_", "xoxp-", "xoxb-", "ghp_", "github_pat_"):
            # Replace any token-ish substring starting with a known prefix.
            redacted = redacted.replace(prefix, "***")
        # Also redact explicit --api-key occurrences in logs if present.
        redacted = redacted.replace("--api-key", "--api-key ***")
        return redacted

    def _tail_lines(text: str, max_lines: int = 120) -> str:
        if not text:
            return ""
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text
        return "\n".join(lines[-max_lines:])

    def _extract_gemini_model(stdout_text: str) -> str | None:
        if not stdout_text:
            return None
        for line in stdout_text.splitlines():
            if "Using Gemini model:" in line:
                # Example: "✅ [DEBUG] Using Gemini model: gemini-2.0-flash"
                return line.strip()
        return None
    
    env = os.environ.copy()
    # Force UTF-8 for subprocess I/O so Unicode log symbols don't crash on Windows.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    verbose_logs = os.environ.get("VERISAFE_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on")
    is_generation_step = (
        "Generating base tests" in description
        or "Generating MC/DC" in description
        or "Generating tests" in description
        or "CW_Test_Gen" in description
    )
    quiet_stream = (not verbose_logs) and stream_output and is_generation_step

    def _should_keep_log_line(line: str) -> bool:
        if verbose_logs:
            return True
        l = (line or "").strip()
        if not l:
            return False

        if any(k in l for k in ("Traceback", "Exception", "ERROR", "Error", "FAILED", "FATAL")):
            return True

        if not is_generation_step:
            return True

        internal_markers = (
            "Using Gemini model:",
            "Trying Gemini model",
            "[LLM]",
            "[INIT]",
            "calling API",
            "Prompt built",
            "Sending request",
            "Response received",
            "API response received",
        )
        return not any(m in l for m in internal_markers)

    def _filter_demo_logs(text: str) -> str:
        if verbose_logs or not text:
            return text or ""
        kept = [ln for ln in text.splitlines() if _should_keep_log_line(ln)]
        return "\n".join(kept)

    def _progress_bar(frac: float, width: int = 24) -> str:
        frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
        filled = int(round(frac * width))
        return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"

    if stream_output:
        # Stream combined stdout/stderr live.
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception as e:
            print_error(f"{description} - FAILED")
            return False, str(e)

        progress_state: dict[str, Any] = {
            "phase": "starting",
            "step": 0,
            "total": 5,
            "frames": "|/-\\",
            "i": 0,
        }
        stop_event = threading.Event()

        def _spinner() -> None:
            if not quiet_stream:
                return
            sys.stdout.write("   ")
            sys.stdout.flush()
            last_render = ""
            while not stop_event.is_set():
                progress_state["i"] += 1
                frame = progress_state["frames"][progress_state["i"] % len(progress_state["frames"]) ]
                frac = 0.0
                try:
                    frac = float(progress_state["step"]) / float(progress_state["total"])
                except Exception:
                    frac = 0.0
                msg = f"{_progress_bar(frac)} {frame} {progress_state['phase']}"
                if msg != last_render:
                    sys.stdout.write("\r   " + msg + " " * max(0, len(last_render) - len(msg)))
                    sys.stdout.flush()
                    last_render = msg
                time.sleep(0.12)
            sys.stdout.write("\r" + " " * (len(last_render) + 3) + "\r")
            sys.stdout.flush()

        spinner_thread = threading.Thread(target=_spinner, daemon=True)
        spinner_thread.start()

        last_lines: list[str] = []
        model_line: str | None = None
        assert proc.stdout is not None
        for line in proc.stdout:
            if not quiet_stream:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                l = line.strip()
                if "Reusing existing analysis" in l or "Analysis complete" in l:
                    progress_state["phase"] = "analyzing"
                    progress_state["step"] = max(progress_state["step"], 1)
                elif "Prompt built" in l or "calling API" in l or "Sending request" in l:
                    progress_state["phase"] = "calling model"
                    progress_state["step"] = max(progress_state["step"], 2)
                elif "Response received" in l or "API response received" in l:
                    progress_state["phase"] = "processing response"
                    progress_state["step"] = max(progress_state["step"], 3)
                elif "Test saved" in l or "saved to" in l:
                    progress_state["phase"] = "writing files"
                    progress_state["step"] = max(progress_state["step"], 4)
                elif "sections appended" in l or "MC/DC" in l and "appended" in l:
                    progress_state["phase"] = "finalizing"
                    progress_state["step"] = max(progress_state["step"], 5)
            if _should_keep_log_line(line):
                last_lines.append(line.rstrip("\n"))
            if len(last_lines) > 200:
                last_lines = last_lines[-200:]
            if model_line is None and "Using Gemini model:" in line:
                model_line = line.strip()

        rc = proc.wait()
        stop_event.set()
        try:
            spinner_thread.join(timeout=1.0)
        except Exception:
            pass
        if quiet_stream:
            print("")
        if rc == 0:
            print_success(f"{description} - SUCCESS")
            if verbose_logs and model_line:
                print_info(model_line)
            return True, "\n".join(last_lines)

        print_error(f"{description} - FAILED")
        combined = _filter_demo_logs(_redact_sensitive_text("\n".join(last_lines)))
        if combined:
            print("   Output (last lines):")
            print(_tail_lines(combined))
        else:
            print(f"   Output: (no output captured; exit code {rc})")
        return False, combined

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env
        )
        print_success(f"{description} - SUCCESS")
        model_line = _extract_gemini_model(result.stdout or "")
        if verbose_logs and model_line:
            print_info(model_line)
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        print_error(f"{description} - FAILED")
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        combined = "\n".join([part for part in (stdout, stderr) if part]).strip()
        combined = _filter_demo_logs(_redact_sensitive_text(combined))

        if combined:
            print("   Output (last lines):")
            print(_tail_lines(combined))
        else:
            print(f"   Output: (no output captured; exit code {e.returncode})")
        return False, combined


_GCOVR_FILE_PATH_RE = re.compile(r"<th scope=\"row\">File:</th>\s*<td>([^<]+)</td>", re.IGNORECASE)
_GCOVR_DIR_PATH_RE = re.compile(r"<th scope=\"row\">Directory:</th>\s*<td>([^<]+)</td>", re.IGNORECASE)


def _materialize_per_file_reports(report_root: Path, *, report_base: str | None = None) -> None:
    """Copy gcovr per-file HTML reports into coverage_report/src/... directories.

    gcovr --html-details writes hashed files like index.Foo.cpp.<hash>.html in the report root.
    The demo expects each source file under src/ to live at tests/coverage_report/src/<path>/index.html.
    We copy the gcovr output into that structure (adjusting relative CSS links) so opening the per-file path
    always shows the detailed report.
    """

    safe_base = (report_base or "index").strip() or "index"
    patterns = ['index.*.html']
    base_pattern = f"{safe_base}_coverage.*.html"
    if base_pattern not in patterns:
        patterns.append(base_pattern)

    summary_names = {
        'index.html',
        'index.functions.html',
        f"{safe_base}_coverage.html",
        f"{safe_base}_coverage.functions.html",
    }

    hashed_reports: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        for candidate in report_root.glob(pattern):
            if not candidate.is_file():
                continue
            name = candidate.name
            if name in summary_names:
                continue
            if name.endswith('.functions.html') or '.functions.' in name:
                continue
            if name in seen:
                continue
            seen.add(name)
            hashed_reports.append(candidate)

    # gcovr omits hashed detail files when --html-details is paired with a single
    # --filter/--include-file target (it writes the line-by-line view directly into
    # the summary). In that situation, fall back to processing the canonical
    # summary HTML so we still materialize src/<path>/index.html.
    if not hashed_reports:
        fallback_candidates = [report_root / f"{safe_base}_coverage.html", report_root / 'index.html']
        hashed_reports = [p for p in fallback_candidates if p.exists() and p.is_file()]
        if not hashed_reports:
            return

    src_root = report_root / 'src'
    if src_root.exists():
        shutil.rmtree(src_root)

    produced = 0
    css_candidates = [report_root / 'index.css', report_root / f"{safe_base}_coverage.css"]
    css_file = next((p for p in css_candidates if p.exists()), css_candidates[0])
    func_candidates = [report_root / 'index.functions.html', report_root / f"{safe_base}_coverage.functions.html"]
    functions_index = next((p for p in func_candidates if p.exists()), func_candidates[0])

    for html_file in hashed_reports:
        name = html_file.name
        if name in ('index.html', 'index.functions.html'):
            continue

        try:
            text = html_file.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        match = _GCOVR_FILE_PATH_RE.search(text)
        if not match:
            continue

        rel_path = match.group(1).strip().replace('\\', '/')
        if not rel_path.startswith('src/'):
            dir_match = _GCOVR_DIR_PATH_RE.search(text)
            if dir_match:
                rel_dir = dir_match.group(1).strip().replace('\\', '/').lstrip('./')
                rel_dir = rel_dir.rstrip('/')
                candidate = f"{rel_dir}/{rel_path.lstrip('./')}" if rel_dir else rel_path
                candidate = candidate.replace('//', '/')
                if candidate.startswith('src/'):
                    rel_path = candidate
        if not rel_path.startswith('src/'):
            continue

        dest_dir = (report_root / Path(rel_path)).with_suffix('')
        dest_dir.mkdir(parents=True, exist_ok=True)

        replacements: dict[str, str] = {}
        if css_file and css_file.exists():
            css_rel = os.path.relpath(css_file, dest_dir).replace('\\', '/')
            replacements['href="index.css"'] = f'href="{css_rel}"'
            replacements[f'href="{safe_base}_coverage.css"'] = f'href="{css_rel}"'
        if functions_index and functions_index.exists():
            func_rel = os.path.relpath(functions_index, dest_dir).replace('\\', '/')
            replacements['href="index.functions.html"'] = f'href="{func_rel}"'
            replacements[f'href="{safe_base}_coverage.functions.html"'] = f'href="{func_rel}"'

        rewritten = text
        for needle, repl in replacements.items():
            rewritten = rewritten.replace(needle, repl)

        dest_file = dest_dir / 'index.html'
        dest_file.write_text(rewritten, encoding='utf-8')
        produced += 1

    if produced:
        print_info(f"Materialized {produced} per-file coverage reports under tests/coverage_report/src")


def _load_dotenv_if_present(dotenv_path: Path) -> None:
    """Minimal .env loader (KEY=VALUE per line). Only sets keys not already in os.environ."""
    try:
        if not dotenv_path.exists():
            return
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        # Best-effort only; demo should still run without dotenv support.
        return


def _cmake_test_name_for_test_file(repo_path: Path, test_path: Path) -> str:
    """Match RailwaySignalSystem/tests/CMakeLists.txt naming scheme."""
    try:
        rel = test_path.relative_to(repo_path / "tests")
    except Exception:
        rel = Path(test_path.name)

    stem = rel.stem
    dir_part = str(rel.parent).replace("\\", "/")
    if dir_part in ("", "."):
        return stem
    return f"{stem}__{dir_part.replace('/', '__')}"


def enforce_manual_review_gate(repo_path: Path) -> None:
    """Block build/run unless all generated tests are explicitly approved."""

    review_dir = repo_path / "tests" / "review"
    review_required = review_dir / "review_required.md"

    def _approval_flag_candidates(test_path: Path) -> list[Path]:
        """Return candidate approval flag paths.

        Preferred: tests/review/<repo-path-under-tests>.flag (mirrors project structure)
        Back-compat (older mirrored scheme): tests/review/<repo-relative test path>.flag
        Legacy scheme (back-compat): tests/review/APPROVED.<filename>.flag
        """
        try:
            rel = test_path.relative_to(repo_path)
        except Exception:
            rel = Path(test_path.name)

        rel_no_tests = rel
        if rel_no_tests.parts[:1] == ("tests",):
            rel_no_tests = Path(*rel_no_tests.parts[1:])

        preferred = review_dir / rel_no_tests.parent / f"{rel_no_tests.name}.flag"
        compat_mirrored = review_dir / rel.parent / f"{rel.name}.flag"
        legacy = review_dir / f"APPROVED.{test_path.name}.flag"
        return [preferred, compat_mirrored, legacy]

    def _parse_generated_test_files(path: Path) -> list[Path]:
        try:
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except Exception:
            return []

        lines = text.split("\n")
        in_section = False
        generated: list[Path] = []
        for line in lines:
            if line.strip() == "## Generated test files":
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if not in_section:
                continue
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            item = stripped.lstrip("-").strip()
            if not item or item == "(none)":
                continue
            item = item.replace("\\", "/")

            # Items should ideally be repo-relative paths (e.g., tests/test_Foo.cpp).
            # Some generators may emit bare filenames; if so, resolve under <repo>/tests/.
            candidate = Path(item)
            if candidate.is_absolute():
                resolved = candidate
            else:
                resolved = repo_path / candidate
                if not resolved.exists():
                    alt = repo_path / "tests" / candidate
                    if alt.exists():
                        resolved = alt

            generated.append(resolved)
        return generated

    generated_tests = _parse_generated_test_files(review_required)
    if not generated_tests:
        print("❌ Manual review not approved. Build and execution halted.")
        sys.exit(3)

    def _is_iso_date(value: str) -> bool:
        try:
            # Accept YYYY-MM-DD or full ISO timestamp.
            datetime.date.fromisoformat(value)
            return True
        except Exception:
            try:
                datetime.datetime.fromisoformat(value)
                return True
            except Exception:
                return False

    def _approval_ok(content: str) -> bool:
        text = (content or "").replace("\r\n", "\n")
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) < 3:
            return False
        if lines[0].lower() != "approved = true":
            return False
        if not lines[1].lower().startswith("reviewed_by ="):
            return False
        if not lines[2].lower().startswith("date ="):
            return False

        reviewed_by = lines[1].split("=", 1)[1].strip() if "=" in lines[1] else ""
        date_val = lines[2].split("=", 1)[1].strip() if "=" in lines[2] else ""

        # Reject placeholders.
        if reviewed_by in ("<human_name>", ""):
            return False
        if date_val in ("<ISO date>", ""):
            return False
        if not _is_iso_date(date_val):
            return False
        return True

    for test_path in generated_tests:
        content = ""
        found = False
        for approved_path in _approval_flag_candidates(test_path):
            try:
                content = approved_path.read_text(encoding="utf-8").replace("\r\n", "\n")
                found = True
                break
            except Exception:
                continue

        if not found or not _approval_ok(content):
            print("❌ Manual review not approved. Build and execution halted.")
            sys.exit(3)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text_bytes(path: Path) -> bytes:
    # Hash on bytes to avoid newline normalization differences.
    return path.read_bytes()


def ensure_approvals_registry(repo_path: Path) -> Path:
    """Ensure tests/.approvals.json exists.

    This is the single source of truth for section approvals (pipeline v2).
    """

    # NOTE: In v2, the approvals registry is owned by CW_Test_Gen.
    # This demo should never write/overwrite it.
    approvals_path = repo_path / "tests" / ".approvals.json"
    approvals_path.parent.mkdir(parents=True, exist_ok=True)
    return approvals_path


def cleanup_analysis_artifacts(repo_path: Path) -> None:
    """Remove non-reviewable or duplicate analysis artifacts.

    Policy: keep reviewer-friendly outputs only and avoid emitting any
    fingerprint/hash artifacts.
    """

    analysis_dir = repo_path / "tests" / "analysis"
    if not analysis_dir.exists():
        return

    # Explicit removals (avoid destructive whitelisting).
    remove_names = {
        "source_fingerprints.json",
        "functions.txt",
        "hardware_functions.txt",
        "file_summaries.txt",
        "call_depths.txt",
    }

    for p in list(analysis_dir.glob("*")):
        try:
            if p.name in remove_names:
                p.unlink(missing_ok=True)
                continue
            # Excel temporary lock files (e.g. "~$analysis.xlsx")
            if p.is_file() and p.name.startswith("~$") and p.suffix.lower() == ".xlsx":
                p.unlink(missing_ok=True)
        except Exception:
            # Best-effort cleanup.
            continue


def phase0_prepare_analysis_dir(repo_path: Path) -> Path:
    """Phase 0: prepare tests/analysis for deterministic, reviewable outputs."""

    analysis_dir = repo_path / "tests" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cleanup_analysis_artifacts(repo_path)
    return analysis_dir


def phase0_source_fingerprinting(repo_path: Path, source_root: Path | None = None) -> Path:
    """Phase 0: compute source_hash for repo sources and persist under tests/analysis.

    Minimum viable implementation: sha256(file bytes). (AST hash can be added later.)
    """

    analysis_dir = repo_path / "tests" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    fingerprints_path = analysis_dir / "source_fingerprints.json"

    src_root = source_root or (repo_path / "src")
    # If repo doesn't have src/, still produce the file (empty) so later phases are deterministic.
    source_files: list[Path] = []
    if src_root.exists():
        for ext in ("*.c", "*.cc", "*.cpp", "*.cxx", "*.h", "*.hpp"):
            source_files.extend(src_root.rglob(ext))
        # Avoid hashing artifacts if someone has a tests/ folder under src.
        source_files = [p for p in source_files if "tests" not in p.parts]

    files_payload: dict[str, Any] = {}
    for path in sorted(set(source_files)):
        try:
            rel = path.relative_to(repo_path).as_posix()
            files_payload[rel] = {
                "source_hash": _sha256_bytes(_read_text_bytes(path)),
            }
        except Exception:
            # Best-effort: skip unreadable files.
            continue

    payload: dict[str, Any] = {
        "version": "1.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source_root": (src_root.relative_to(repo_path).as_posix() if src_root.exists() else None),
        "files": files_payload,
    }
    fingerprints_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return fingerprints_path


def _build_status_file(repo_path: Path) -> Path:
    return repo_path / 'tests' / '.build_status.json'


def _read_build_status(repo_path: Path) -> dict[str, Any] | None:
    path = _build_status_file(repo_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _read_harness_metadata(repo_path: Path) -> dict[str, Any] | None:
    path = repo_path / '.verisafe' / 'metadata.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _write_build_status(repo_path: Path, *, status: str, details: str | None = None) -> None:
    path = _build_status_file(repo_path)
    payload = {
        'status': str(status or 'UNKNOWN').upper(),
        'details': (details or '').strip(),
        'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    except Exception:
        pass


def _summarize_log_tail(text: str, max_lines: int = 20) -> str:
    if not text:
        return ''
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def run_incremental_pipeline_v2(
    *,
    repo_path: Path,
    workspace: Path,
    args: argparse.Namespace,
    from_phase: int,
    to_phase: int,
    selected_model: str | None,
    interactive_loop: bool,
    generation_source_dir: str | None = None,
    generation_file: str | None = None,
    execution_source_scope_rel: str | None = None,
    run_tests: bool = True,
) -> bool:
    """Incremental (v2) pipeline runner.

    Current implementation focuses on Phases 0-2 and scaffolds 3-8.
    """

    if from_phase < 0 or to_phase > 8 or from_phase > to_phase:
        print_error("Invalid phase range. Must be within 0-8.")
        return False

    try:
        _ensure_repo_prereqs(repo_path)
    except Exception:
        pass

    def _cmake_test_name_for_test_file_rel(test_file_rel: str) -> str:
        rel = (test_file_rel or "").replace("\\", "/")
        if rel.startswith("tests/"):
            rel = rel[len("tests/"):]
        p = Path(rel)
        stem = p.stem
        dir_part = str(p.parent).replace("\\", "/")
        if dir_part in ("", "."):
            return stem
        return f"{stem}__{dir_part.replace('/', '__')}"

    def _compute_ctest_regex_for_source_scope(source_scope_rel: str) -> str | None:
        """Best-effort mapping: source_rel -> set of CTest test names.

        Uses tests/.approvals.json (v2 sections) to find test_file_rel entries.
        """

        scope_norm = str(source_scope_rel or '').replace('\\', '/').strip()
        if not scope_norm:
            return None

        approvals_path_local = repo_path / 'tests' / '.approvals.json'
        if not approvals_path_local.exists():
            return None

        try:
            reg = json.loads(approvals_path_local.read_text(encoding='utf-8'))
        except Exception:
            return None

        sections = (reg or {}).get('sections', {})
        if not isinstance(sections, dict) or not sections:
            return None

        harness_meta = _read_harness_metadata(repo_path) or {}
        meta_map_raw = harness_meta.get('test_names_by_file') or {}
        test_names_lookup: dict[str, list[str]] = {}
        for key, value in meta_map_raw.items():
            norm_key = str(key or '').replace('\\', '/').lstrip('./')
            if not norm_key:
                continue
            if isinstance(value, list):
                test_names_lookup[norm_key] = [str(v) for v in value if isinstance(v, str) and v]

        test_names: set[str] = set()
        for s in sections.values():
            if not isinstance(s, dict) or s.get('active') is not True:
                continue
            src_rel = str(s.get('source_rel') or '').replace('\\', '/')
            if src_rel != scope_norm:
                continue
            test_file_rel = s.get('test_file_rel')
            if not isinstance(test_file_rel, str) or not test_file_rel:
                continue
            norm_rel = test_file_rel.replace('\\', '/').lstrip('./')
            mapped = test_names_lookup.get(norm_rel)
            if mapped:
                test_names.update(mapped)
            else:
                test_names.add(_cmake_test_name_for_test_file_rel(test_file_rel))

        if not test_names:
            return None

        return "|".join(sorted(test_names))


    def _prune_coverage_root(output_dir: Path, report_base: str) -> None:
        """Remove top-level report files created by gcovr/run_gcovr while keeping `src/`.

        This deletes summary files like <report_base>_coverage.html, .json, .xml, .txt and
        hashed per-file detail pages left in the output directory. The materialized
        `src/` folder is preserved so the demo UI sees only per-file reports.
        """
        try:
            safe = (report_base or "").strip() or "interlocking_test_report"
            # Remove main summary files
            for ext in (".html", ".json", ".xml", ".txt"):
                p = output_dir / f"{safe}_coverage{ext}"
                try:
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception:
                    continue

            # Remove hashed per-file detail pages (e.g., <base>_coverage.FileName.<hash>.html)
            for p in output_dir.glob(f"{safe}_coverage.*.html"):
                try:
                    # Skip the canonical summary (already removed) and keep any src/ folder
                    if p.is_file():
                        p.unlink()
                except Exception:
                    continue

            # Remove functions page too, if present
            funcs = output_dir / f"{safe}_coverage.functions.html"
            try:
                if funcs.exists() and funcs.is_file():
                    funcs.unlink()
            except Exception:
                pass
        except Exception:
            return

    def _normalize_coverage_css(output_dir: Path, report_base: str) -> None:
        """Rename gcovr-generated CSS file to a generic `coverage.css` and update HTML refs."""
        try:
            safe = (report_base or "").strip() or "interlocking_test_report"
            # Common gcovr emits '<base>_coverage.css'
            candidate = output_dir / f"{safe}_coverage.css"
            target = output_dir / "coverage.css"
            if candidate.exists():
                try:
                    # Overwrite target if exists
                    if target.exists():
                        target.unlink()
                    candidate.rename(target)
                except Exception:
                    try:
                        # Try copy-and-unlink as fallback
                        shutil.copy2(candidate, target)
                        candidate.unlink()
                    except Exception:
                        return

            # Update any HTML files to reference the generic name
            for html in output_dir.rglob("*.html"):
                try:
                    txt = html.read_text(encoding='utf-8', errors='ignore')
                    if f"{safe}_coverage.css" in txt:
                        txt2 = txt.replace(f"{safe}_coverage.css", "coverage.css")
                        if txt2 != txt:
                            html.write_text(txt2, encoding='utf-8')
                except Exception:
                    continue
        except Exception:
            return

    def _coverage_report_hint_path(coverage_root: Path, include_file_rel: str | None) -> Path | None:
        """Return the most relevant coverage HTML path for status messages."""
        try:
            if include_file_rel:
                rel = include_file_rel.replace('\\', '/').lstrip('./')
                explicit = (coverage_root / Path(rel)) / 'index.html'
                if explicit.exists():
                    return explicit
        except Exception:
            pass

        src_root = coverage_root / 'src'
        if src_root.exists():
            per_file = sorted(src_root.rglob('index.html'))
            if per_file:
                return per_file[0]

        default_index = coverage_root / 'index.html'
        if default_index.exists():
            return default_index

        return None

    def _ensure_repo_prereqs(repo_path: Path) -> None:
        """Create minimal repository layout expected by the demo and harness."""
        try:
            repo_path = Path(repo_path)
            dirs = [
                repo_path / 'tests',
                repo_path / 'tests' / 'test_reports',
                repo_path / 'tests' / 'coverage_report',
                repo_path / 'extern',
                repo_path / 'build_instrumented',
            ]
            for d in dirs:
                try:
                    d.mkdir(parents=True, exist_ok=True)
                except Exception:
                    continue

            gitkeep = repo_path / 'extern' / '.gitkeep'
            try:
                if not gitkeep.exists():
                    gitkeep.write_text('', encoding='utf-8')
            except Exception:
                pass

            gtest_readme = repo_path / 'extern' / 'googletest' / 'README.md'
            try:
                if not gtest_readme.exists():
                    gtest_readme.parent.mkdir(parents=True, exist_ok=True)
                    gtest_readme.write_text('# vendored googletest placeholder\n', encoding='utf-8')
            except Exception:
                pass

            try:
                (repo_path / 'tests' / 'coverage_report' / 'src').mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

        except Exception:
            return


    @dataclass
    class HarnessInfo:
        root: Path
        build_dir: Path
        generated_dir: Path
        extern_dir: Path
        cmake_path: Path
        metadata_path: Path
        production_sources: list[str]
        test_sources: list[str]
        test_names_by_file: dict[str, list[str]]


    def _read_instrumentation_marker(repo_path: Path) -> dict[str, Any] | None:
        marker = repo_path / 'tests' / '.instrumented'
        if not marker.exists():
            return None
        try:
            return json.loads(marker.read_text(encoding='utf-8'))
        except Exception:
            return None


    def _collect_production_sources(repo_path: Path) -> list[str]:
        analysis_json = repo_path / 'tests' / 'analysis' / 'analysis.json'
        sources: list[str] = []

        def _normalize_rel(value: str) -> str:
            return value.replace('\\', '/').lstrip('./')

        if analysis_json.exists():
            try:
                payload = json.loads(analysis_json.read_text(encoding='utf-8'))
                files = payload.get('files', {})
                if isinstance(files, dict):
                    for key, entry in files.items():
                        if not isinstance(entry, dict):
                            continue
                        path_val = str(entry.get('path') or key or '')
                        if not path_val:
                            continue
                        language = str(entry.get('language') or '').lower()
                        if language and not language.startswith(('c', 'cpp', 'c++')):
                            continue
                        if bool(entry.get('is_header')):
                            continue
                        rel_norm = _normalize_rel(path_val)
                        if rel_norm:
                            sources.append(rel_norm)
            except Exception:
                sources = []

        if not sources:
            src_root = repo_path / 'src'
            if src_root.exists():
                for candidate in sorted(src_root.rglob('*')):
                    if not candidate.is_file():
                        continue
                    suffix = candidate.suffix.lower()
                    if suffix not in ('.c', '.cc', '.cpp', '.cxx'):
                        continue
                    try:
                        rel = candidate.relative_to(repo_path)
                    except ValueError:
                        rel = candidate
                    sources.append(_normalize_rel(str(rel)))

        seen: set[str] = set()
        deduped: list[str] = []
        for rel in sources:
            rel_norm = _normalize_rel(rel)
            if not rel_norm or rel_norm in seen:
                continue
            seen.add(rel_norm)
            deduped.append(rel_norm)
        return deduped


    def _collect_approved_test_files(repo_path: Path) -> list[str]:
        approvals_path = repo_path / 'tests' / '.approvals.json'
        if not approvals_path.exists():
            return []
        try:
            data = json.loads(approvals_path.read_text(encoding='utf-8'))
        except Exception:
            return []

        sections = data.get('sections', {})
        if not isinstance(sections, dict):
            return []

        tests: set[str] = set()
        for entry in sections.values():
            if not isinstance(entry, dict):
                continue
            if entry.get('active') is not True:
                continue
            if entry.get('approved') is not True:
                continue
            test_rel = entry.get('test_file_rel')
            if not isinstance(test_rel, str) or not test_rel:
                continue
            normal = test_rel.replace('\\', '/').lstrip('./')
            tests.add(normal)
        return sorted(tests)


    def _extract_gtest_names(src_path: Path) -> list[str]:
        try:
            text = src_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return []

        pattern = re.compile(r"^\s*(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*([A-Za-z0-9_\.]+)\s*,\s*([A-Za-z0-9_\.]+)\s*\)", re.MULTILINE)
        names: list[str] = []
        for match in pattern.finditer(text):
            suite, case = match.group(1), match.group(2)
            names.append(f"{suite}.{case}")
        return names

    def _sync_generated_tests(repo_path: Path, harness_root: Path, test_rel_paths: list[str]) -> tuple[list[str], dict[str, list[str]]]:
        generated_dir = harness_root / 'generated'
        if generated_dir.exists():
            shutil.rmtree(generated_dir)
        generated_dir.mkdir(parents=True, exist_ok=True)

        harness_rel_paths: list[str] = []
        test_name_map: dict[str, list[str]] = {}
        for rel in test_rel_paths:
            src = (repo_path / rel).resolve()
            if not src.exists():
                print_info(f"Approved test missing on disk; skipping: {rel}")
                continue
            rel_norm = rel.replace('\\', '/').lstrip('./')
            rel_no_prefix = rel_norm
            if rel_no_prefix.startswith('tests/'):
                rel_no_prefix = rel_no_prefix[len('tests/'):]
            dest = generated_dir / rel_no_prefix
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            harness_rel_paths.append(dest.relative_to(harness_root).as_posix())
            test_name_map[rel_norm] = _extract_gtest_names(src)

        if not harness_rel_paths:
            placeholder = generated_dir / 'verisafe_placeholder_test.cpp'
            placeholder_content = (
                "#include <gtest/gtest.h>\n\n"
                "TEST(VerisafeHarness, Placeholder) {\n"
                "    GTEST_SKIP() << \"No approved VERISAFE tests available.\";\n"
                "}\n"
            )
            placeholder.write_text(placeholder_content, encoding='utf-8')
            harness_rel_paths.append(placeholder.relative_to(harness_root).as_posix())
            test_name_map['generated/verisafe_placeholder_test.cpp'] = ['VerisafeHarness.Placeholder']

        return harness_rel_paths, test_name_map


    def _write_harness_cmake(
        *,
        harness_root: Path,
        production_sources: list[str],
        harness_test_sources: list[str],
        include_dirs: list[str],
    ) -> Path:
        if not production_sources:
            raise RuntimeError("No production sources available for harness generation.")

        cmake_lines = [
            "cmake_minimum_required(VERSION 3.20)",
            "project(verisafe_harness LANGUAGES CXX)",
            "",
            "set(CMAKE_CXX_STANDARD 17)",
            "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
            "set(CMAKE_POSITION_INDEPENDENT_CODE ON)",
            "",
            "set(VERISAFE_ROOT \"${CMAKE_CURRENT_LIST_DIR}\")",
            "get_filename_component(REPO_ROOT \"${VERISAFE_ROOT}/..\" ABSOLUTE)",
            "",
            "if(NOT EXISTS \"${VERISAFE_ROOT}/extern/googletest/CMakeLists.txt\")",
            "  message(FATAL_ERROR \"GoogleTest not found under .verisafe/extern/googletest. Please vendor it before running the harness.\")",
            "endif()",
            "add_subdirectory(\"${VERISAFE_ROOT}/extern/googletest\" googletest-build EXCLUDE_FROM_ALL)",
            "",
            "set(PRODUCTION_SOURCES",
        ]

        for src in production_sources:
            cmake_lines.append(f'    "${{REPO_ROOT}}/{src}"')
        cmake_lines.append(")")
        cmake_lines.append("")
        cmake_lines.append("add_library(verisafe_under_test STATIC ${PRODUCTION_SOURCES})")

        if include_dirs:
            cmake_lines.append("target_include_directories(verisafe_under_test PUBLIC")
            for inc in include_dirs:
                cmake_lines.append(f'    "${{REPO_ROOT}}/{inc}"')
            cmake_lines.append(")")

        cmake_lines.append("")
        cmake_lines.append("set(GENERATED_TEST_SOURCES")
        for test_src in harness_test_sources:
            cmake_lines.append(f'    "${{VERISAFE_ROOT}}/{test_src}"')
        cmake_lines.append(")")
        cmake_lines.append("")
        cmake_lines.append("add_executable(verisafe_tests ${GENERATED_TEST_SOURCES})")
        cmake_lines.append("target_link_libraries(verisafe_tests PRIVATE verisafe_under_test gtest gtest_main)")
        cmake_lines.append("target_include_directories(verisafe_tests PRIVATE")
        cmake_lines.append('    "${VERISAFE_ROOT}/generated"')
        for inc in include_dirs:
            cmake_lines.append(f'    "${{REPO_ROOT}}/{inc}"')
        cmake_lines.append(")")
        cmake_lines.append("")
        cmake_lines.append("enable_testing()")
        cmake_lines.append("include(GoogleTest)")
        cmake_lines.append("# Discover and register individual GoogleTest cases with CTest so ctest -R filters work")
        cmake_lines.append("gtest_discover_tests(verisafe_tests WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})")
        cmake_lines.append("add_test(NAME verisafe_tests COMMAND verisafe_tests)")
        cmake_lines.append("")

        cmake_path = harness_root / 'CMakeLists.txt'
        cmake_path.write_text("\n".join(cmake_lines), encoding='utf-8')
        return cmake_path


    def _prepare_harness(repo_path: Path) -> HarnessInfo:
        harness_root = repo_path / '.verisafe'
        build_dir = harness_root / 'build'
        extern_dir = harness_root / 'extern'
        generated_dir = harness_root / 'generated'

        for d in (build_dir, extern_dir, generated_dir):
            d.mkdir(parents=True, exist_ok=True)

        production_sources = _collect_production_sources(repo_path)
        if not production_sources:
            raise RuntimeError("No production sources detected. Run analysis (phase 1) before preparing the harness.")

        test_rel_paths = _collect_approved_test_files(repo_path)
        harness_test_paths, test_name_map = _sync_generated_tests(repo_path, harness_root, test_rel_paths)

        gtest_dir = extern_dir / 'googletest'
        repo_gtest = repo_path / 'extern' / 'googletest'

        # If harness doesn't already contain googletest, prefer a repo-local vendored copy.
        # Otherwise, attempt to locate a bundled third-party copy under the tool at
        # tools/third_party/googletest or tools/third_party/googletest-<version>.
        if not gtest_dir.exists():
            # 1) Repo-local vendored copy (highest priority)
            if repo_gtest.exists() and (repo_gtest / 'CMakeLists.txt').exists():
                shutil.copytree(repo_gtest, gtest_dir, dirs_exist_ok=True)
            else:
                # 2) Tool-bundled third_party (support both exact and versioned folder names)
                tool_third_party_base = Path(__file__).resolve().parent / 'tools' / 'third_party'
                candidate: Path | None = None
                exact = tool_third_party_base / 'googletest'
                if exact.exists() and (exact / 'CMakeLists.txt').exists():
                    candidate = exact
                else:
                    # Look for googletest-<version> style directories
                    if tool_third_party_base.exists():
                        for p in sorted(tool_third_party_base.glob('googletest*')):
                            if p.is_dir() and (p / 'CMakeLists.txt').exists():
                                candidate = p
                                break

                if candidate is not None:
                    shutil.copytree(candidate, gtest_dir, dirs_exist_ok=True)
                else:
                    raise RuntimeError(
                        "GoogleTest not found. Vendor a full googletest tree into the repository's 'extern/googletest' "
                        "or place a copy under this tool at 'tools/third_party/googletest' (or 'tools/third_party/googletest-<version>')."
                    )
        elif not (gtest_dir / 'CMakeLists.txt').exists():
            raise RuntimeError(".verisafe/extern/googletest is missing CMakeLists.txt. Vendor full GoogleTest before running.")

        include_dirs: list[str] = []
        if (repo_path / 'include').exists():
            include_dirs.append('include')
        if (repo_path / 'src').exists():
            include_dirs.append('src')

        # Ensure deterministic harness CMakeLists (non-invasive): write a stable
        # CMakeLists.txt under `.verisafe/` that expects -DREPO_ROOT=<repo_root>.
        cmake_path = harness_root / 'CMakeLists.txt'
        cmake_lines = [
            'cmake_minimum_required(VERSION 3.20)',
            'project(verisafe_harness LANGUAGES CXX)',
            '',
            'set(CMAKE_CXX_STANDARD 17)',
            'set(CMAKE_CXX_STANDARD_REQUIRED ON)',
            'set(CMAKE_POSITION_INDEPENDENT_CODE ON)',
            '',
            '# REPO_ROOT must be supplied with -DREPO_ROOT=<path> when configuring CMake',
            'if(NOT DEFINED REPO_ROOT)',
            '  message(FATAL_ERROR "REPO_ROOT must be provided when configuring the verisafe harness")',
            'endif()',
            '',
            'file(GLOB_RECURSE PROD_SOURCES "${REPO_ROOT}/src/*.cpp")',
            'add_library(verisafe_under_test STATIC ${PROD_SOURCES})',
            'target_include_directories(verisafe_under_test PUBLIC "${REPO_ROOT}/include" "${REPO_ROOT}/src")',
            '',
            'if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/extern/googletest/CMakeLists.txt")',
            '  message(FATAL_ERROR "GoogleTest not found under .verisafe/extern/googletest. Vendor it before running the harness.")',
            'endif()',
            'add_subdirectory(extern/googletest)',
            '',
            'file(GLOB_RECURSE GENERATED_TESTS "${CMAKE_CURRENT_SOURCE_DIR}/generated/*.cpp")',
            'add_executable(verisafe_tests ${GENERATED_TESTS})',
            'target_link_libraries(verisafe_tests PRIVATE verisafe_under_test gtest gtest_main)',
            'target_include_directories(verisafe_tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/generated" "${REPO_ROOT}/include" "${REPO_ROOT}/src")',
            '',
            'enable_testing()',
            'include(CTest)',
            'include(GoogleTest)',
            '# Discover tests so ctest -R filters per GoogleTest case',
            'gtest_discover_tests(verisafe_tests WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})',
            'add_test(NAME verisafe_tests COMMAND verisafe_tests)',
            '',
        ]
        cmake_content = '\n'.join(cmake_lines)
        need_write = True
        if cmake_path.exists():
            try:
                if cmake_path.read_text(encoding='utf-8') == cmake_content:
                    need_write = False
            except Exception:
                pass
        if need_write:
            cmake_path.write_text(cmake_content, encoding='utf-8')

        metadata_path = harness_root / 'metadata.json'
        metadata = {
            'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'production_sources': production_sources,
            'tests': harness_test_paths,
            'test_names_by_file': test_name_map,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

        return HarnessInfo(
            root=harness_root,
            build_dir=build_dir,
            generated_dir=generated_dir,
            extern_dir=extern_dir,
            cmake_path=cmake_path,
            metadata_path=metadata_path,
            production_sources=production_sources,
            test_sources=harness_test_paths,
            test_names_by_file=test_name_map,
        )

        

    # Load safety policy once and drive phase requirements from it.
    try:
        SafetyPolicy = _get_safety_policy_class()
        policy = SafetyPolicy.load(
            safety_level=getattr(args, 'safety_level', 'QM'),
            repo_root=repo_path,
            policy_file=getattr(args, 'policy_file', None),
            disable_mcdc=bool(getattr(args, 'disable_mcdc', False)),
            workspace_root=workspace,
        )
    except Exception as e:
        print_error(f"Failed to load safety policy: {e}")
        return False

    # Validate planning_constraints.json produced by the SafetyPolicy, if present.
    try:
        vc_path = workspace / 'work' / 'planning_constraints.json'
        schema_path = workspace / 'schemas' / 'planning_constraints.schema.json'
        validator_file = workspace / 'tools' / 'schema' / 'validate.py'
        if validator_file.exists() and vc_path.exists() and schema_path.exists():
            spec = importlib.util.spec_from_file_location("verisafe_validator", str(validator_file))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            validate_or_halt = getattr(mod, "validate_or_halt", None)
            if validate_or_halt:
                try:
                    pc_obj = json.loads(vc_path.read_text(encoding='utf-8'))
                except Exception as e:
                    print_error(f"Failed to read planning constraints for validation: {e}")
                    return False
                validate_or_halt(pc_obj, str(schema_path), artifact_name=vc_path.name)
                print_info("Planning constraints validated.")
        else:
            if not validator_file.exists():
                print_info("Validator not found; skipping planning_constraints.json validation.")
            elif not vc_path.exists():
                print_info("No planning_constraints.json emitted; skipping validation.")
            elif not schema_path.exists():
                print_info("planning_constraints.schema.json missing; skipping validation.")
    except SystemExit:
        # Validator uses sys.exit for fail-fast behavior; propagate to stop pipeline.
        raise
    except Exception as e:
        print_info(f"Planning constraints validation skipped due to error: {e}")

    # Phase 0
    if from_phase <= 0 <= to_phase:
        print_phase(0, "PREPARATION")
        phase0_prepare_analysis_dir(repo_path)
        print_success("Preparation completed.")

    approvals_path = ensure_approvals_registry(repo_path)
    if approvals_path.exists():
        print_info("Approval data ready.")
    else:
        print_info("Approval data will be created during generation.")

    # Phase 1
    if from_phase <= 1 <= to_phase:
        print_phase(1, "STATIC ANALYSIS (CW_Test_Analyzer, v2)")

        analysis_json = repo_path / "tests" / "analysis" / "analysis.json"
        analysis_xlsx = repo_path / "tests" / "analysis" / "analysis.xlsx"
        if args.skip_analysis and (analysis_json.exists() or analysis_xlsx.exists()):
            print_info("Skipping analysis (existing outputs found and --skip-analysis set).")
        else:
            success, _ = run_command(
                [
                    sys.executable,
                    '-m',
                    'ai_c_test_analyzer.cli',
                    '--repo-path',
                    str(repo_path),
                    '--safety-level',
                    policy.safety_level,
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    '--disable-mcdc',
                ],
                cwd=workspace / 'CW_Test_Analyzer',
                description="Analyzing codebase"
            )
            if not success:
                print_error("Analysis failed. Stopping incremental pipeline.")
                return False
            print_success("Analysis completed (see tests/analysis/analysis.xlsx).")
            cleanup_analysis_artifacts(repo_path)

    # Phase 2
    if from_phase <= 2 <= to_phase:
        print_phase(2, "BASE TEST GENERATION (CW_Test_Gen, v2)")
        print_info("NOTE: v2 uses per-function test blocks inside a stable master test file.")
        print_info("Unchanged functions are not regenerated; only changed/new functions produce new blocks.")

        if args.skip_generation:
            print_info("Skipping generation (--skip-generation set).")
            print_info("STOP: Human review is mandatory before any build/run.")
            return True

        _load_dotenv_if_present(workspace / ".env")
        model = selected_model

        source_dir = generation_source_dir if generation_source_dir is not None else args.source_dir
        file_to_generate = generation_file

        gen_cmd = [
            sys.executable, '-m', 'ai_c_test_generator.cli',
            '--repo-path', str(repo_path),
            '--source-dir', source_dir,
            '--output', str(repo_path / 'tests'),
            '--safety-level', policy.safety_level,
        ]

        if file_to_generate:
            gen_cmd.extend(['--file', str(file_to_generate)])

        if model:
            gen_cmd.extend(['--model', model])

        if getattr(args, 'policy_file', None):
            gen_cmd.extend(['--policy-file', str(args.policy_file)])
        if getattr(args, 'disable_mcdc', False):
            gen_cmd.append('--disable-mcdc')

        if model in ('gemini', 'groq'):
            api_key = args.api_key or os.environ.get(f"{model.upper()}_API_KEY")
            if not api_key:
                print_error(f"Missing API key for model {model}.")
                print_info("Provide via --api-key or set environment variable.")
                return False
            gen_cmd.extend(['--api-key', api_key])

        success, _ = run_command(
            gen_cmd,
            cwd=workspace / 'CW_Test_Gen',
            description="Generating base tests",
            stream_output=(model == 'ollama')
        )
        if not success:
            # Common demo-packaged failure: malformed GEMINI_API_KEY loaded from .env (quotes/whitespace)
            # or an expired/revoked key.
            try:
                out_text = _ or ""
                if "API_KEY_INVALID" in out_text or "API key not valid" in out_text:
                    print_info("Gemini rejected the API key (API_KEY_INVALID).")
                    print_info("If using a .env file, ensure GEMINI_API_KEY has no quotes or trailing spaces.")
                    print_info("Example: GEMINI_API_KEY=AIza... (no surrounding quotes)")
            except Exception:
                pass
            print_error("Generation failed. Stopping incremental pipeline.")
            return False

        print_success("Base tests generated.")
        # Validate planner output (scenarios.json) if produced under workspace/work
        try:
            scenarios_path = workspace / 'work' / 'scenarios.json'
            scenarios_schema = workspace / 'schemas' / 'scenarios.schema.json'
            validator_file = workspace / 'tools' / 'schema' / 'validate.py'
            if validator_file.exists() and scenarios_path.exists() and scenarios_schema.exists():
                spec = importlib.util.spec_from_file_location("verisafe_validator", str(validator_file))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                validate_or_halt = getattr(mod, "validate_or_halt", None)
                if validate_or_halt:
                    try:
                        sc_obj = json.loads(scenarios_path.read_text(encoding='utf-8'))
                    except Exception as e:
                        print_error(f"Failed to read scenarios.json for validation: {e}")
                        return False
                    validate_or_halt(sc_obj, str(scenarios_schema), artifact_name=scenarios_path.name)
                    print_info("Scenarios validated.")
            else:
                if not validator_file.exists():
                    print_info("Validator not found; skipping scenarios.json validation.")
                elif not scenarios_path.exists():
                    print_info("No scenarios.json emitted; skipping validation.")
                elif not scenarios_schema.exists():
                    print_info("scenarios.schema.json missing; skipping validation.")
        except SystemExit:
            raise
        except Exception as e:
            print_info(f"Scenarios validation skipped due to error: {e}")
        if file_to_generate:
            source_dir_path = Path(source_dir)
            src_path = Path(file_to_generate)
            if not src_path.is_absolute():
                src_path = Path(repo_path) / source_dir_path / file_to_generate
            master_test_path = Path(repo_path) / 'tests' / src_path.relative_to(Path(repo_path)).parent / f"test_{src_path.name}"
            ok, msg = _verify_per_function_blocks(master_test_path)
            if ok:
                print_success(f"Per-function block verification: {msg}")
            else:
                print_error(f"Per-function block verification failed: {msg}")
        if to_phase == 2:
            print_info("STOP: Human review is mandatory before any build/run.")
            if _DEMO_CLIENT_MODE:
                print_info("Next: Review & Approvals.")
            else:
                print_info("Next: Phase 3 (status/approve sections).")
            return True

    # Phase 3: approvals gate
    if from_phase <= 3 <= to_phase:
        if not policy.approval_required:
            print_phase(3, "HUMAN APPROVAL")
            print_info("Safety policy: approvals not required at this safety level.")
        else:
            print_phase(3, "HUMAN REVIEW")

            # Demo-safe approval flow: never expose internal identifiers.
            try:
                gen_root = (workspace / 'CW_Test_Gen').resolve()
                if str(gen_root) not in sys.path:
                    sys.path.insert(0, str(gen_root))
                from ai_c_test_generator.approvals import ApprovalsRegistry, update_section_header
            except Exception:
                print_error("Approval subsystem unavailable. Stopping.")
                return False

            registry = ApprovalsRegistry(Path(repo_path))
            registry.load()

            # Scope approvals to the current target file when provided.
            scope_source_rel: str | None = None
            if getattr(args, 'source_dir', None) and getattr(args, 'target_file', None):
                scope_source_rel = str((Path(args.source_dir) / args.target_file).as_posix())

            pending = [
                s
                for s in registry.iter_sections()
                if s.get('active') is True
                and s.get('approved') is not True
                and (scope_source_rel is None or s.get('source_rel') == scope_source_rel)
            ]

            if pending:
                if not interactive_loop:
                    print_info("Pending approvals exist. Please review and approve the sections, then re-run.")
                    return False

                reviewer = getpass.getuser() or "reviewer"
                print_info(f"Interactive approval: reviewer='{reviewer}'")
                print_info("Sections available: BASE_TESTS, MCDC_TESTS, BOUNDARY_TESTS, ERROR_PATH_TESTS")

                kind_map = {
                    'BASE_TESTS': 'base',
                    'MCDC_TESTS': 'mcdc',
                    'BOUNDARY_TESTS': 'boundary',
                    'ERROR_PATH_TESTS': 'error_path',
                }

                while True:
                    sec = input("Enter section to approve (or 'q' to stop, 'a' to approve all): ").strip().upper()
                    if not sec or sec.lower() in ('q', 'quit', 'exit'):
                        break

                    # Allow 'A' or 'ALL' to approve all supported section labels in one go.
                    to_process = []
                    if sec in ("A", "ALL"):
                        to_process = list(kind_map.keys())
                    else:
                        to_process = [sec]

                    approved_any = False
                    for sec_key in to_process:
                        kind = kind_map.get(sec_key)
                        if not kind:
                            print_error(f"Unknown section label: {sec_key}")
                            continue

                        for s in list(registry.iter_sections()):
                            if s.get('active') is not True or s.get('approved') is True:
                                continue
                            if scope_source_rel is not None and s.get('source_rel') != scope_source_rel:
                                continue
                            if str(s.get('kind') or 'base').lower() != kind:
                                continue

                            section_sha = s.get('section_sha256')
                            test_file_rel = s.get('test_file_rel')
                            if not section_sha or not test_file_rel:
                                continue

                            reviewed_at = None
                            try:
                                reviewed_at = registry.data.get('sections', {}).get(section_sha, {}).get('reviewed_at')
                            except Exception:
                                reviewed_at = None

                            # Mark approved in the registry.
                            registry.approve(section_sha256=section_sha, reviewed_by=reviewer)

                            # Update the test file header to reflect approval.
                            test_path = (Path(repo_path) / test_file_rel)
                            try:
                                txt = test_path.read_text(encoding='utf-8')
                                txt2 = update_section_header(
                                    txt,
                                    section_sha256=section_sha,
                                    approved=True,
                                    reviewed_by=reviewer,
                                    reviewed_at_iso=reviewed_at,
                                )
                                test_path.write_text(txt2, encoding='utf-8', newline='\n')
                            except Exception:
                                pass

                            approved_any = True

                    registry.save()

                    if not approved_any:
                        print_error("No matching pending sections found.")
                        continue

                    # Recompute pending after approval.
                    pending = [
                        s
                        for s in registry.iter_sections()
                        if s.get('active') is True
                        and s.get('approved') is not True
                        and (scope_source_rel is None or s.get('source_rel') == scope_source_rel)
                    ]
                    if not pending:
                        break

            # After interactive approval, if there are no pending active sections,
            # create the harness overlay immediately so the UI shows `.verisafe/`.
            try:
                sections_now = _load_approvals_sections(repo_path)
                pending_now = [s for s in sections_now.values() if s.get('active') is True and s.get('approved') is not True]
                if not pending_now:
                    try:
                        _prepare_harness(repo_path)
                        print_info("Harness scaffolded under .verisafe/ (ready for build).")
                    except Exception as e:
                        print_info(f"Could not prepare harness immediately: {e}")
            except Exception:
                pass

    # New policy: harness overlay is mandatory and becomes the default build mechanism.
    harness_info: HarnessInfo | None = None
    try:
        harness_info = _prepare_harness(repo_path)
        print_info("Harness prepared under .verisafe/ with approved tests only.")
    except Exception as e:
        print_error(f"Harness preparation failed: {e}")
        return False

    # Instrumentation step (optional): configure and build instrumented binary tree or harness.
    if bool(getattr(args, 'instrument', False)) and from_phase <= 4 <= to_phase:
        print_phase(3, "INSTRUMENTATION")
        # Always build via the harness overlay (.verisafe). The harness is prepared
        # earlier and must exist; never configure from the repository root.
        if not harness_info:
            print_error("Internal error: harness not prepared")
            return False
        build_instrumented_dir = harness_info.build_dir
        cmake_source = harness_info.root
        cmake_cwd = harness_info.root

        cmake_config_cmd = [
            'cmake',
            '-S', str(cmake_source),
            '-B', str(build_instrumented_dir),
            '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Debug',
            '-DCMAKE_CXX_FLAGS=--coverage -g -O0',
            f'-DREPO_ROOT={str(repo_path)}',
        ]

        ok, _ = run_command(
            cmake_config_cmd,
            cwd=cmake_cwd,
            description="Configuring instrumented build",
            stream_output=True,
        )
        if not ok:
            print_error("CMake configuration for instrumented build failed.")
            return False

        jobs = os.cpu_count() or 1
        ok, out = run_command(
            ['cmake', '--build', str(build_instrumented_dir), '--', '-j', str(jobs)],
            cwd=cmake_cwd,
            description="Building instrumented code",
            stream_output=True,
        )
        if not ok:
            print_error("Building instrumented binaries failed.")
            failure_detail = _summarize_log_tail(out)
            _write_build_status(repo_path, status="FAILED", details=failure_detail or "Instrumented build failed.")
            # Controlled Fix-It stage: attempt conservative fixes on failing generated tests
            try:
                fixed = _attempt_fix_it(repo_path, out)
            except Exception as e:
                fixed = False
                print_info(f"Fix-It stage aborted due to error: {e}")

            if fixed:
                print_info("Fix-It applied; retrying build")
                ok2, out2 = run_command(
                    ['cmake', '--build', str(build_instrumented_dir), '--', '-j', str(jobs)],
                    cwd=cmake_cwd,
                    description="Building instrumented code (after Fix-It)",
                    stream_output=True,
                )
                if not ok2:
                    print_error("Rebuild after Fix-It failed.")
                    failure_detail = _summarize_log_tail(out2)
                    _write_build_status(repo_path, status="FAILED", details=failure_detail or "Instrumented build failed after Fix-It.")
                    return False
                ok = ok2
            else:
                return False

        _write_build_status(repo_path, status="SUCCESS", details="Instrumented build completed.")

        try:
            marker = repo_path / 'tests' / '.instrumented'
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker_payload: dict[str, Any] = {
                "build_dir": str(build_instrumented_dir),
                "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "harness": bool(harness_info is not None),
            }
            if harness_info:
                marker_payload.update(
                    {
                        "harness_root": str(harness_info.root),
                        "tests": harness_info.test_sources,
                        "production_sources": harness_info.production_sources,
                        "test_names_by_file": harness_info.test_names_by_file,
                    }
                )
            marker.write_text(json.dumps(marker_payload, indent=2), encoding='utf-8')
            print_success("Instrumented build completed and marker created.")
        except Exception:
            print_info("Instrumented build completed, but failed to write marker file.")

    # Phase 4: build + run (or build-only when run_tests=False)
    if from_phase <= 4 <= to_phase:
        if run_tests:
            print_phase(4, "BUILD + RUN REGRESSION (v2 gate)")
        else:
            print_phase(4, "BUILD ONLY (instrumentation)")
        ctest_regex_for_scope: str | None = None
        if execution_source_scope_rel:
            ctest_regex_for_scope = _compute_ctest_regex_for_source_scope(execution_source_scope_rel)
            if ctest_regex_for_scope:
                print_info(f"Scoped execution: will run ctest -R: {ctest_regex_for_scope}")
            else:
                print_info("Scoped execution requested, but no matching v2 sections were found for this source.")
                print_info("Proceeding without a ctest filter may trigger repo-wide gates.")
        # If instrumentation/harness is available, prefer running from that build tree via ctest.
        instrument_requested = bool(getattr(args, 'instrument', False))
        marker_info = _read_instrumentation_marker(repo_path)
        build_instrumented_dir = repo_path / 'build_instrumented'
        if marker_info and marker_info.get('build_dir'):
            build_instrumented_dir = Path(marker_info['build_dir'])
        harness_mode = bool(marker_info and marker_info.get('harness'))
        if harness_mode:
            instrument_requested = True

        instrument_marker_path = repo_path / 'tests' / '.instrumented'
        if instrument_requested or instrument_marker_path.exists():
            if not build_instrumented_dir.exists():
                print_error("Instrumented build directory not found. Run instrumentation first.")
                return False

            if not run_tests:
                print_info("Build-only: skipping test execution as requested.")
                return True

            ctest_cmd = ['ctest', '--output-on-failure']
            if ctest_regex_for_scope:
                ctest_cmd.extend(['-R', ctest_regex_for_scope])

            success, out = run_command(
                ctest_cmd,
                cwd=build_instrumented_dir,
                description="Running instrumented tests (ctest)",
                stream_output=True,
            )

            reports_payload: list[str] = [out or ""]

            # If the scoped filter matched zero tests, rerun once without -R so the
            # harness still executes something (and approvals/tests remain in sync).
            if ctest_regex_for_scope and ('No tests were found' in (out or '')):
                print_info("Scoped ctest filter matched zero tests; rerunning without filter.")
                success, out = run_command(
                    ['ctest', '--output-on-failure'],
                    cwd=build_instrumented_dir,
                    description="Running instrumented tests (ctest, unscoped fallback)",
                    stream_output=True,
                )
                reports_payload.append(out or "")

            try:
                reports_dir = repo_path / 'tests' / 'test_reports'
                reports_dir.mkdir(parents=True, exist_ok=True)
                report_suffix = 'harness' if harness_mode else 'instrumented'
                report_file = reports_dir / f'ctest_{report_suffix}_{datetime.datetime.now().strftime("%Y%m%dT%H%M%S")}.log'
                report_file.write_text("\n\n---\n\n".join([payload or "(no captured output)" for payload in reports_payload]), encoding='utf-8')
            except Exception:
                pass

            if not success:
                return False
        else:
            success, _ = run_command(
                [
                    sys.executable,
                    '-m',
                    'ai_test_runner.cli',
                    str(repo_path),
                    '--safety-level',
                    policy.safety_level,
                    *( ['--ctest-regex', ctest_regex_for_scope] if ctest_regex_for_scope else [] ),
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    *( ['--disable-mcdc'] if getattr(args, 'disable_mcdc', False) else [] ),
                ],
                cwd=workspace / 'CW_Test_Run',
                description="Building and running tests",
                stream_output=True,
            )
            if not success:
                return False

    # Phase 5: coverage
    if from_phase <= 5 <= to_phase:
        print_phase(5, "COVERAGE REPORTS")
        ctest_regex_for_scope: str | None = None
        include_file_for_scope: str | None = None
        if execution_source_scope_rel:
            include_file_for_scope = str(execution_source_scope_rel).replace('\\', '/').strip()
            ctest_regex_for_scope = _compute_ctest_regex_for_source_scope(execution_source_scope_rel)
            if include_file_for_scope:
                print_info(f"Scoped coverage: include file: {include_file_for_scope}")
            if ctest_regex_for_scope:
                print_info(f"Scoped coverage: will run ctest -R: {ctest_regex_for_scope}")
        coverage_generated = False
        # If an instrumented build exists, prefer gcovr for compact, per-file reports
        marker_info = _read_instrumentation_marker(repo_path)
        build_instrumented_dir = repo_path / 'build_instrumented'
        if marker_info and marker_info.get('build_dir'):
            build_instrumented_dir = Path(marker_info['build_dir'])
        instrument_marker = repo_path / 'tests' / '.instrumented'
        marker_present = instrument_marker.exists() or (marker_info is not None)
        if build_instrumented_dir.exists() and (marker_present or bool(getattr(args, 'instrument', False))):
            print_info("Instrumented build detected — generating coverage with gcovr for selected scope.")
            coverage_root = repo_path / 'tests' / 'coverage_report'
            coverage_root.mkdir(parents=True, exist_ok=True)
            coverage_index = coverage_root / 'index.html'

            gcovr_cmd = [
                sys.executable, '-m', 'gcovr',
                '-r', str(repo_path),
                '--object-directory', str(build_instrumented_dir),
                '--branches',
                '--html', '--html-details',
                '-o', str(coverage_index),
            ]

            # Prefer using the packaged coverage runner if available (richer HTML/JSON + MC/DC injection)
            cw_cov_mod = _import_cw_coverage_module()
            run_gcovr = getattr(cw_cov_mod, 'run_gcovr', None) if cw_cov_mod else None

            if run_gcovr is not None:
                try:
                    include_files = None
                    if include_file_for_scope:
                        include_files = [repo_path / include_file_for_scope.replace('\\', '/').lstrip('./')]

                    cov_out = run_gcovr(
                        repo_root=repo_path,
                        build_dir=build_instrumented_dir,
                        output_dir=coverage_root,
                        report_base='railway_coverage',
                        html=True,
                        html_details=True,
                        include_files=include_files,
                        filters=None,
                        exclude_tests=True,
                        fail_if_no_data=False,
                    )
                    if cov_out.html_report is not None and cov_out.html_report.exists():
                        # Materialize per-file stable reports for the demo
                        _materialize_per_file_reports(coverage_root, report_base='railway_coverage')
                        # Prune the top-level generated summary files; keep only `src/` materialized pages
                        _prune_coverage_root(coverage_root, 'railway_coverage')
                        # Normalize CSS filename to a generic one and update HTML references
                        _normalize_coverage_css(coverage_root, 'railway_coverage')
                        hint_path = _coverage_report_hint_path(coverage_root, include_file_for_scope)
                        if hint_path:
                            try:
                                print_success(f"Coverage report written: {hint_path.relative_to(repo_path)}")
                            except Exception:
                                print_success(f"Coverage report written: {hint_path}")
                        elif cov_out.html_report and cov_out.html_report.exists():
                            try:
                                print_success(f"Coverage report written: {cov_out.html_report.relative_to(repo_path)}")
                            except Exception:
                                print_success(f"Coverage report written: {cov_out.html_report}")
                        else:
                            print_success("Coverage artifacts updated under tests/coverage_report")
                        coverage_generated = True
                    else:
                        print_error('Coverage run completed but no HTML report generated by packaged runner.')
                except Exception as e:
                    print_error(f"Packaged coverage runner failed: {e}")
                    run_gcovr = None

            if run_gcovr is None:
                # Fallback to invoking gcovr via subprocess
                success, _ = run_command(
                    gcovr_cmd,
                    cwd=repo_path,
                    description=f"Generating gcovr report for {include_file_for_scope or 'repo'}",
                    stream_output=False,
                )
                if not success:
                    print_error("gcovr coverage generation failed; no coverage available.")
                else:
                    _materialize_per_file_reports(coverage_root, report_base='railway_coverage')
                    # Remove the gcovr summary artifacts we don't want in the coverage root
                    _prune_coverage_root(coverage_root, 'railway_coverage')
                    _normalize_coverage_css(coverage_root, 'railway_coverage')

                    hint_path = _coverage_report_hint_path(coverage_root, include_file_for_scope)
                    if hint_path:
                        try:
                            print_success(f"Coverage report written: {hint_path.relative_to(repo_path)}")
                        except Exception:
                            print_success(f"Coverage report written: {hint_path}")
                    elif coverage_index.exists():
                        try:
                            print_success(f"Coverage report written: {coverage_index.relative_to(repo_path)}")
                        except Exception:
                            print_success(f"Coverage report written: {coverage_index}")
                    else:
                        print_success("Coverage artifacts updated under tests/coverage_report")

                    try:
                        manifest_target = None
                        if hint_path and hint_path.exists():
                            manifest_target = hint_path
                        elif coverage_index.exists():
                            manifest_target = coverage_index
                        if manifest_target:
                            manifest = coverage_root / 'latest_coverage.json'
                            manifest.write_text(json.dumps({
                                'html': str(manifest_target.relative_to(repo_path).as_posix()),
                                'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            }, indent=2), encoding='utf-8')
                    except Exception:
                        pass
                    coverage_generated = True
                if to_phase == 5:
                    return True

        # Fallback: use packaged coverage CLI only if we still need coverage output.
        if not coverage_generated:
            cli_cmd = [
                sys.executable,
                '-m',
                'ai_c_test_coverage.cli',
                str(repo_path),
                '--safety-level',
                policy.safety_level,
            ]
            if ctest_regex_for_scope:
                cli_cmd += ['--ctest-regex', ctest_regex_for_scope]
            if include_file_for_scope:
                cli_cmd += ['--include-file', include_file_for_scope]
            if getattr(args, 'policy_file', None):
                cli_cmd += ['--policy-file', str(getattr(args, 'policy_file'))]

            success, _ = run_command(
                cli_cmd,
                cwd=workspace / 'CW_Test_Cov',
                description="Generating coverage",
            )
            if not success:
                return False

    # Phase 6: MC/DC gap analysis
    if from_phase <= 6 <= to_phase:
        if not policy.mcdc_analysis_enabled() or getattr(args, 'disable_mcdc', False):
            print_phase(6, "MC/DC GAP ANALYSIS")
            print_info("Safety policy: MC/DC analysis not enabled for this safety level.")
        else:
            print_phase(6, "MC/DC GAP ANALYSIS")
            success, _ = run_command(
                [
                    sys.executable,
                    '-m',
                    'ai_c_test_analyzer.cli',
                    '--repo-path',
                    str(repo_path),
                    '--mcdc',
                    '--safety-level',
                    policy.safety_level,
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    *( ['--disable-mcdc'] if getattr(args, 'disable_mcdc', False) else [] ),
                ],
                cwd=workspace / 'CW_Test_Analyzer',
                description="Analyzing MC/DC decisions",
            )
            if not success:
                return False

    # Phase 7: MC/DC test generation (append-only)
    if from_phase <= 7 <= to_phase:
        # Check for unapproved sections before proceeding to model selection (to avoid wasting API calls)
        approvals_path = repo_path / "tests" / ".approvals.json"
        if approvals_path.exists():
            try:
                reg = json.loads(approvals_path.read_text(encoding='utf-8'))
                sections = (reg or {}).get('sections', {})
                active = [s for s in sections.values() if isinstance(s, dict) and s.get('active') is True]
                unapproved = [s for s in active if s.get('approved') is not True]
                if unapproved:
                    print_error(f"Cannot generate MC/DC tests: {len(unapproved)} unapproved sections exist.")
                    print_info("Approve all sections before generating MC/DC (use option 3: Review approvals).")
                    return False
            except Exception as e:
                print_error(f"Error reading approvals registry: {e}")
                return False

        if not policy.mcdc_generation_required() or getattr(args, 'disable_mcdc', False):
            print_phase(7, "MC/DC TEST GENERATION")
            print_info("Safety policy: MC/DC generation not required for this safety level.")
        else:
            print_phase(7, "MC/DC TEST GENERATION (append-only)")
            _load_dotenv_if_present(workspace / '.env')
            model = selected_model

            # Interactive UX: offer scope selection based on mcdc_gaps.json.
            selected_sources: list[str] = []
            gaps_path = repo_path / 'tests' / 'analysis' / 'mcdc_gaps.json'
            if interactive_loop and gaps_path.exists():
                try:
                    gaps = json.loads(gaps_path.read_text(encoding='utf-8'))
                    files_map = (gaps or {}).get('files', {})
                    candidate_files = sorted([k for k, v in (files_map or {}).items() if isinstance(v, list) and len(v) > 0])
                except Exception:
                    candidate_files = []

                if candidate_files:
                    print("\nSelect MC/DC generation scope:")
                    print(f"1. Whole repo (all files with gaps: {len(candidate_files)})")
                    print("2. Single file")
                    print("3. Multiple files")
                    scope = input("Enter choice (1-3) or ENTER for default target: ").strip()

                    def _print_candidates() -> None:
                        print(f"\n{Colors.BOLD}Files with MC/DC gaps:{Colors.ENDC}")
                        for idx, rel in enumerate(candidate_files, start=1):
                            print(f"{idx:2d}. {rel}")

                    if scope == '1':
                        selected_sources = candidate_files
                    elif scope == '2':
                        _print_candidates()
                        pick = input("Select file number: ").strip()
                        if pick.isdigit() and 1 <= int(pick) <= len(candidate_files):
                            selected_sources = [candidate_files[int(pick) - 1]]
                    elif scope == '3':
                        _print_candidates()
                        pick = input("Select files (e.g. 1,3) or 'a' for all: ").strip().lower()
                        if pick in ('a', 'all', ''):
                            selected_sources = candidate_files
                        else:
                            parts = [p.strip() for p in pick.split(',') if p.strip()]
                            picks: list[str] = []
                            ok_sel = True
                            for part in parts:
                                if not part.isdigit():
                                    ok_sel = False
                                    break
                                i = int(part)
                                if i < 1 or i > len(candidate_files):
                                    ok_sel = False
                                    break
                                picks.append(candidate_files[i - 1])
                            if ok_sel and picks:
                                seen = set()
                                selected_sources = []
                                for rel in picks:
                                    if rel in seen:
                                        continue
                                    seen.add(rel)
                                    selected_sources.append(rel)

            # Non-interactive fallback (or if user skipped selection): use args.
            if not selected_sources:
                selected_sources = [str(Path(args.source_dir) / args.target_file).replace('\\', '/')]

            any_appended = False
            for rel in selected_sources:
                p = Path(rel)
                src_dir = str(p.parent.as_posix())
                file_name = p.name

                gen_cmd = [
                    sys.executable, '-m', 'ai_c_test_generator.cli', 'mcdc-generate',
                    '--repo-path', str(repo_path),
                    '--source-dir', src_dir,
                    '--file', file_name,
                    '--output', str(repo_path / 'tests'),
                    '--safety-level', policy.safety_level,
                ]

                if model:
                    gen_cmd.extend(['--model', model])
                if getattr(args, 'policy_file', None):
                    gen_cmd.extend(['--policy-file', str(args.policy_file)])
                if getattr(args, 'disable_mcdc', False):
                    gen_cmd.append('--disable-mcdc')
                if model in ('gemini', 'groq'):
                    api_key = args.api_key or os.environ.get(f"{model.upper()}_API_KEY")
                    if not api_key:
                        print_error(f"Missing API key for model {model}.")
                        return False
                    gen_cmd.extend(['--api-key', api_key])

                success, out = run_command(
                    gen_cmd,
                    cwd=workspace / 'CW_Test_Gen',
                    description=f"Generating MC/DC tests for {rel}",
                    stream_output=True,
                )
                if not success:
                    return False
                if out and ("MC/DC sections appended to" in out or "✅ Test saved to" in out):
                    any_appended = True

            if any_appended:
                print_info("MC/DC section(s) generated as UNAPPROVED. Use option 3 to approve.")
            else:
                print_info("No MC/DC sections were appended (no MC/DC decisions for the selected file(s)).")

    # Phase 8: approve + run + coverage (holistic)
    if from_phase <= 8 <= to_phase:
        print_phase(8, "APPROVE + RUN + COVERAGE")
        # Re-use Phase 3 approval check.
        status_cmd = [sys.executable, '-m', 'ai_c_test_generator.cli', 'status', '--repo-path', str(repo_path)]
        ok, _ = run_command(status_cmd, cwd=workspace / 'CW_Test_Gen', description="Checking approval status")
        if not ok and policy.approval_required:
            print_error("Pending approvals exist. Approve sections before running Phase 8.")
            return False

        ok, _ = run_command(
            [
                sys.executable,
                '-m',
                'ai_test_runner.cli',
                str(repo_path),
                '--safety-level',
                policy.safety_level,
                *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                *( ['--disable-mcdc'] if getattr(args, 'disable_mcdc', False) else [] ),
            ],
            cwd=workspace / 'CW_Test_Run',
            description="Building and running tests",
            stream_output=True,
        )
        if not ok:
            return False

        ok, _ = run_command(
            [
                sys.executable,
                '-m',
                'ai_c_test_coverage.cli',
                str(repo_path),
                '--safety-level',
                policy.safety_level,
                *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
            ],
            cwd=workspace / 'CW_Test_Cov',
            description="Generating coverage",
        )
        if not ok:
            return False

    return True


def _prompt_model_choice() -> str | None:
    print("\nSelect AI Assist Level (optional):")
    print("1. Gemini (Cloud - requires API key)")
    print("2. Groq (Cloud - requires API key)")
    print("3. Local LLM (Local - no API key needed)")
    while True:
        print("(Press ENTER to continue without AI assist)")
        model_choice = input("Enter choice (1-3) or ENTER to skip: ").strip()
        if model_choice == '':
            return None
        if model_choice == '1':
            return 'gemini'
        if model_choice == '2':
            return 'groq'
        if model_choice == '3':
            return 'ollama'
        print("Invalid choice. Please enter 1-3 (or ENTER to skip).")


def _print_client_menu(*, safety_level: str, show_engineering_menu: bool) -> None:
    print(f"\n{Colors.BOLD}{'='*70}")
    print("   VERISAFE – AI-ASSISTED SAFETY TESTING – LIVE DEMO")
    print(f"{'='*70}{Colors.ENDC}\n")

    print(f"Safety Workflow: {safety_level}")
    print("\nSelect Safety Workflow:")
    print("1. SIL0 / Early Development")
    print("   - Static analysis")
    print("   - Base unit test generation")
    print("   - Mandatory human review")
    print("   - Test Execution + Coverage")
    print("\n2. SIL2 – Safety-Related Software")
    print("   - Static analysis")
    print("   - Base unit test generation")
    print("   - Mandatory human review")
    print("   - Test Execution + Coverage (branch/decision)")
    print("\n3. SIL3 – Safety-Critical Software")
    print("   - Static analysis")
    print("   - Base unit test generation")
    print("   - Mandatory human review")
    print("   - Test Execution + Coverage")
    print("   - MC/DC gap analysis")
    print("   - MC/DC test generation (append-only)")
    print("   - Mandatory human review")
    print("   - Test Execution + Coverage (MC/DC)")
    print("\n4. SIL4 – Safety-Critical (Analysis-Driven)")
    print("   - Static analysis")
    print("   - Coverage & MC/DC gap analysis")
    print("   - Test suggestions only")
    print("   - Mandatory human approval before execution")

    print("\nA. Review & Approvals")
    print("B. View Reports (Analysis / Coverage / MC/DC)")
    print("I. Instrumentation Build (produce instrumented binaries for coverage)")
    if show_engineering_menu:
        print("L. Advanced / Engineering Mode (internal)")
    print("Q. Quit")


def _view_reports(*, repo_path: Path) -> None:
    print(f"\n{Colors.BOLD}File Status{Colors.ENDC}")

    analysis_dir = repo_path / "tests" / "analysis"
    test_src_dir = repo_path / "tests" / "src"
    test_mcdc_dir = repo_path / "tests" / "mcdc"
    test_reports_dir = repo_path / "tests" / "test_reports"
    coverage_dir = repo_path / "tests" / "coverage_report"

    # Code Analysis
    code_analysis_available = (analysis_dir / "analysis.xlsx").exists() or (analysis_dir / "analysis.json").exists()
    print(f"  Code Analysis: {'Available' if code_analysis_available else 'Not Available'}")

    # Base Tests
    base_tests_available = test_src_dir.exists() and any(f.suffix == '.cpp' for f in test_src_dir.rglob('*.cpp'))
    print(f"  Base Tests: {'Available' if base_tests_available else 'Not Available'}")

    # MC/DC Analysis
    mcdc_analysis_available = (analysis_dir / "mcdc_gaps.xlsx").exists() or (analysis_dir / "mcdc_gaps.json").exists()
    print(f"  MC/DC Analysis: {'Available' if mcdc_analysis_available else 'Not Available'}")

    # Execution Base Tests
    execution_base_available = test_reports_dir.exists()
    print(f"  Execution Base Tests: {'Available' if execution_base_available else 'Not Available'}")

    # MC/DC Tests
    mcdc_tests_available = test_mcdc_dir.exists() and any(f.suffix == '.cpp' for f in test_mcdc_dir.rglob('*.cpp'))
    print(f"  MC/DC Tests: {'Available' if mcdc_tests_available else 'Not Available'}")

    # Execution MC/DC Tests
    execution_mcdc_available = coverage_dir.exists()
    print(f"  Execution MC/DC Tests: {'Available' if execution_mcdc_available else 'Not Available'}")


def _normalize_newlines(text: str) -> str:
    return (text or '').replace('\r\n', '\n').replace('\r', '\n')


def _canonical_section_body(text: str) -> str:
    """Canonicalize section body text for stable hashing across appends.

    When a new AI-TESTGEN-SECTION is appended, we may introduce separator blank
    lines between sections. Those separator newlines must not cause previously
    approved section hashes to change.
    """

    normalized = _normalize_newlines(text)
    stripped = normalized.rstrip()
    if not stripped:
        return ''
    return stripped + '\n'


def _sha256_text(text: str) -> str:
    return hashlib.sha256(_normalize_newlines(text).encode('utf-8')).hexdigest()


def _load_approvals_sections(repo_path: Path) -> dict[str, dict[str, Any]]:
    approvals_path = repo_path / "tests" / ".approvals.json"
    if not approvals_path.exists():
        return {}
    try:
        data = json.loads(approvals_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    sections = (data or {}).get('sections', {})
    if not isinstance(sections, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in sections.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _compute_demo_state(repo_path: Path, *, source_scope: set[str] | None = None) -> dict[str, Any]:
    """Compute demo status flags from on-disk artifacts.

    If source_scope is provided, only consider approvals sections whose source_rel is in that set.
    """

    sections = _load_approvals_sections(repo_path)
    active_sections: list[dict[str, Any]] = []
    for s in sections.values():
        if s.get('active') is not True:
            continue
        if source_scope is not None:
            sr = s.get('source_rel')
            if not isinstance(sr, str) or sr.replace('\\', '/') not in source_scope:
                continue
        active_sections.append(s)

    active_pending = [s for s in active_sections if s.get('approved') is not True]
    active_approved = [s for s in active_sections if s.get('approved') is True]

    base_active = [s for s in active_sections if str(s.get('kind') or '').lower() == 'base']
    mcdc_active = [s for s in active_sections if str(s.get('kind') or '').lower() == 'mcdc']

    base_pending = [s for s in base_active if s.get('approved') is not True]
    mcdc_pending = [s for s in mcdc_active if s.get('approved') is not True]

    # Hash mismatch detection for ACTIVE+APPROVED sections: if the approved section hash no longer
    # exists in the file, the tool should treat it as requiring re-approval.
    header_re = re.compile(r"(?ms)^/\*\s*(?:AI-TEST-SECTION|AI-TESTGEN-SECTION)\s*\n(?P<body>.*?)\*/\s*\n")

    def _section_hashes_for_file(test_file_rel: str) -> set[str]:
        try:
            text = (repo_path / test_file_rel).read_text(encoding='utf-8')
        except Exception:
            return set()
        text = _normalize_newlines(text)
        matches = list(header_re.finditer(text))
        hashes: set[str] = set()
        for idx, m in enumerate(matches):
            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            hashes.add(_sha256_text(_canonical_section_body(text[body_start:body_end])))
        return hashes

    file_hash_cache: dict[str, set[str]] = {}
    hash_mismatches: list[dict[str, Any]] = []
    base_hash_mismatches: list[dict[str, Any]] = []
    mcdc_hash_mismatches: list[dict[str, Any]] = []
    for s in active_approved:
        test_file_rel = s.get('test_file_rel')
        section_sha = s.get('section_sha256')
        if not isinstance(test_file_rel, str) or not test_file_rel:
            continue
        if not isinstance(section_sha, str) or not section_sha:
            continue
        if test_file_rel not in file_hash_cache:
            file_hash_cache[test_file_rel] = _section_hashes_for_file(test_file_rel)
        if section_sha not in file_hash_cache[test_file_rel]:
            hash_mismatches.append(s)
            kind = str(s.get('kind') or '').lower()
            if kind == 'base':
                base_hash_mismatches.append(s)
            elif kind == 'mcdc':
                mcdc_hash_mismatches.append(s)

    analysis_dir = repo_path / "tests" / "analysis"
    test_reports_dir = repo_path / "tests" / "test_reports"
    coverage_dir = repo_path / "tests" / "coverage_report"

    has_analysis = analysis_dir.exists() and any(analysis_dir.iterdir())
    has_mcdc_gaps = (analysis_dir / "mcdc_gaps.json").exists()

    has_executed_tests = test_reports_dir.exists() and any(test_reports_dir.iterdir())
    has_coverage = coverage_dir.exists() and any(coverage_dir.iterdir())

    build_status_info = _read_build_status(repo_path) or {}
    build_status_value = str(build_status_info.get('status') or 'NOT_RUN').upper()
    build_status_details = str(build_status_info.get('details') or '').strip()
    build_status_updated = build_status_info.get('updated_at')

    return {
        'active_total': len(active_sections),
        'active_pending': len(active_pending),
        'active_approved': len(active_approved),
        'hash_mismatch': len(hash_mismatches),
        'base_hash_mismatch': len(base_hash_mismatches),
        'mcdc_hash_mismatch': len(mcdc_hash_mismatches),
        'base_active': len(base_active),
        'base_pending': len(base_pending),
        'mcdc_active': len(mcdc_active),
        'mcdc_pending': len(mcdc_pending),
        'has_analysis': bool(has_analysis),
        'has_mcdc_gaps': bool(has_mcdc_gaps),
        'has_executed_tests': bool(has_executed_tests),
        'has_coverage': bool(has_coverage),
        'build_status': build_status_value,
        'build_status_details': build_status_details,
        'build_status_updated': build_status_updated,
    }


def _format_tests_status(*, generated: bool, pending: int, hash_mismatch: int) -> str:
    if not generated:
        return "NOT GENERATED"
    parts: list[str] = ["GENERATED"]
    if hash_mismatch > 0:
        parts.append("CHANGED (re-approval required)")
    if pending > 0:
        parts.append("UNAPPROVED")
    return parts[0] + (" (" + ", ".join(parts[1:]) + ")" if len(parts) > 1 else "")


def _sil_mode_title(level: str) -> str:
    return f"VERISAFE – AI-ASSISTED SAFETY TESTING – {level.upper()} MODE"


def _normalize_scope_source_rel(selected_file: str) -> str:
    rel = str(selected_file or '').replace('\\', '/')
    rel = rel.strip().lstrip('/')
    if not rel:
        return ''
    return rel if rel.startswith('src/') else f"src/{rel}"


def _prompt_scope_selection(*, repo_path: Path, title: str) -> str | None:
    """Return a source_rel scope (e.g. 'src/logic/Foo.cpp') or None for repo-wide.

    Returns empty-string when cancelled.
    """

    heading = "Selection" if _DEMO_CLIENT_MODE else "Scope Selection"
    print(f"\n{Colors.BOLD}{heading} for {title}{Colors.ENDC}")
    print("1. Whole Repository")
    print("2. Single File")

    while True:
        try:
            pick = input("Enter choice (1-2) or 'q' to cancel: ").strip().lower()
            if pick in ('q', 'quit', 'back', 'b'):
                return ""
            if pick == '1':
                return None
            if pick == '2':
                f = select_target_file(repo_path)
                if not f:
                    return ""
                return _normalize_scope_source_rel(f)
            print_error("Invalid choice. Please enter 1 or 2.")
        except KeyboardInterrupt:
            return ""


def _run_sil_mode_menu(*, level: str, repo_path: Path, workspace: Path, args: Any, policy: Any, interactive_loop: bool) -> None:
    """Per-SIL interactive menu.

    Key principle: show only the actions required/available for the selected SIL.
    """

    level = (level or '').upper().strip()
    args.safety_level = level

    selected_model: str | None = None
    # SIL-mode scope is per "unit under test" (source_rel). If None, status is repo-wide.
    scope_source_rel: str | None = None

    # Entry UX: select scope once, then show status + actions.
    selected_scope = _prompt_scope_selection(repo_path=repo_path, title="Unit Under Test")
    if selected_scope == "":
        scope_source_rel = None
    else:
        scope_source_rel = selected_scope

    while True:
        scope_set = {scope_source_rel} if scope_source_rel else None
        state = _compute_demo_state(repo_path, source_scope=scope_set)

        base_generated = state['base_active'] > 0
        mcdc_generated = state['mcdc_active'] > 0

        approvals_block = (state['active_pending'] > 0) or (state['hash_mismatch'] > 0)
        has_any_tests = state['active_total'] > 0
        build_status_value = state.get('build_status', 'NOT_RUN')
        build_ready = build_status_value == 'SUCCESS'
        can_execute = (not approvals_block) and has_any_tests and build_ready

        test_reports_dir = repo_path / "tests" / "test_reports"
        analysis_dir = repo_path / "tests" / "analysis"
        coverage_dir = repo_path / "tests" / "coverage_report"

        print(f"\n{Colors.BOLD}{'='*70}")
        print(f"   {_sil_mode_title(level)}")
        print(f"{'='*70}{Colors.ENDC}\n")

        print("Status:")
        scope_label = scope_source_rel if scope_source_rel else "REPO (all sources)"
        print(f"- Scope: {scope_label}")

        # Code analysis
        code_analysis_done = (analysis_dir / "analysis.xlsx").exists() or (analysis_dir / "analysis.json").exists()
        print(f"- Code analysis: {'DONE' if code_analysis_done else 'NOT DONE'}")

        print(f"- Base tests: {_format_tests_status(generated=base_generated, pending=state['base_pending'], hash_mismatch=state['base_hash_mismatch'] if base_generated else 0)}")

        # Base tests Execution
        base_tests_executed = test_reports_dir.exists()
        print(f"- Base tests Execution: {'COMPLETED' if base_tests_executed else 'NOT RUN'}")

        # Coverage
        coverage_available = coverage_dir.exists()
        print(f"- Coverage: {'AVAILABLE' if coverage_available else 'NOT AVAILABLE'}")

        build_line = {
            'SUCCESS': 'COMPLETED',
            'FAILED': 'FAILED',
        }.get(build_status_value, 'NOT RUN')
        print(f"- Instrumented build: {build_line}")
        if build_status_value == 'FAILED':
            detail = state.get('build_status_details') or ''
            if detail:
                snippet = detail.replace('\r', ' ').replace('\n', ' | ')
                if len(snippet) > 160:
                    snippet = snippet[-160:]
                print(f"  Last error: {snippet}")

        print(f"- MC/DC tests: {_format_tests_status(generated=mcdc_generated, pending=state['mcdc_pending'], hash_mismatch=state['mcdc_hash_mismatch'] if mcdc_generated else 0)}")

        # MC/DC analysis
        mcdc_analysis_done = (analysis_dir / "mcdc_gaps.xlsx").exists() or (analysis_dir / "mcdc_gaps.json").exists()
        print(f"- MC/DC analysis: {'DONE' if mcdc_analysis_done else 'NOT DONE'}")

        # MC/DC Test execution
        mcdc_tests_executed = mcdc_generated and coverage_available
        print(f"- MC/DC Test execution: {'COMPLETED' if mcdc_tests_executed else 'NOT RUN'}")

        print("\nSelect action:")

        visible_choices: set[str] = set()
        print("S. Change unit under test (scope)")
        visible_choices.add('s')
        if scope_source_rel:
            print("R. Reset scope to Whole Repository")
            visible_choices.add('r')

        # Actions: show only those relevant for the selected SIL.
        show_base_generation = level != 'SIL4'
        show_mcdc_analysis = level in ('SIL3', 'SIL4')
        show_mcdc_generation = level == 'SIL3'

        gen_block_reason = None
        if not show_base_generation:
            gen_block_reason = 'blocked – SIL4 is analysis-driven (no auto test generation)'

        print("0. Static analysis")
        visible_choices.add('0')

        if show_base_generation:
            print(f"1. Generate base tests{(' (' + gen_block_reason + ')') if gen_block_reason else ''}")
            visible_choices.add('1')

        # Build-only action: run instrumentation/build but do not execute tests
        print("B. Build instrumented only")
        visible_choices.add('b')

        print("2. Review & approve test sections")
        visible_choices.add('2')

        exec_reason = None
        # Only block execution when a prior build explicitly failed, no tests exist,
        # or approvals are required. Do not block simply because a build hasn't run yet;
        # choosing Execute should trigger the build/run pipeline.
        if build_status_value == 'FAILED' and getattr(args, 'enforce_build_success', False):
            exec_reason = 'blocked – build failed'
        elif not has_any_tests:
            exec_reason = 'blocked – no tests generated'
        elif approvals_block:
            exec_reason = 'blocked – approval required'
            if state['hash_mismatch'] > 0:
                exec_reason = 'blocked – section content changed (re-approval required)'
        print(f"3. Execute all approved tests{(' (' + exec_reason + ')') if exec_reason else ''}")
        visible_choices.add('3')

        cov_reason = None
        if build_status_value != 'SUCCESS' and getattr(args, 'enforce_build_success', False):
            cov_reason = 'blocked – build not successful'
        elif not state['has_executed_tests']:
            cov_reason = 'blocked – execution required'
        print(f"4. Coverage analysis{(' (' + cov_reason + ')') if cov_reason else ''}")
        visible_choices.add('4')

        if show_mcdc_analysis:
            print("5. MC/DC gap analysis (analysis only)")
            visible_choices.add('5')

        mcdc_gen_reason = None
        if show_mcdc_generation:
            if state['base_active'] == 0:
                mcdc_gen_reason = 'blocked – base tests not generated'
            elif state['base_pending'] > 0 or state['base_hash_mismatch'] > 0:
                mcdc_gen_reason = 'blocked – base tests must be approved first'
            elif not state['has_mcdc_gaps']:
                mcdc_gen_reason = 'blocked – run MC/DC gap analysis first'
            print(f"6. Generate MC/DC tests{(' (' + mcdc_gen_reason + ')') if mcdc_gen_reason else ''}")
            visible_choices.add('6')

        print("7. View reports")
        visible_choices.add('7')
        print("Q. Back")
        visible_choices.add('q')

        prompt_nums = [n for n in ['0', '1', '2', '3', '4', '5', '6', '7'] if n in visible_choices]
        prompt_letters = ['S'] + (['R'] if 'r' in visible_choices else []) + ['Q']
        prompt = f"Enter choice ({', '.join(prompt_nums + prompt_letters)}): "
        choice = input(prompt).strip().lower()
        if choice not in visible_choices:
            print_error("Invalid choice.")
            continue
        if choice == 'q':
            return

        if choice == '0':
            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=0,
                to_phase=1,
                selected_model=None,
                interactive_loop=interactive_loop,
            )
            if not ok:
                continue
            continue

        if choice == 'r':
            if not scope_source_rel:
                print_error("Scope is already repo-wide.")
                continue
            scope_source_rel = None
            print_info("Scope reset: Whole Repository")
            continue

        if choice == 's':
            selected_scope = _prompt_scope_selection(repo_path=repo_path, title="Unit Under Test")
            if selected_scope == "":
                print_info("Selection cancelled.")
                continue
            scope_source_rel = selected_scope
            continue

        if choice == '7':
            _view_reports(repo_path=repo_path)
            continue

        if choice == '2':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                _interactive_approve_pending_sections_v2(
                    repo_path=repo_path,
                    workspace=workspace,
                    interactive_loop=interactive_loop,
                    source_scope_rel=scope_source_rel,
                )
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue

        if choice == '1':
            if gen_block_reason:
                print_error(f"Action blocked: {gen_block_reason}.")
                continue

            if not state['has_analysis']:
                print_error("Static analysis not found.")
                print_info("Run '0. Static analysis' first (kept separate from test generation).")
                continue

            if selected_model is None:
                selected_model = _prompt_model_choice()

            # Reuse current scope. The user can change scope explicitly via 'S'.
            print_info(f"Using current scope: {scope_source_rel if scope_source_rel else 'REPO (all sources)'}")

            generation_file = None
            if scope_source_rel:
                # Generator expects file relative to source-dir (src/), not including the leading 'src/'.
                generation_file = scope_source_rel[len('src/') :] if scope_source_rel.startswith('src/') else scope_source_rel

            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=2,
                to_phase=2,
                selected_model=selected_model,
                interactive_loop=interactive_loop,
                generation_source_dir='src',
                generation_file=generation_file,
            )
            if not ok:
                continue
            continue

        if choice == 'b':
            # Build instrumented only (no test execution)
            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=4,
                to_phase=4,
                selected_model=selected_model,
                interactive_loop=interactive_loop,
                execution_source_scope_rel=scope_source_rel,
                run_tests=False,
            )
            if not ok:
                continue
            continue

        if choice == '3':
            if exec_reason:
                print_error(f"Action blocked: {exec_reason}.")
                print_info("Use 'Review & approve test sections' to unblock execution.")
                continue

            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=4,
                to_phase=4,
                selected_model=None,
                interactive_loop=interactive_loop,
                execution_source_scope_rel=scope_source_rel,
            )
            if not ok:
                continue
            continue

        if choice == '4':
            if cov_reason:
                print_error(f"Action blocked: {cov_reason}.")
                continue

            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=5,
                to_phase=5,
                selected_model=None,
                interactive_loop=interactive_loop,
                execution_source_scope_rel=scope_source_rel,
            )
            if not ok:
                continue
            continue

        if choice == '5':
            # Always allowed: analysis-only.
            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=6,
                to_phase=6,
                selected_model=None,
                interactive_loop=interactive_loop,
            )
            if not ok:
                continue
            continue

        if choice == '6':
            if mcdc_gen_reason:
                print_error(f"Action blocked: {mcdc_gen_reason}.")
                continue

            if selected_model is None:
                selected_model = _prompt_model_choice()

            # Prefer scoped unit-under-test; otherwise prompt.
            target_file = scope_source_rel
            if not target_file:
                target_file = select_scope_and_file(repo_path, "Unit Under Test Selection")
                if target_file == "":
                    print_info("Selection cancelled.")
                    continue
                scope_source_rel = _normalize_scope_source_rel(target_file)

            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=7,
                to_phase=7,
                selected_model=selected_model,
                interactive_loop=interactive_loop,
            )
            if not ok:
                continue

            print_info("MC/DC tests generated. Review & approve sections before execution.")
            continue

        print_error("Invalid choice.")


def _interactive_approve_pending_sections_v2(*, repo_path: Path, workspace: Path, interactive_loop: bool, source_scope_rel: str | None = None) -> bool:
    """Interactive v2 approvals UI.

    Returns True when no pending active sections remain (unblocked), False otherwise.
    """

    print_phase(3, "REVIEW & APPROVE PENDING SECTIONS (v2)")
    approvals_path = ensure_approvals_registry(repo_path)
    if not approvals_path.exists():
        print_error("No approval data found. Run generation first.")
        return False

    header_re = re.compile(r"(?ms)^/\*\s*(?:AI-TEST-SECTION|AI-TESTGEN-SECTION)\s*\n(?P<body>.*?)\*/\s*\n")

    def _normalize_newlines(text: str) -> str:
        return (text or '').replace('\r\n', '\n').replace('\r', '\n')

    def _canonical_section_body(text: str) -> str:
        normalized = _normalize_newlines(text)
        stripped = normalized.rstrip()
        if not stripped:
            return ''
        return stripped + '\n'

    def _sha256_text(text: str) -> str:
        return hashlib.sha256(_normalize_newlines(text).encode('utf-8')).hexdigest()

    def _section_hashes_for_file(test_file_rel: str) -> set[str]:
        try:
            text = (repo_path / test_file_rel).read_text(encoding='utf-8')
        except Exception:
            return set()
        text = _normalize_newlines(text)
        matches = list(header_re.finditer(text))
        hashes: set[str] = set()
        for idx, m in enumerate(matches):
            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            hashes.add(_sha256_text(_canonical_section_body(text[body_start:body_end])))
        return hashes

    def _try_repair_unapproved_section_hash(*, entry: dict[str, Any]) -> bool:
        """Best-effort repair for sections whose body hash no longer matches the registry.

        This happens if someone edits the test file (even trivial formatting), which changes the
        section body hash. To keep the approvals UX stable, we re-key the registry entry to the
        current on-disk hash when we can do so unambiguously.

        IMPORTANT SAFETY BEHAVIOR:
        - If the section was previously approved, we clear approval metadata and require
          re-approval (approved -> False) after re-keying.
        """

        if not isinstance(entry, dict):
            return False

        kind = str(entry.get('kind') or 'base').lower().strip()
        section_label_map = {
            'base': 'BASE_TESTS',
            'mcdc': 'MCDC_TESTS',
            'boundary': 'BOUNDARY_TESTS',
            'error_path': 'ERROR_PATH_TESTS',
        }
        requested_label = section_label_map.get(kind)
        requested_name = str(entry.get('name') or '').strip()
        if not requested_label and not requested_name:
            return False

        test_file_rel = entry.get('test_file_rel')
        if not isinstance(test_file_rel, str) or not test_file_rel:
            return False
        test_path = repo_path / test_file_rel
        if not test_path.exists():
            return False

        try:
            text = _normalize_newlines(test_path.read_text(encoding='utf-8'))
        except Exception:
            return False

        matches = list(header_re.finditer(text))
        if not matches:
            return False

        candidates: list[str] = []
        for idx, m in enumerate(matches):
            meta_block = m.group('body')
            meta: dict[str, str] = {}
            for line in _normalize_newlines(meta_block).split('\n'):
                line = line.strip()
                if not line or ':' not in line:
                    continue
                k, v = line.split(':', 1)
                meta[k.strip()] = v.strip()

            label = str(meta.get('Section') or '').strip()
            if requested_name:
                if label != requested_name:
                    continue
            else:
                if label.upper() != requested_label:
                    continue

            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body_text = _canonical_section_body(text[body_start:body_end])
            candidates.append(_sha256_text(body_text))

        # Only auto-repair when unambiguous.
        # Auto-repair when unambiguous.
        #
        # If multiple sections share the same label in a single test file (e.g., multiple
        # MCDC_TESTS sections), use the registry name ordinal (mcdc_1, mcdc_2, ...) to
        # deterministically select the Nth candidate in file order.
        new_sha: str | None = None
        if len(candidates) == 1:
            new_sha = candidates[0]
        elif len(candidates) > 1:
            name = str(entry.get('name') or '').strip().lower()
            m = re.fullmatch(r"[a-z_]+_(\d+)", name)
            if m:
                try:
                    ordinal = int(m.group(1))
                except Exception:
                    ordinal = 0
                if 1 <= ordinal <= len(candidates):
                    new_sha = candidates[ordinal - 1]

        if not new_sha:
            return False
        old_sha = str(entry.get('section_sha256') or '').strip()
        if not old_sha or new_sha == old_sha:
            return False

        try:
            reg = json.loads(approvals_path.read_text(encoding='utf-8'))
        except Exception:
            return False

        sections_dict = (reg or {}).get('sections', {})
        if not isinstance(sections_dict, dict):
            return False

        existing = sections_dict.get(old_sha)
        if not isinstance(existing, dict):
            return False

        sections_dict.pop(old_sha, None)
        updated = dict(existing)
        updated['section_sha256'] = new_sha

        # If this section was approved before, the content changed; require re-approval.
        if updated.get('approved') is True:
            updated['approved'] = False
            updated.pop('reviewed_by', None)
            updated.pop('reviewed_at', None)

        sections_dict[new_sha] = updated

        try:
            approvals_path.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding='utf-8', newline='\n')
        except Exception:
            return False

        return True

    def _fmt(s: dict[str, Any]) -> str:
        reason = s.get('_pending_reason')
        suffix = ""
        if reason == 'CHANGED_SINCE_APPROVAL':
            suffix = "  [changed since approval]"
        elif reason:
            suffix = f"  [{reason}]"
        return f"{s.get('name','')}  {s.get('test_file_rel','')}{suffix}"

    scope_norm = (str(source_scope_rel).replace('\\', '/').strip() if source_scope_rel else None)

    while True:
        try:
            reg = json.loads(approvals_path.read_text(encoding='utf-8'))
        except Exception as e:
            print_error(f"Could not read approvals registry: {e}")
            return False

        sections = (reg or {}).get('sections', {})
        if not isinstance(sections, dict) or not sections:
            print_info("No sections tracked yet.")
            return True

        hashes_cache: dict[str, set[str]] = {}

        active_pending: list[dict[str, Any]] = []
        for _, s in sections.items():
            if not isinstance(s, dict) or s.get('active') is not True:
                continue

            if scope_norm is not None:
                if str(s.get('source_rel') or '').replace('\\', '/') != scope_norm:
                    continue

            if s.get('approved') is not True:
                active_pending.append(s)
                continue

            test_file_rel = s.get('test_file_rel')
            section_sha = s.get('section_sha256')
            if not isinstance(test_file_rel, str) or not isinstance(section_sha, str):
                continue
            if test_file_rel not in hashes_cache:
                hashes_cache[test_file_rel] = _section_hashes_for_file(test_file_rel)
            if section_sha not in hashes_cache.get(test_file_rel, set()):
                flagged = dict(s)
                flagged['_pending_reason'] = 'CHANGED_SINCE_APPROVAL'
                active_pending.append(flagged)

        if not active_pending:
            print_success("No pending active sections. You're unblocked.")
            # Immediately scaffold the deterministic .verisafe harness so the
            # next pipeline stages (configure/build) run against the overlay.
            # Fail loudly if a required dependency (e.g., vendored GoogleTest)
            # is missing to keep the safety workflow deterministic.
            try:
                ensure_harness_layout(repo_path)
                print_info("Harness scaffolded under .verisafe/ (ready for build).")
            except Exception as e:
                print_error(f"Harness preparation failed: {e}")
                return False
            if interactive_loop:
                back = input("Enter 'q' to return to the main menu: ").strip().lower()
                if back in ('q', 'quit', 'exit', ''):
                    return True
            return True

        print_info(f"Pending approvals (active): {len(active_pending)}")
        for idx, s in enumerate(sorted(active_pending, key=lambda x: (x.get('test_file_rel',''), x.get('name',''))), start=1):
            print(f"{idx:2d}. {_fmt(s)}")

        proceed = input("Approve now? (y/n, 'q' to return): ").strip().lower()
        if proceed in ('q', 'quit', 'exit', 'n', 'no'):
            return True

        reviewer = input("Reviewed-by (default: current user): ").strip() or (getpass.getuser() or 'reviewer')
        print_info("Approve: enter numbers (e.g. 1 or 1,3) | 'a' approve all | 'q' back")
        choice = input("Selection: ").strip().lower()
        if choice in ('q', 'quit', 'exit'):
            return True

        to_approve: list[dict[str, Any]] = []
        if choice in ('a', 'all'):
            to_approve = active_pending
        else:
            parts = [p.strip() for p in choice.split(',') if p.strip()]
            idxs: list[int] = []
            for part in parts:
                if not part.isdigit():
                    continue
                idxs.append(int(part))
            for i in idxs:
                if 1 <= i <= len(active_pending):
                    to_approve.append(active_pending[i - 1])

        if not to_approve:
            print_error("Nothing selected.")
            if interactive_loop:
                continue
            return False

        for s in to_approve:
            test_file_rel = s.get('test_file_rel')
            section_name = s.get('name')
            if not test_file_rel or not section_name:
                continue
            kind = str(s.get('kind') or 'base').lower().strip()
            section_label_map = {
                'base': 'BASE_TESTS',
                'mcdc': 'MCDC_TESTS',
                'boundary': 'BOUNDARY_TESTS',
                'error_path': 'ERROR_PATH_TESTS',
            }
            section_label = section_label_map.get(kind)
            section_name = str(s.get('name') or '').strip()
            # Do NOT replace the canonical section label with the internal
            # `name` (e.g. FUNC_evaluate). The approvals CLI expects one of
            # the standard labels (BASE_TESTS, MCDC_TESTS, ...). Use the
            # internal name only for logging/registry purposes.
            if not section_label:
                print_error(f"Cannot approve unknown section kind: {kind!r}")
                continue

            # If the test file changed after generation, re-key the unapproved registry entry
            # before running the approve command to avoid a confusing fail-then-succeed flow.
            _try_repair_unapproved_section_hash(entry=s)
            approve_cmd = [
                sys.executable, '-m', 'ai_c_test_generator.cli', 'approve',
                '--repo-path', str(repo_path),
                '--file', str(test_file_rel),
                '--section', str(section_label),
                '--reviewed-by', reviewer,
            ]
            ok, out = run_command(approve_cmd, cwd=workspace / 'CW_Test_Gen', description=f"Approving {str(section_name)}")
            if not ok:
                # Last-chance retry in case the repair needed to happen after reading latest output.
                if isinstance(out, str) and 'No matching sections found' in out:
                    if _try_repair_unapproved_section_hash(entry=s):
                        ok, _ = run_command(approve_cmd, cwd=workspace / 'CW_Test_Gen', description=f"Approving {str(section_name)}")
                if not ok:
                    print_error("Approval failed.")

        print_success("Approval step complete.")
        if interactive_loop:
            again = input("Press Enter to refresh status, or 'q' to return to the main menu: ").strip().lower()
            if again in ('q', 'quit', 'exit'):
                return True
        else:
            return True


def find_generated_test_files(repo_path: Path) -> list[Path]:
    """Find generated test_*.cpp files under <repo>/tests (excluding build/review)."""
    tests_root = repo_path / "tests"
    if not tests_root.exists():
        return []

    candidates = []
    for path in tests_root.rglob("test_*.cpp"):
        parts = set(path.parts)
        if "build" in parts or "CMakeFiles" in parts or "review" in parts:
            continue
        candidates.append(path)
    return sorted(set(candidates), key=lambda p: str(p).lower())


def find_compilable_test_files(repo_path: Path) -> list[Path]:
    """Mirror runner logic: only tests with *_compiles_yes.txt reports are considered compilable."""
    verification_dir = repo_path / "tests" / "compilation_report"
    tests_dir = repo_path / "tests"
    if not verification_dir.exists() or not tests_dir.exists():
        return []

    found: list[Path] = []
    for report_file in verification_dir.rglob("*compiles_yes.txt"):
        rel_report = report_file.relative_to(verification_dir)
        base_name = report_file.name.replace("_compiles_yes.txt", "")
        for ext in [".cpp", ".cc", ".cxx", ".c++", ".c"]:
            candidate = tests_dir / rel_report.parent / f"{base_name}{ext}"
            if candidate.exists():
                found.append(candidate)
                break
    return sorted(set(found), key=lambda p: str(p).lower())


def is_test_approved(repo_path: Path, test_path: Path) -> bool:
    review_dir = repo_path / "tests" / "review"

    def _approval_flag_candidates() -> list[Path]:
        try:
            rel = test_path.relative_to(repo_path)
        except Exception:
            rel = Path(test_path.name)

        rel_no_tests = rel
        if rel_no_tests.parts[:1] == ("tests",):
            rel_no_tests = Path(*rel_no_tests.parts[1:])

        preferred = review_dir / rel_no_tests.parent / f"{rel_no_tests.name}.flag"
        compat_mirrored = review_dir / rel.parent / f"{rel.name}.flag"
        legacy = review_dir / f"APPROVED.{test_path.name}.flag"
        return [preferred, compat_mirrored, legacy]

    content = ""
    found = False
    for approved_path in _approval_flag_candidates():
        try:
            content = approved_path.read_text(encoding="utf-8").replace("\r\n", "\n")
            found = True
            break
        except Exception:
            continue
    if not found:
        return False

    # Match the same rules as enforce_manual_review_gate(): must be approved=true and non-placeholder reviewer/date.
    text = (content or "").replace("\r\n", "\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 3:
        return False
    if lines[0].lower() != "approved = true":
        return False
    if not lines[1].lower().startswith("reviewed_by ="):
        return False
    if not lines[2].lower().startswith("date ="):
        return False
    reviewed_by = lines[1].split("=", 1)[1].strip() if "=" in lines[1] else ""
    date_val = lines[2].split("=", 1)[1].strip() if "=" in lines[2] else ""
    if reviewed_by in ("", "<human_name>"):
        return False
    if date_val in ("", "<ISO date>"):
        return False
    try:
        datetime.date.fromisoformat(date_val)
        return True
    except Exception:
        try:
            datetime.datetime.fromisoformat(date_val)
            return True
        except Exception:
            return False


def has_coverage_data(repo_path: Path) -> bool:
    build_dir = repo_path / "tests" / "build"
    if not build_dir.exists():
        return False
    # Require at least one gcda file (produced after executing instrumented binaries).
    return any(build_dir.rglob("*.gcda"))


def list_tests_grouped(repo_path: Path, test_files: list[Path]) -> None:
    from collections import defaultdict
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for p in test_files:
        try:
            rel = p.relative_to(repo_path)
        except Exception:
            rel = p
        by_dir[str(rel.parent).replace("\\", "/")].append(p)

    for dir_key in sorted(by_dir.keys(), key=lambda s: s.lower()):
        print(f"\n📁 {dir_key}/")
        for p in sorted(by_dir[dir_key], key=lambda x: x.name.lower()):
            try:
                rel = p.relative_to(repo_path)
            except Exception:
                rel = p
            print(f"  - {str(rel).replace('\\\\', '/')} ")


def run_review_phase(repo_path: Path) -> bool:
    """Interactive review phase: show status + approve selected tests."""
    return run_review_and_approve_phase(repo_path)


def _parse_review_required_tests(repo_path: Path) -> list[Path]:
    """Parse tests/review/review_required.md into absolute test file paths.

    Normalizes entries so bare filenames resolve under <repo>/tests/.
    """
    review_required = repo_path / "tests" / "review" / "review_required.md"
    if not review_required.exists():
        return []
    try:
        text = review_required.read_text(encoding="utf-8").replace("\r\n", "\n")
    except Exception:
        return []

    lines = text.split("\n")
    in_section = False
    generated: list[Path] = []
    for line in lines:
        if line.strip() == "## Generated test files":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        item = stripped.lstrip("-").strip()
        if not item or item == "(none)":
            continue
        item = item.replace("\\", "/")

        candidate = Path(item)
        if candidate.is_absolute():
            resolved = candidate
        else:
            resolved = repo_path / candidate
            if not resolved.exists():
                alt = repo_path / "tests" / candidate
                if alt.exists():
                    resolved = alt
        generated.append(resolved)
    return generated


def run_review_and_approve_phase(repo_path: Path) -> bool:
    """Numbered review+approval phase.

    Shows which tests are approved vs pending, and allows approving specific tests.
    Returns True only when ALL generated tests are approved.
    """
    review_dir = repo_path / "tests" / "review"
    tests = _parse_review_required_tests(repo_path)
    if not tests:
        print_error("No review_required.md found or it contains no generated tests. Run generation first.")
        return False

    print_phase("3", "REVIEW & APPROVE TEST CASES")
    print("Open each test file, review it, then approve it here.")

    def _fmt_rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo_path)).replace("\\", "/")
        except Exception:
            return str(p).replace("\\", "/")

    def _status(p: Path) -> str:
        return "APPROVED" if is_test_approved(repo_path, p) else "PENDING"

    while True:
        pending = [p for p in tests if _status(p) == "PENDING"]
        approved = [p for p in tests if _status(p) == "APPROVED"]

        print(f"\n{Colors.BOLD}Generated tests:{Colors.ENDC} {len(tests)} total | {len(approved)} approved | {len(pending)} pending")
        for idx, test_path in enumerate(tests, start=1):
            print(f"{idx:2d}. [{_status(test_path)}] {_fmt_rel(test_path)}")

        if not pending:
            print_success("All generated tests are approved.")
            return True

        print("\nApprove: enter numbers (e.g. 1 or 1,3) | 'a' approve all pending | 'p' show pending only | 'q' quit")
        choice = input("Selection: ").strip().lower()

        if choice in ("q", "quit", "exit"):
            return False

        if choice in ("p", "pending"):
            print(f"\n{Colors.BOLD}Pending tests:{Colors.ENDC}")
            for p in pending:
                print(f"- {_fmt_rel(p)}")
            continue

        if choice in ("a", "all"):
            to_approve = pending
        else:
            parts = [p.strip() for p in choice.split(',') if p.strip()]
            if not parts:
                continue
            idxs: list[int] = []
            ok = True
            for part in parts:
                if not part.isdigit():
                    ok = False
                    break
                i = int(part)
                if i < 1 or i > len(tests):
                    ok = False
                    break
                idxs.append(i)
            if not ok:
                print_error("Invalid selection.")
                continue
            to_approve = [tests[i - 1] for i in idxs]

        # Filter out already-approved (idempotent).
        to_approve = [p for p in to_approve if _status(p) == "PENDING"]
        if not to_approve:
            print_info("Nothing new to approve.")
            continue

        reviewer = input("Reviewer name (used in approval flag): ").strip()
        if not reviewer:
            print_error("Reviewer name is required.")
            continue
        date_str = datetime.date.today().isoformat()

        for test_path in to_approve:
            if not test_path.exists():
                print_error(f"Missing test file: {_fmt_rel(test_path)}")
                continue
            try:
                rel = test_path.relative_to(repo_path)
            except Exception:
                rel = Path(test_path.name)

            rel_no_tests = rel
            if rel_no_tests.parts[:1] == ("tests",):
                rel_no_tests = Path(*rel_no_tests.parts[1:])

            approved_path = review_dir / rel_no_tests.parent / f"{rel_no_tests.name}.flag"
            content = f"approved = true\nreviewed_by = {reviewer}\ndate = {date_str}\n"
            try:
                approved_path.parent.mkdir(parents=True, exist_ok=True)
                approved_path.write_text(content, encoding="utf-8", newline="\n")
                print_success(f"Approved: {approved_path.relative_to(repo_path)}")
            except Exception as e:
                print_error(f"Failed to write approval flag: {approved_path} ({e})")


def ensure_harness_layout(repo_path: Path) -> Path:
    """Create deterministic .verisafe overlay, sync approved tests, and validate dependencies.

    Raises RuntimeError if required dependencies (e.g., GoogleTest) are missing.
    Returns the harness root Path on success.
    """
    repo_path = Path(repo_path)
    harness_root = repo_path / '.verisafe'
    build_dir = harness_root / 'build'
    extern_dir = harness_root / 'extern'
    generated_dir = harness_root / 'generated'

    for d in (harness_root, build_dir, extern_dir, generated_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Collect production sources by scanning src/ for compilable files.
    production_sources: list[str] = []
    src_root = repo_path / 'src'
    if src_root.exists():
        for candidate in sorted(src_root.rglob('*')):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in ('.c', '.cc', '.cpp', '.cxx'):
                continue
            try:
                rel = candidate.relative_to(repo_path)
            except Exception:
                rel = candidate
            production_sources.append(str(rel).replace('\\', '/'))

    if not production_sources:
        raise RuntimeError("No production sources detected. Run analysis (phase 1) before preparing the harness.")

    # Collect approved tests from tests/.approvals.json; fall back to generated test files.
    approvals_path = repo_path / 'tests' / '.approvals.json'
    test_rel_paths: list[str] = []
    if approvals_path.exists():
        try:
            data = json.loads(approvals_path.read_text(encoding='utf-8'))
            sections = data.get('sections', {}) if isinstance(data, dict) else {}
            for entry in sections.values():
                if not isinstance(entry, dict):
                    continue
                if entry.get('active') is not True or entry.get('approved') is not True:
                    continue
                tr = entry.get('test_file_rel')
                if not isinstance(tr, str) or not tr:
                    continue
                test_rel_paths.append(tr.replace('\\', '/').lstrip('./'))
        except Exception:
            test_rel_paths = []

    if not test_rel_paths:
        # fallback: pick up any generated test_*.cpp files
        generated = find_generated_test_files(repo_path)
        test_rel_paths = [str(p.relative_to(repo_path)).replace('\\', '/') for p in generated]

    # Sync approved/generated tests into harness generated/ directory
    if generated_dir.exists():
        try:
            shutil.rmtree(generated_dir)
        except Exception:
            pass
    generated_dir.mkdir(parents=True, exist_ok=True)

    harness_rel_paths: list[str] = []
    for rel in test_rel_paths:
        src = (repo_path / rel).resolve()
        if not src.exists():
            print_info(f"Approved test missing on disk; skipping: {rel}")
            continue
        rel_no_prefix = rel
        if rel_no_prefix.startswith('tests/'):
            rel_no_prefix = rel_no_prefix[len('tests/'):]
        dest = generated_dir / rel_no_prefix
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        harness_rel_paths.append(dest.relative_to(harness_root).as_posix())

    if not harness_rel_paths:
        placeholder = generated_dir / 'verisafe_placeholder_test.cpp'
        placeholder_content = (
            "#include <gtest/gtest.h>\n\n"
            "TEST(VerisafeHarness, Placeholder) {\n"
            "    GTEST_SKIP() << \"No approved VERISAFE tests available.\";\n"
            "}\n"
        )
        placeholder.write_text(placeholder_content, encoding='utf-8')
        harness_rel_paths.append(placeholder.relative_to(harness_root).as_posix())

    # Ensure GoogleTest is vendored into harness extern
    gtest_dir = extern_dir / 'googletest'
    repo_gtest = repo_path / 'extern' / 'googletest'
    # Prefer a googletest vendored inside the target repository itself.
    # Fallback: copy from this tool's bundled third_party/googletest to ensure
    # deterministic, self-contained harness layouts without cross-repo links.
    tool_third_party = Path(__file__).resolve().parent / 'tools' / 'third_party' / 'googletest'

    if not gtest_dir.exists():
        # 1) If the repo provides its own googletest, copy that (repo-local, reproducible).
        if repo_gtest.exists() and (repo_gtest / 'CMakeLists.txt').exists():
            shutil.copytree(repo_gtest, gtest_dir, dirs_exist_ok=True)
        # 2) Else if this tool bundles googletest, copy it into the harness.
        elif tool_third_party.exists() and (tool_third_party / 'CMakeLists.txt').exists():
            shutil.copytree(tool_third_party, gtest_dir, dirs_exist_ok=True)
        else:
            raise RuntimeError(
                "GoogleTest not found. Place vendored GoogleTest under .verisafe/extern/googletest, "
                "or add a copy to this tool's third_party/googletest so the harness can build against it."
            )
    elif not (gtest_dir / 'CMakeLists.txt').exists():
        raise RuntimeError(".verisafe/extern/googletest is missing CMakeLists.txt. Vendor full GoogleTest before running.")

    # Write deterministic CMakeLists.txt if missing
    cmake_path = harness_root / 'CMakeLists.txt'
    if not cmake_path.exists():
        cmake_lines = [
            'cmake_minimum_required(VERSION 3.20)',
            'project(verisafe_harness LANGUAGES CXX)',
            '',
            'set(CMAKE_CXX_STANDARD 17)',
            'set(CMAKE_CXX_STANDARD_REQUIRED ON)',
            '',
            '# REPO_ROOT must be supplied with -DREPO_ROOT=<path> when configuring CMake',
            'if(NOT DEFINED REPO_ROOT)',
            '  message(FATAL_ERROR "REPO_ROOT must be provided when configuring the verisafe harness")',
            'endif()',
            '',
            'file(GLOB_RECURSE PROD_SOURCES "${REPO_ROOT}/src/*.cpp")',
            'add_library(verisafe_under_test STATIC ${PROD_SOURCES})',
            'target_include_directories(verisafe_under_test PUBLIC "${REPO_ROOT}/include" "${REPO_ROOT}/src")',
            '',
            'if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/extern/googletest/CMakeLists.txt")',
            '  message(FATAL_ERROR "GoogleTest not found under .verisafe/extern/googletest. Vendor it before running the harness.")',
            'endif()',
            'add_subdirectory(extern/googletest)',
            '',
            'file(GLOB_RECURSE GENERATED_TESTS "${CMAKE_CURRENT_SOURCE_DIR}/generated/*.cpp")',
            'add_executable(verisafe_tests ${GENERATED_TESTS})',
            'target_link_libraries(verisafe_tests PRIVATE verisafe_under_test gtest gtest_main)',
            'target_include_directories(verisafe_tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/generated" "${REPO_ROOT}/include" "${REPO_ROOT}/src")',
            'enable_testing()',
            'add_test(NAME verisafe_tests COMMAND verisafe_tests)',
            '',
        ]
        cmake_path.write_text('\n'.join(cmake_lines), encoding='utf-8')

    # Write metadata
    metadata_path = harness_root / 'metadata.json'
    metadata = {
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'production_sources': production_sources,
        'tests': harness_rel_paths,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

    return harness_root


def _extract_failed_sources_from_build_output(output: str) -> list[Path]:
    """Extract absolute source file paths from build output.

    Returns unique Paths in order of appearance.
    """
    if not output:
        return []
    # Broadly match Windows absolute paths to .cpp/.cc/.cxx files
    regex = re.compile(r"([A-Za-z]:[\\/][^\s:]+?\.(?:cpp|cc|cxx))", re.IGNORECASE)
    matches = regex.findall(output)
    paths: list[Path] = []
    seen: set[str] = set()
    for m in matches:
        try:
            p = Path(m)
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(p)
        except Exception:
            continue
    return paths


def _fix_designated_initializers_in_file(src_path: Path) -> bool:
    """Rewrite designated initializers into ordered member assignments.

    The transform is intentionally strict: it only fires when every entry in the
    initializer uses `.field = value` and it preserves inline comments.
    """

    try:
        original = src_path.read_text(encoding='utf-8')
    except Exception:
        return False

    pattern = re.compile(
        r"(?P<indent>^[ \t]*)"
        r"(?P<type>[A-Za-z0-9_:<>\[\]\s\*&]+?)"
        r"\s+"
        r"(?P<var>[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*=\s*\{"
        r"(?P<body>[^{}]*?)"
        r"\}\s*;"  # closing brace + semicolon
        r"\s*(?P<trailing>//[^\n]*)?",
        re.MULTILINE | re.DOTALL,
    )

    def _clean_expr(expr: str) -> tuple[str, str]:
        comment = ''
        if '//' in expr:
            expr_part, comment_part = expr.split('//', 1)
            expr = expr_part
            comment = ' //' + comment_part.strip()
        value = expr.rstrip().rstrip(',').strip()
        return value, comment

    modified = False
    pieces: list[str] = []
    last_idx = 0

    for match in pattern.finditer(original):
        body = match.group('body') or ''
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assignments: list[str] = []
        valid = bool(lines)

        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith('//'):
                # Keep stand-alone comments inside initializer untouched by aborting.
                valid = False
                break
            field_match = re.match(r"^\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<expr>.+)$", stripped)
            if not field_match:
                valid = False
                break
            value_expr, comment = _clean_expr(field_match.group('expr'))
            if not value_expr:
                valid = False
                break
            field_name = field_match.group('field')
            line = f"{match.group('indent')}{match.group('var')}.{field_name} = {value_expr};"
            if comment:
                line += comment
            assignments.append(line)

        if not valid or not assignments:
            continue

        init_line = f"{match.group('indent')}{match.group('type').rstrip()} {match.group('var')}{{}};"
        trailing = match.group('trailing') or ''
        if trailing:
            init_line += f" {trailing.strip()}"

        replacement = "\n".join([init_line] + assignments) + "\n"
        pieces.append(original[last_idx:match.start()])
        pieces.append(replacement)
        last_idx = match.end()
        modified = True

    if not modified:
        return False

    pieces.append(original[last_idx:])
    new_text = ''.join(pieces)
    try:
        src_path.write_text(new_text, encoding='utf-8')
    except Exception:
        return False
    return True


def _fix_interlocking_eval_in_file(src_path: Path) -> bool:
    """Deterministically replace incorrect qualified call to Interlocking::evaluate.

    This is a narrow, non-LLM edit applied only to generated/approved test files when
    the build indicates failing references to `Interlocking`.
    """
    try:
        txt = src_path.read_text(encoding='utf-8')
    except Exception:
        return False

    target = "::railway::logic::Interlocking::evaluate("
    if target not in txt:
        return False

    new_txt = txt.replace(target, "::railway::logic::evaluate(")
    if new_txt == txt:
        return False

    try:
        src_path.write_text(new_txt, encoding='utf-8')
    except Exception:
        return False
    return True


def _attempt_fix_it(repo_path: Path, build_output: str) -> bool:
    """Controller for the Fix-It stage.

    Currently limited to deterministic designated-initializer corrections. Returns
    True if any file was patched.
    """

    build_output = build_output or ''
    if 'designator order for field' not in build_output.lower():
        return False

    failed = _extract_failed_sources_from_build_output(build_output)
    if not failed:
        return False

    any_fixed = False
    for candidate in failed:
        try:
            path = candidate.resolve()
        except Exception:
            continue
        path_lower = str(path).lower()
        if '.verisafe' not in path_lower and 'generated' not in path_lower:
            continue
        if _fix_designated_initializers_in_file(path):
            print_info(f"Fix-It reordered designated initializer in: {path}")
            any_fixed = True
        # Also apply a narrow deterministic qualification fix for Interlocking::evaluate
        if _fix_interlocking_eval_in_file(path):
            print_info(f"Fix-It corrected Interlocking::evaluate qualification in: {path}")
            any_fixed = True
    return any_fixed

def find_testable_files(repo_path):
    """Find all C++ source files in the repository that are suitable for testing"""
    testable_files = []

    # Common C++ file extensions
    cpp_extensions = ['.cpp', '.cc', '.cxx', '.c++']

    # Walk through all directories in src/
    src_path = repo_path / 'src'
    if src_path.exists():
        for root, dirs, files in os.walk(src_path):
            for file in files:
                if any(file.endswith(ext) for ext in cpp_extensions):
                    # Get relative path from src/
                    rel_path = Path(root).relative_to(src_path) / file
                    testable_files.append(str(rel_path))

    # Sort files alphabetically by path
    testable_files.sort()
    return testable_files

def select_scope_and_file(repo_path, operation_name):
    """Interactive scope selection (whole repo vs file-wise) and file selection if needed"""
    heading = "Selection" if _DEMO_CLIENT_MODE else "Scope Selection"
    print(f"\n{Colors.BOLD}{heading} for {operation_name}{Colors.ENDC}")
    print("1. Whole Repository")
    print("2. Single File")

    while True:
        try:
            scope_choice = input("Enter choice (1-2) or 'q' to quit: ").strip()

            if scope_choice.lower() == 'q':
                return ""

            if scope_choice == '1':
                print_success("Selected: Whole Repository")
                return None  # None means whole repo
            elif scope_choice == '2':
                selected_file = select_target_file(repo_path)
                if selected_file in (None, ""):
                    return ""
                return selected_file
            else:
                print_error("Invalid choice. Please enter 1 or 2.")
        except KeyboardInterrupt:
            return ""

def select_target_file(repo_path):
    """Interactive file selection from available testable files"""
    testable_files = find_testable_files(repo_path)

    if not testable_files:
        return None

    print(f"\n{Colors.BOLD}Available Testable Files:{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")

    # Group files by directory for better organization
    from collections import defaultdict
    files_by_dir = defaultdict(list)

    for file_path in testable_files:
        dir_path = str(Path(file_path).parent)
        if dir_path == '.':
            dir_path = 'root'
        files_by_dir[dir_path].append(file_path)

    # Sort directories and display
    sorted_dirs = sorted(files_by_dir.keys())
    file_num = 1

    for dir_path in sorted_dirs:
        if dir_path != 'root':
            print(f"\n📁 {dir_path}/")
        else:
            print("\n📁 src/")
        for file_path in sorted(files_by_dir[dir_path]):
            print(f"{file_num:2d}. {file_path}")
            file_num += 1

    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")

    while True:
        try:
            choice = input(f"Select target file (1-{len(testable_files)}) or 'q' to quit: ").strip()

            if choice.lower() == 'q':
                return ""

            try:
                index = int(choice) - 1
                if 0 <= index < len(testable_files):
                    selected_file = testable_files[index]
                    print_success(f"Selected: {selected_file}")
                    return selected_file
                else:
                    print_error(f"Invalid choice. Please enter 1-{len(testable_files)}.")
            except ValueError:
                print_error("Please enter a valid number.")

        except KeyboardInterrupt:
            return ""

def main():
    # Professional behavior: prefer venv, but don't hard-fail.
    global _DEMO_CLIENT_MODE
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    if not in_venv:
        print_info("VERISAFE is running without its bundled environment.")
        print_info("Recommended: run 'install.bat' (once) then 'run_demo.bat' from the VERISAFE demo folder.")
    
    parser = argparse.ArgumentParser(description="Master AI Unit Test Generation Demo")
    parser.add_argument('--repo-path', required=True, help='Target repository path (required)')
    parser.add_argument('--skip-analysis', action='store_true', help='Skip analysis if already done')
    parser.add_argument('--skip-generation', action='store_true', help='Skip test generation if already done')
    phase_group = parser.add_mutually_exclusive_group()
    phase_group.add_argument('--only-analysis', action='store_true', help='Run analysis only, then stop')
    phase_group.add_argument('--only-generation', action='store_true', help='Run test generation only, then stop')
    phase_group.add_argument('--only-run', action='store_true', help='Run build+tests only, then stop')
    parser.add_argument('--api-key', default=None, help='API key for generation (preferred: set GEMINI_API_KEY or GROQ_API_KEY)')
    parser.add_argument('--target-file', default='Interlocking.cpp', help='Target file for test generation')
    parser.add_argument('--source-dir', default='src/logic', help='Source directory containing target file')
    parser.add_argument(
        '--pipeline',
        choices=['legacy', 'incremental'],
        default='legacy',
        help='Pipeline mode: legacy (file-level approvals) or incremental (section-based approvals, v2)'
    )
    parser.add_argument(
        '--model',
        choices=['ollama', 'gemini', 'groq', 'github'],
        default='ollama',
        help='AI model to use for generation/planning when running non-interactively (default: ollama)'
    )
    parser.add_argument(
        '--from-phase',
        type=int,
        default=None,
        help='(incremental pipeline) Start from a given phase number (0-8).'
    )
    parser.add_argument(
        '--to-phase',
        type=int,
        default=None,
        help='(incremental pipeline) Stop after a given phase number (0-8).'
    )
    parser.add_argument(
        '--safety-level',
        choices=['QM', 'SIL0', 'SIL1', 'SIL2', 'SIL3', 'SIL4'],
        default='SIL0',
        help='Policy enforcement level (QM, SIL0..SIL4).'
    )
    parser.add_argument(
        '--policy-file',
        default=None,
        help='Optional path to safety policy YAML (defaults to repo/workspace safety_policy.yaml).'
    )
    parser.add_argument(
        '--disable-mcdc',
        action='store_true',
        help='Override: disable MC/DC analysis/generation even if policy enables it.'
    )
    parser.add_argument(
        '--no-instrument',
        action='store_true',
        help='Disable instrumentation build (produce normal binaries).'
    )
    parser.add_argument(
        '--use-harness',
        action='store_true',
        help='Use the .verisafe harness build instead of the repo CMake tree.'
    )
    parser.add_argument(
        '--enforce-build-success',
        action='store_true',
        help='When set, block execution/coverage if the last instrumented build failed (default: allow execution).'
    )
    parser.add_argument(
        '--engineering-menu',
        action='store_true',
        help='Show Advanced / Engineering Mode in the client demo menu.'
    )
    args = parser.parse_args()

    # Make instrumentation the default behavior unless explicitly disabled.
    if getattr(args, 'no_instrument', False):
        args.instrument = False
    else:
        # Default: instrumented builds enabled
        args.instrument = True

    if getattr(args, 'use_harness', False):
        args.instrument = True

    workspace = Path(__file__).parent.resolve()

    # Demo-bundle default: use the bundled safety policy unless the caller overrides.
    # This keeps packaged demos working even when the target repo has no policy file.
    if getattr(args, 'policy_file', None) is None:
        bundled_policy = workspace / 'safety_policy.yaml'
        if bundled_policy.exists():
            args.policy_file = str(bundled_policy)

    # Resolve repo path: support both absolute and workspace-relative inputs.
    repo_path_arg = Path(args.repo_path).expanduser()
    repo_path = (repo_path_arg if repo_path_arg.is_absolute() else (workspace / repo_path_arg)).resolve()

    # Quick non-interactive shortcuts: allow callers to request a single phase and exit.
    if getattr(args, 'only_analysis', False):
        ok = run_incremental_pipeline_v2(
            repo_path=repo_path,
            workspace=workspace,
            args=args,
            from_phase=0,
            to_phase=1,
            selected_model=args.model,
            interactive_loop=False,
        )
        sys.exit(0 if ok else 2)

    if getattr(args, 'only_generation', False):
        # Run generation phase only (phase 2). Requires analysis outputs to already exist or --skip-analysis.
        ok = run_incremental_pipeline_v2(
            repo_path=repo_path,
            workspace=workspace,
            args=args,
            from_phase=2,
            to_phase=2,
            selected_model=args.model,
            interactive_loop=False,
            generation_source_dir=args.source_dir,
            generation_file=(args.target_file if args.target_file else None),
        )
        sys.exit(0 if ok else 2)

    if getattr(args, 'only_run', False):
        ok = run_incremental_pipeline_v2(
            repo_path=repo_path,
            workspace=workspace,
            args=args,
            from_phase=4,
            to_phase=4,
            selected_model=args.model,
            interactive_loop=False,
        )
        sys.exit(0 if ok else 2)

    # Non-interactive incremental pipeline entrypoint.
    # If user passes --pipeline incremental, we run phases explicitly and stop (default 0-2).
    if args.pipeline == 'incremental' and (args.from_phase is not None or args.to_phase is not None):
        from_phase = int(args.from_phase) if args.from_phase is not None else 0
        to_phase = int(args.to_phase) if args.to_phase is not None else 2

        # Only treat --target-file as a generation filter if it was explicitly passed.
        explicit_target_file = any(
            a == '--target-file' or a.startswith('--target-file=')
            for a in sys.argv[1:]
        )
        ok = run_incremental_pipeline_v2(
            repo_path=repo_path,
            workspace=workspace,
            args=args,
            from_phase=from_phase,
            to_phase=to_phase,
            selected_model=None,
            interactive_loop=False,
            generation_source_dir=args.source_dir,
            generation_file=(args.target_file if explicit_target_file else None),
        )
        sys.exit(0 if ok else 2)
    
    interactive_loop = not (args.only_analysis or args.only_generation or args.only_run)
    # Demo-only state: remember the last MC/DC target selected from gap analysis.
    mcdc_target_source_rel: str | None = None
    while True:
        if not repo_path.exists():
            print_error(f"Repository not found: {repo_path}")
            return

        # Load safety policy once per menu iteration so we can gate options deterministically.
        policy = None
        try:
            SafetyPolicy = _get_safety_policy_class()
            policy = SafetyPolicy.load(
                safety_level=getattr(args, 'safety_level', 'QM'),
                repo_root=repo_path,
                policy_file=getattr(args, 'policy_file', None),
                disable_mcdc=bool(getattr(args, 'disable_mcdc', False)),
            )
        except Exception:
            policy = None

        # Client-facing demo menu (safety intent first).
        show_engineering_menu = bool(getattr(args, 'engineering_menu', False))
        _print_client_menu(
            safety_level=getattr(args, 'safety_level', 'SIL0'),
            show_engineering_menu=show_engineering_menu,
        )
        while True:
            prompt = "Enter choice (1-4, A, B, I, Q)"
            if show_engineering_menu:
                prompt = "Enter choice (1-4, A, B, I, L, Q)"
            client_choice = input(f"{prompt}: ").strip().lower()

            if client_choice == 'l' and not show_engineering_menu:
                print_info("Engineering Mode is disabled for client demos.")
                print_info("Re-run with --engineering-menu to enable it.")
                continue

            if client_choice in ('1', '2', '3', '4', 'a', 'b', 'q', 'i'):
                break

            if show_engineering_menu and client_choice == 'l':
                break

            if show_engineering_menu:
                print("Invalid choice. Please enter 1-4, A, B, L, or Q.")
            else:
                print("Invalid choice. Please enter 1-4, A, B, or Q.")

        if client_choice == 'q':
            return

        if client_choice == 'b':
            _view_reports(repo_path=repo_path)
            continue

        if client_choice == 'i':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                # Run instrumentation build (ensure generation exists first)
                args.instrument = True
                ok = run_incremental_pipeline_v2(
                    repo_path=repo_path,
                    workspace=workspace,
                    args=args,
                    from_phase=2,
                    to_phase=4,
                    selected_model=args.model,
                    interactive_loop=interactive_loop,
                    generation_source_dir=args.source_dir,
                    generation_file=(args.target_file if args.target_file else None),
                )
                if not ok:
                    print_error("Instrumentation/build failed.")
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue

        if client_choice == 'a':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                _interactive_approve_pending_sections_v2(repo_path=repo_path, workspace=workspace, interactive_loop=interactive_loop)
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue
        if client_choice == '1':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                _run_sil_mode_menu(level='SIL0', repo_path=repo_path, workspace=workspace, args=args, policy=policy, interactive_loop=interactive_loop)
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue
        if client_choice == '2':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                _run_sil_mode_menu(level='SIL2', repo_path=repo_path, workspace=workspace, args=args, policy=policy, interactive_loop=interactive_loop)
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue
        if client_choice == '3':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                _run_sil_mode_menu(level='SIL3', repo_path=repo_path, workspace=workspace, args=args, policy=policy, interactive_loop=interactive_loop)
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue
        if client_choice == '4':
            prev_mode = globals().get("_DEMO_CLIENT_MODE", True)
            globals()["_DEMO_CLIENT_MODE"] = True
            try:
                _run_sil_mode_menu(level='SIL4', repo_path=repo_path, workspace=workspace, args=args, policy=policy, interactive_loop=interactive_loop)
            finally:
                globals()["_DEMO_CLIENT_MODE"] = prev_mode
            continue

        # Advanced / Engineering mode: expose internal pipeline options.
        # (Falls through to the legacy menu logic below.)

        selected_operation = None
        selected_model = None
        ctest_regex: str | None = None
        also_run_coverage: bool = False
        prompt_for_coverage_after_execution: bool = False
        selected_tests: list[Path] | None = None
        selected_scope_label: str | None = None

        print(f"\n{Colors.BOLD}{'='*70}")
        print(f"   ADVANCED / ENGINEERING MODE (internal)")
        print(f"{'='*70}{Colors.ENDC}\n")

        print(f"Safety Level: {getattr(args, 'safety_level', 'QM')}")

        print("Select Operation:")
        print("1. Analyze (Static Analysis)")
        print("2. Generate BASE tests (Statement/Branch)")
        print("3. Review approvals (v2)")
        print("4. Build + Run regression (all approved)")
        print("9. Instrumentation build (produce instrumented binaries for coverage)")
        print("5. Coverage reports (baseline/current)")
        print("6. MC/DC gap analysis (no generation)")
        print("7. Generate MC/DC tests (append-only)")
        print("8. Run regression + coverage (all approved)")
        print("S. Set safety level")
        print("q. Back")

        while True:
            op_choice = input("Enter choice (1-8, S, q): ").strip().lower()
            if op_choice == 'q':
                break
            if op_choice in ('1', '2', '3', '4', '5', '6', '7', '8', 's'):
                break
            if op_choice == '9':
                break
            print("Invalid choice. Please enter 1-8, S, or q.")

        if op_choice == 'q':
            continue

        if op_choice == 's':
            allowed = None
            try:
                SafetyPolicy = _get_safety_policy_class()
                allowed = list(SafetyPolicy.allowed_levels())
            except Exception:
                allowed = ['QM', 'SIL0', 'SIL1', 'SIL2', 'SIL3', 'SIL4']

            print("\nSelect Safety Level:")
            for idx, level in enumerate(allowed, start=1):
                print(f"{idx}. {level}")
            pick = input(f"Enter choice (1-{len(allowed)}) or ENTER to keep current: ").strip()
            if pick == '':
                continue
            if not pick.isdigit() or not (1 <= int(pick) <= len(allowed)):
                print_error("Invalid selection.")
                continue
            args.safety_level = allowed[int(pick) - 1]
            print_success(f"Safety level set to: {args.safety_level}")
            continue

        if op_choice == '1':
            selected_operation = 'analyze_v2'
        elif op_choice == '2':
            selected_operation = 'gen_base_v2'
            if policy is not None and getattr(policy, 'base_tests', True) is not True:
                print_error(f"BASE test generation is disabled by safety policy: {getattr(args, 'safety_level', 'QM')}")
                print_info("This safety level is configured for analysis-only / human-driven tests.")
                continue
            selected_model = _prompt_model_choice()
        elif op_choice == '3':
            selected_operation = 'approve_v2'
        elif op_choice == '4':
            selected_operation = 'execution'
        elif op_choice == '5':
            selected_operation = 'coverage'
        elif op_choice == '6':
            selected_operation = 'mcdc_analyze_v2'
            if policy is not None and hasattr(policy, 'mcdc_analysis_enabled') and not policy.mcdc_analysis_enabled():
                print_error(f"MC/DC analysis is disabled by safety policy: {getattr(args, 'safety_level', 'QM')}")
                continue
        elif op_choice == '7':
            selected_operation = 'mcdc_generate_v2'
            if policy is not None and hasattr(policy, 'mcdc_generation_required') and not policy.mcdc_analysis_enabled():
                print_error(f"MC/DC generation is disabled by safety policy: {getattr(args, 'safety_level', 'QM')}")
                continue
            if policy is not None and hasattr(policy, 'mcdc_generation_required'):
                enabled = bool(getattr(policy, 'mcdc_generation', False))
                if not enabled:
                    print_error(f"MC/DC generation is disabled by safety policy: {getattr(args, 'safety_level', 'QM')}")
                    continue
            selected_model = _prompt_model_choice()
        elif op_choice == '8':
            selected_operation = 'run_and_coverage'
        elif op_choice == '9':
            selected_operation = 'instrument'

        # Professional milestones
        if selected_operation == 'analyze_v2':
            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=0,
                to_phase=1,
                selected_model=None,
                interactive_loop=interactive_loop,
            )
            if not ok and not interactive_loop:
                return
            if interactive_loop:
                continue
            return

        if selected_operation == 'gen_base_v2':
            analysis_json = repo_path / 'tests' / 'analysis' / 'analysis.json'
            analysis_xlsx = repo_path / 'tests' / 'analysis' / 'analysis.xlsx'
            if not (analysis_json.exists() or analysis_xlsx.exists()):
                print_error("Static analysis outputs not found.")
                print_info("Run '1. Analyze codebase' first (analysis is kept separate from test generation).")
                if interactive_loop:
                    continue
                return

            # Bring back classic behavior: ask scope (whole repo vs file-wise).
            selected_file = select_scope_and_file(repo_path, "Base Test Generation")
            if selected_file == "":
                if interactive_loop:
                    continue
                return

            # Scope mapping: generator expects source_dir and optional --file.
            scope_source_dir = 'src'
            scope_file = selected_file  # None => whole repo

            ok = run_incremental_pipeline_v2(
                repo_path=repo_path,
                workspace=workspace,
                args=args,
                from_phase=2,
                to_phase=2,
                selected_model=selected_model,
                interactive_loop=interactive_loop,
                generation_source_dir=scope_source_dir,
                generation_file=scope_file,
            )
            if not ok and not interactive_loop:
                return
            if interactive_loop:
                continue
            return

        if selected_operation == 'approve_v2':
            _interactive_approve_pending_sections_v2(repo_path=repo_path, workspace=workspace, interactive_loop=interactive_loop)
            if interactive_loop:
                continue
            return

        if selected_operation == 'mcdc_analyze_v2':
            print_phase(6, "MC/DC GAP ANALYSIS (v2)")
            ok, _ = run_command(
                [
                    sys.executable,
                    '-m',
                    'ai_c_test_analyzer.cli',
                    '--repo-path',
                    str(repo_path),
                    '--mcdc',
                    '--safety-level',
                    getattr(args, 'safety_level', 'QM'),
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    *( ['--disable-mcdc'] if getattr(args, 'disable_mcdc', False) else [] ),
                ],
                cwd=workspace / 'CW_Test_Analyzer',
                description='Generating MC/DC gap report'
            )
            if ok:
                gaps_path = repo_path / 'tests' / 'analysis' / 'mcdc_gaps.json'
                try:
                    data = json.loads(gaps_path.read_text(encoding='utf-8')) if gaps_path.exists() else {}
                    files = list((data or {}).get('files', {}).keys())

                    # Try to load analyzer summaries so we can label hardware-touching candidates.
                    hw_map: dict[str, bool] = {}
                    try:
                        analysis_json = repo_path / 'tests' / 'analysis' / 'analysis.json'
                        if analysis_json.exists():
                            a = json.loads(analysis_json.read_text(encoding='utf-8'))
                            summaries = a.get('file_summaries', {}) if isinstance(a, dict) else {}
                            if isinstance(summaries, dict):
                                for k, v in summaries.items():
                                    if not isinstance(v, dict):
                                        continue
                                    # analysis.json uses backslashes; normalize to posix for display/lookup.
                                    key = str(k).replace('\\', '/')
                                    hw_map[key] = bool(v.get('hardware_free') is True)
                    except Exception:
                        hw_map = {}

                    if not files:
                        print_info('No MC/DC candidate decisions found in repo sources.')
                    else:
                        print_info('MC/DC candidate files:')
                        for idx, f in enumerate(files, start=1):
                            hw_free = hw_map.get(f)
                            tag = "[LOGIC]" if hw_free is True else ("[HW/MIXED]" if hw_free is False else "")
                            print(f"{idx:2d}. {tag} {f}")
                        if interactive_loop:
                            pick = input("Select a file number to target for MC/DC generation (or ENTER to keep current target): ").strip()
                            if pick.isdigit():
                                i = int(pick)
                                if 1 <= i <= len(files):
                                    mcdc_target_source_rel = files[i - 1]
                                    if hw_map.get(mcdc_target_source_rel) is False:
                                        print_info("Note: this file is hardware-touching (MIXED). Demo can still work using host stubs/mocks.")
                                    print_success(f"MC/DC target set to: {mcdc_target_source_rel}")
                except Exception as e:
                    print_error(f"Could not summarize mcdc_gaps.json: {e}")
            if interactive_loop:
                continue
            return

        if selected_operation == 'mcdc_generate_v2':
            print_phase(7, "MC/DC TEST GENERATION (v2)")
            # Gate before any interactive prompts (scope/model) to avoid wasting API calls.
            approvals_path_v2 = repo_path / 'tests' / '.approvals.json'
            if approvals_path_v2.exists():
                try:
                    reg = json.loads(approvals_path_v2.read_text(encoding='utf-8'))
                    sections = (reg or {}).get('sections', {})
                    active = [s for s in (sections or {}).values() if isinstance(s, dict) and s.get('active') is True]
                    pending = [s for s in active if s.get('approved') is not True]
                    if pending:
                        print_error(f"Cannot generate MC/DC tests: {len(pending)} unapproved sections exist.")
                        print_info("Approve all sections first (option 3: Review approvals) to avoid wasting model rate limits.")
                        if interactive_loop:
                            continue
                        return
                except Exception as e:
                    print_error(f"Could not read approvals registry: {e}")
                    if interactive_loop:
                        continue
                    return

            # Offer whole-repo vs file-wise selection based on mcdc_gaps.json.
            gaps_path = repo_path / 'tests' / 'analysis' / 'mcdc_gaps.json'
            if not gaps_path.exists():
                print_error("mcdc_gaps.json not found.")
                print_info("Run MC/DC analysis first (Phase 6).")
                if interactive_loop:
                    continue
                return

            try:
                gaps = json.loads(gaps_path.read_text(encoding='utf-8'))
            except Exception as e:
                print_error(f"Failed to read mcdc_gaps.json: {e}")
                if interactive_loop:
                    continue
                return

            files_map = (gaps or {}).get('files', {})
            candidate_files = sorted([k for k, v in (files_map or {}).items() if isinstance(v, list) and len(v) > 0])
            if not candidate_files:
                print_info("No MC/DC candidate decisions found in mcdc_gaps.json.")
                if interactive_loop:
                    continue
                return

            default_target_rel = None
            if mcdc_target_source_rel and str(Path(mcdc_target_source_rel).as_posix()) in candidate_files:
                default_target_rel = str(Path(mcdc_target_source_rel).as_posix())

            print("\nSelect MC/DC generation scope:")
            print(f"1. Whole repo (all files with gaps: {len(candidate_files)})")
            print("2. File-wise (single)")
            print("3. File-wise (multiple)")

            scope = None
            while True:
                scope_choice = input("Enter choice (1-3) or 'b' to go back: ").strip().lower()
                if scope_choice in ('b', 'back', 'q', 'quit', 'exit'):
                    if interactive_loop:
                        continue
                    return
                if scope_choice in ('1', '2', '3'):
                    scope = scope_choice
                    break
                print("Invalid choice. Please enter 1-3.")

            def _print_candidates() -> None:
                print(f"\n{Colors.BOLD}MC/DC candidate files (from tests/analysis/mcdc_gaps.json):{Colors.ENDC}")
                for idx, rel in enumerate(candidate_files, start=1):
                    marker = " (default)" if default_target_rel and rel == default_target_rel else ""
                    print(f"{idx:2d}. {rel}{marker}")

            selected_sources: list[str] = []
            if scope == '1':
                selected_sources = candidate_files
            elif scope == '2':
                _print_candidates()
                prompt = "Select file number"
                if default_target_rel:
                    prompt += " (Enter for default)"
                prompt += ": "
                pick = input(prompt).strip()
                if pick == '' and default_target_rel:
                    selected_sources = [default_target_rel]
                elif pick.isdigit() and 1 <= int(pick) <= len(candidate_files):
                    selected_sources = [candidate_files[int(pick) - 1]]
                else:
                    print_error("Invalid selection.")
                    if interactive_loop:
                        continue
                    return
            elif scope == '3':
                _print_candidates()
                pick = input("Select files (e.g. 1,3) or 'a' for all: ").strip().lower()
                if pick in ('a', 'all', ''):
                    selected_sources = candidate_files
                else:
                    parts = [p.strip() for p in pick.split(',') if p.strip()]
                    picks: list[str] = []
                    ok_sel = True
                    for part in parts:
                        if not part.isdigit():
                            ok_sel = False
                            break
                        i = int(part)
                        if i < 1 or i > len(candidate_files):
                            ok_sel = False
                            break
                        picks.append(candidate_files[i - 1])
                    if not ok_sel or not picks:
                        print_error("Invalid selection.")
                        if interactive_loop:
                            continue
                        return
                    seen = set()
                    selected_sources = []
                    for rel in picks:
                        if rel in seen:
                            continue
                        seen.add(rel)
                        selected_sources.append(rel)

            print("\nSelect AI Model for MC/DC generation (optional):")
            print("1. Gemini (Cloud - requires API key)")
            print("2. Groq (Cloud - requires API key)")
            print("3. Local LLM (Local - no API key needed)")
            model = None
            while True:
                model_choice = input("Enter choice (1-3) or ENTER to skip: ").strip()
                if model_choice == '':
                    model = None
                    break
                if model_choice == '1':
                    model = 'gemini'
                    break
                if model_choice == '2':
                    model = 'groq'
                    break
                if model_choice == '3':
                    model = 'ollama'
                    break
                print("Invalid choice. Please enter 1-3 (or ENTER to skip).")

            if model is None:
                print_info("Skipping MC/DC generation.")
                if interactive_loop:
                    continue
                return

            _load_dotenv_if_present(workspace / '.env')
            any_appended = False
            for rel in selected_sources:
                p = Path(rel)
                src_dir = str(p.parent.as_posix())
                file_name = p.name

                gen_cmd = [
                    sys.executable, '-m', 'ai_c_test_generator.cli', 'mcdc-generate',
                    '--repo-path', str(repo_path),
                    '--source-dir', src_dir,
                    '--file', file_name,
                    '--output', str(repo_path / 'tests'),
                    '--model', model,
                    '--safety-level', getattr(args, 'safety_level', 'QM'),
                ]

                if getattr(args, 'policy_file', None):
                    gen_cmd.extend(['--policy-file', str(args.policy_file)])
                if getattr(args, 'disable_mcdc', False):
                    gen_cmd.append('--disable-mcdc')

                if model in ('gemini', 'groq'):
                    api_key = args.api_key or os.environ.get(f"{model.upper()}_API_KEY")
                    if not api_key:
                        print_error(f"Missing API key for model {model}.")
                        if interactive_loop:
                            continue
                        return
                    gen_cmd.extend(['--api-key', api_key])

                ok, out = run_command(
                    gen_cmd,
                    cwd=workspace / 'CW_Test_Gen',
                    description=f"Generating MC/DC tests for {rel}",
                    stream_output=True,
                )
                if not ok:
                    if interactive_loop:
                        continue
                    return

                any_appended = True

            if any_appended:
                print_info("MC/DC section(s) generated as UNAPPROVED. Run operation 3 to approve.")
            else:
                print_info("No MC/DC sections were appended (no MC/DC decisions for selected file(s)).")
            if interactive_loop:
                continue
            return

        if selected_operation == 'run_and_coverage':
            # Professional one-click: run regression then coverage.
            # We fall through to the existing execution handler; coverage is optional.
            selected_operation = 'execution'

        # Legacy menu removed: keep demo flow professional and focused.

        # Per-operation selection
        selected_file = None

        if selected_operation == 'generation':
            selected_file = select_scope_and_file(repo_path, "Generation")
            if selected_file == "":
                if interactive_loop:
                    continue
                return

        if selected_operation == 'execution':
            approvals_path_v2 = repo_path / 'tests' / '.approvals.json'
            if approvals_path_v2.exists():
                # V2 behavior: approvals are per-section; we run only approved sections/files.
                try:
                    reg = json.loads(approvals_path_v2.read_text(encoding='utf-8'))
                except Exception as e:
                    print_error(f"Could not read approvals registry: {e}")
                    if interactive_loop:
                        continue
                    return

                sections = (reg or {}).get('sections', {})
                active = [s for s in (sections or {}).values() if isinstance(s, dict) and s.get('active') is True]
                approved = [s for s in active if s.get('approved') is True]
                pending = [s for s in active if s.get('approved') is not True]

                if pending:
                    print_error(f"Pending section approvals exist (active): {len(pending)}")
                    print_info("Build/run is blocked until all active sections are approved (option 3: Review approvals).")
                    if interactive_loop:
                        continue
                    return

                # Offer test files that contain at least one approved active section.
                approved_files: list[Path] = []
                for s in approved:
                    tf = s.get('test_file_rel')
                    if isinstance(tf, str) and tf:
                        p = (repo_path / tf).resolve()
                        if p.exists() and p not in approved_files:
                            approved_files.append(p)

                if not approved_files:
                    print_error("No approved sections available for execution.")
                    print_info("Use option 3 to approve at least one section.")
                    if interactive_loop:
                        continue
                    return

                print(f"\n{Colors.BOLD}Approved Test Files (has approved sections):{Colors.ENDC}")
                for idx, test_path in enumerate(approved_files, start=1):
                    rel = test_path.relative_to(repo_path)
                    print(f"{idx:2d}. {str(rel).replace('\\\\', '/')} ")

                choice = input("Select tests to run (e.g. 1 or 1,3 or 'a' for all) or 'b' to go back: ").strip().lower()
                if choice in ('b', 'back', 'q', 'quit', 'exit'):
                    if interactive_loop:
                        continue
                    return

                if choice in ('a', 'all', ''):
                    selected_tests = approved_files
                else:
                    selected_tests = []
                    parts = [p.strip() for p in choice.split(',') if p.strip()]
                    ok_sel = True
                    for part in parts:
                        if not part.isdigit():
                            ok_sel = False
                            break
                        i = int(part)
                        if i < 1 or i > len(approved_files):
                            ok_sel = False
                            break
                        selected_tests.append(approved_files[i - 1])
                    if not ok_sel or not selected_tests:
                        print_error("Invalid selection.")
                        if interactive_loop:
                            continue
                        return

                # Derive the CTest regex from the selected test file(s).
                names = [_cmake_test_name_for_test_file(repo_path, p) for p in selected_tests]
                ctest_regex = "|".join(sorted(set(names)))

                print_info("Selected approved tests; will run runner next...")
                # Fall through to the shared execution block below (so option 8 can chain coverage).

            else:
                # Legacy behavior (file-level approvals)
                # Always show a review status checkpoint before execution.
                # This makes it easy to see what's still pending and approve it.
                all_tests = find_generated_test_files(repo_path)
                if all_tests:
                    pending = [t for t in all_tests if not is_test_approved(repo_path, t)]
                    if pending:
                        print_info(f"{len(pending)} test(s) are still pending review.")
                        go_review = input("Review/approve pending tests now? (y/n): ").strip().lower()
                        if go_review == 'y':
                            review_ok = run_review_and_approve_phase(repo_path)
                            # If user only wanted to approve, return to menu rather than forcing execution.
                            if interactive_loop:
                                continue
                            if not review_ok:
                                return
                            return
                        else:
                            print_info("Execution cancelled (pending approvals).")
                            if interactive_loop:
                                continue
                            return

                # Only offer tests that are already approved.
                approved_tests = [t for t in all_tests if is_test_approved(repo_path, t)]

                if not all_tests:
                    print_error("No generated test files found under tests/. Run generation first.")
                    if interactive_loop:
                        continue
                    return

                if not approved_tests:
                    print_info("No approved tests found. Starting review phase...")
                    review_ok = run_review_phase(repo_path)
                    if interactive_loop:
                        continue
                    if not review_ok:
                        return
                    return

                if not approved_tests:
                    print_error("No approved tests available for execution.")
                    if interactive_loop:
                        continue
                    return

                print(f"\n{Colors.BOLD}Approved Tests Ready for Execution:{Colors.ENDC}")

                # Numbered picker
                for idx, test_path in enumerate(approved_tests, start=1):
                    rel = test_path.relative_to(repo_path)
                    print(f"{idx:2d}. {str(rel).replace('\\\\', '/')}")

                choice = input("\nSelect tests to run (e.g. 1 or 1,3 or 'a' for all): ").strip().lower()
                if choice == 'a' or choice == 'all' or choice == '':
                    selected_tests = approved_tests
                else:
                    selected_tests = []
                    parts = [p.strip() for p in choice.split(',') if p.strip()]
                    ok = True
                    for part in parts:
                        if not part.isdigit():
                            ok = False
                            break
                        i = int(part)
                        if i < 1 or i > len(approved_tests):
                            ok = False
                            break
                        selected_tests.append(approved_tests[i - 1])
                    if not ok or not selected_tests:
                        print_error("Invalid selection.")
                        if interactive_loop:
                            continue
                        return

                # Map selected test filenames to a best-effort ctest regex.
                # Common convention in this repo: add_test(NAME test_<Unit>) and file: tests/test_<Unit>.cpp
                stems = [re.escape(p.stem) for p in selected_tests]
                ctest_regex = "|".join(stems)
                print_info(f"Will run ctest -R: {ctest_regex}")

                try:
                    rels = [str(p.relative_to(repo_path)).replace('\\', '/') for p in selected_tests]
                except Exception:
                    rels = [p.name for p in selected_tests]
                selected_scope_label = "Selected test" + ("s" if len(rels) != 1 else "") + ": " + (", ".join(rels))

                proceed = input("Proceed to build & run selected tests? (y/n): ").strip().lower()
                if proceed != 'y':
                    print_info("Execution cancelled.")
                    if interactive_loop:
                        continue
                    return

        if selected_operation == 'coverage':
            if not has_coverage_data(repo_path):
                print_info("No existing coverage data (*.gcda) found yet.")
                print_info("Coverage tool will auto-build and run tests to generate data (unless disabled).")

            print("\nSelect Coverage Scope:")
            print("1. Whole repository coverage")
            print("2. Select approved test(s) (single/multiple)")
            while True:
                scope_choice = input("Enter choice (1-2) or 'b' to go back: ").strip().lower()
                if scope_choice in ('b', 'back', 'q', 'quit', 'exit'):
                    if interactive_loop:
                        continue
                    return
                if scope_choice in ('1', '2'):
                    break
                print_error("Invalid choice. Please enter 1 or 2.")

            compilable = set(find_compilable_test_files(repo_path))

            approvals_path_v2 = repo_path / 'tests' / '.approvals.json'
            if approvals_path_v2.exists():
                # V2: eligible tests are those with at least one approved active section.
                try:
                    reg = json.loads(approvals_path_v2.read_text(encoding='utf-8'))
                except Exception as e:
                    print_error(f"Could not read approvals registry: {e}")
                    if interactive_loop:
                        continue
                    return

                sections = (reg or {}).get('sections', {})
                active = [s for s in (sections or {}).values() if isinstance(s, dict) and s.get('active') is True]
                approved_secs = [s for s in active if s.get('approved') is True]
                approved_files: list[Path] = []
                for s in approved_secs:
                    tf = s.get('test_file_rel')
                    if isinstance(tf, str) and tf:
                        p = (repo_path / tf).resolve()
                        if p.exists() and p not in approved_files:
                            approved_files.append(p)
                # Show ALL approved test files, even if not present in compilation_report.
                eligible = approved_files
            else:
                # Legacy: eligible tests are those with file-level approval flags.
                approved = [t for t in find_generated_test_files(repo_path) if is_test_approved(repo_path, t)]
                eligible = approved

            if not eligible:
                print_error("No eligible tests for coverage.")
                print_info("Eligible means: test exists + approved + compilable.")
                if interactive_loop:
                    continue
                return

            if scope_choice == '1':
                proceed = input("\nGenerate whole-repository coverage report now? (y/n): ").strip().lower()
                if proceed != 'y':
                    print_info("Coverage cancelled.")
                    if interactive_loop:
                        continue
                    return

                # Whole repo coverage: do not pass --include-file.
                cov_cmd = [
                    sys.executable,
                    '-m',
                    'ai_c_test_coverage.cli',
                    str(repo_path),
                    '--safety-level',
                    getattr(args, 'safety_level', 'QM'),
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    '--build-dir',
                    'tests/build',
                    '--output-dir',
                    'tests/coverage_report',
                    '--report-base',
                    'all',
                    '--html',
                    '--allow-no-data',
                ]

                success, _ = run_command(
                    cov_cmd,
                    cwd=workspace / 'CW_Test_Cov',
                    description="Running coverage analysis",
                )
                operation_success = bool(success)

                # Skip the later generic coverage handling.
                if not interactive_loop:
                    return
                # Jump to end-of-iteration status reporting.
                selected_operation = 'coverage'
                run_coverage = True
                run_execution = False
                run_generation = False
                run_analysis = False
                run_review = False
                # Let the main loop print completion + recommendations.
                # Note: control continues below.

            print(f"\n{Colors.BOLD}Tests Eligible for Coverage:{Colors.ENDC}")
            for idx, test_path in enumerate(eligible, start=1):
                rel = test_path.relative_to(repo_path)
                tag = ""
                if compilable:
                    tag = " [compiles_yes]" if test_path in compilable else " [not in compilation_report]"
                print(f"{idx:2d}. {str(rel).replace('\\\\', '/')}{tag}")

            choice = input("\nSelect tests for coverage (e.g. 1 or 1,3 or 'a' for all): ").strip().lower()
            if choice in ('a', 'all', ''):
                selected_cov_tests = eligible
                coverage_label = 'all'
            else:
                selected_cov_tests = []
                parts = [p.strip() for p in choice.split(',') if p.strip()]
                ok = True
                for part in parts:
                    if not part.isdigit():
                        ok = False
                        break
                    i = int(part)
                    if i < 1 or i > len(eligible):
                        ok = False
                        break
                    selected_cov_tests.append(eligible[i - 1])
                if not ok or not selected_cov_tests:
                    print_error("Invalid selection.")
                    if interactive_loop:
                        continue
                    return
                coverage_label = selected_cov_tests[0].stem if len(selected_cov_tests) == 1 else 'selected'

            # If the selection includes tests not present in compilation_report, confirm.
            if compilable and any(t not in compilable for t in selected_cov_tests):
                print_info("Note: Some selected tests are not marked compiles_yes in tests/compilation_report.")
                print_info("You can still run coverage, but build may fail if those tests don't compile.")
                confirm = input("Proceed anyway? (y/n): ").strip().lower()
                if confirm != 'y':
                    print_info("Coverage cancelled.")
                    if interactive_loop:
                        continue
                    return

            try:
                rels = [str(p.relative_to(repo_path)).replace('\\', '/') for p in selected_cov_tests]
            except Exception:
                rels = [p.name for p in selected_cov_tests]
            selected_scope_label = "Coverage from test" + ("s" if len(rels) != 1 else "") + ": " + (", ".join(rels))

            if len(selected_cov_tests) > 1:
                print("\nSelect Coverage Output Mode:")
                print("1. Combined report for all selected")
                print("2. Per-file reports (one report per selected test)")
                print("3. Both combined + per-file")
                while True:
                    mode_choice = input("Enter choice (1-3): ").strip()
                    if mode_choice in ('1', '2', '3'):
                        break
                    print_error("Invalid choice. Please enter 1-3.")
            else:
                mode_choice = '1'

            proceed = input("\nGenerate coverage report now? (y/n): ").strip().lower()
            if proceed != 'y':
                print_info("Coverage cancelled.")
                if interactive_loop:
                    continue
                return

            # Best-effort mapping from selected tests -> production source files.
            # This keeps the coverage report focused only on the code under test.
            include_files: list[str] = []
            for test_path in selected_cov_tests:
                stem = test_path.stem
                unit = stem[5:] if stem.startswith('test_') else stem
                # Prefer src/**/<Unit>.cpp (or .c). Exclude anything under tests/.
                candidates = []
                for ext in ('.cpp', '.c', '.cc', '.cxx'):
                    candidates.extend((repo_path / 'src').rglob(f"{unit}{ext}"))
                candidates = [p for p in candidates if 'tests' not in p.parts]
                if candidates:
                    # Include the first match (repo-specific conventions should keep this unique).
                    rel = candidates[0].relative_to(repo_path)
                    include_files.append(str(rel).replace('\\\\', '/'))

            # Carry into the later coverage command construction.
            coverage_include_files = include_files

            # For multi-selection, optionally generate per-file reports now.
            # (Combined report is handled by the existing run_coverage path below.)
            if len(selected_cov_tests) > 1 and mode_choice in ('2', '3'):
                per_file_ok = True
                for test_path in selected_cov_tests:
                    per_label = test_path.stem

                    per_include: list[str] = []
                    stem = test_path.stem
                    unit = stem[5:] if stem.startswith('test_') else stem
                    candidates: list[Path] = []
                    for ext in ('.cpp', '.c', '.cc', '.cxx'):
                        candidates.extend((repo_path / 'src').rglob(f"{unit}{ext}"))
                    candidates = [p for p in candidates if 'tests' not in p.parts]
                    if candidates:
                        rel = candidates[0].relative_to(repo_path)
                        per_include.append(str(rel).replace('\\\\', '/'))

                    per_cmd = [
                        sys.executable,
                        '-m',
                        'ai_c_test_coverage.cli',
                        str(repo_path),
                        '--safety-level',
                        getattr(args, 'safety_level', 'QM'),
                        *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                        '--build-dir',
                        'tests/build',
                        '--output-dir',
                        'tests/coverage_report',
                        '--report-base',
                        per_label,
                        '--html',
                        '--allow-no-data',
                    ]
                    for inc in per_include:
                        per_cmd.extend(['--include-file', inc])

                    ok, _ = run_command(
                        per_cmd,
                        cwd=workspace / 'CW_Test_Cov',
                        description=f"Running per-file coverage ({per_label})",
                    )
                    per_file_ok = per_file_ok and bool(ok)

                # Keep combined report generation based on mode.
                if mode_choice == '2':
                    # Per-file only; skip combined coverage below.
                    operation_success = per_file_ok
                    if not interactive_loop:
                        return
                    # Jump to end-of-iteration status reporting.
                    selected_operation = 'coverage'
                    run_coverage = False
                    run_execution = False
                    run_generation = False
                    run_analysis = False
                    run_review = False
                    # Let the main loop print completion + recommendations.
                    # Note: control continues below.
                else:
                    operation_success = operation_success and per_file_ok

        print_info(f"Selected operation: {selected_operation}")
        if selected_model:
            print_info(f"Selected model: {selected_model}")
        print_info(f"Workspace: {workspace}")
        print_info(f"Target Repository: {repo_path}")
        if selected_file:
            print_info(f"Target File: {selected_file}")
        elif selected_scope_label:
            print_info(f"Scope: {selected_scope_label}")
        else:
            print_info("Scope: Whole Repository")

        # Track timing and success
        start_time = time.time()
        phases_completed = 0
        operation_success = True

        run_analysis = selected_operation == 'analysis'
        run_generation = selected_operation == 'generation'
        run_review = selected_operation == 'review'
        run_execution = selected_operation == 'execution'
        run_coverage = selected_operation == 'coverage'

        # --------------------------------------------------------------------
        # Execute selected operation (interactive path)
        # --------------------------------------------------------------------
        if run_analysis:
            success, _ = run_command(
                [
                    sys.executable,
                    '-m',
                    'ai_c_test_analyzer.cli',
                    '--repo-path',
                    str(repo_path),
                    '--safety-level',
                    getattr(args, 'safety_level', 'QM'),
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    '--disable-mcdc',
                ],
                cwd=workspace / 'CW_Test_Analyzer',
                description="Analyzing codebase",
            )
            operation_success = bool(success)

        elif run_generation:
            _load_dotenv_if_present(workspace / ".env")

            api_key = args.api_key
            if selected_model and selected_model != 'ollama':
                if not api_key:
                    env_var = f"{selected_model.upper()}_API_KEY"
                    api_key = os.environ.get(env_var)
                if not api_key:
                    print_info(f"No API key found for model '{selected_model}'.")
                    print_info("Set it via environment variable or enter it now (input hidden).")
                    try:
                        api_key = getpass.getpass("")
                    except Exception:
                        api_key = input("API key (will be visible): ")
                api_key = (api_key or "").strip()
                if not api_key:
                    print_error("No API key provided. Generation cancelled.")
                    operation_success = False
                    if interactive_loop:
                        # Always return to the main menu.
                        continue
                    return

            test_output_dir = repo_path / "tests"
            gen_cmd = [
                sys.executable,
                '-m',
                'ai_c_test_generator.cli',
                '--repo-path',
                str(repo_path),
                '--source-dir',
                args.source_dir,
                '--output',
                str(test_output_dir),
                '--safety-level',
                getattr(args, 'safety_level', 'QM'),
            ]

            if getattr(args, 'policy_file', None):
                gen_cmd.extend(['--policy-file', str(args.policy_file)])
            if getattr(args, 'disable_mcdc', False):
                gen_cmd.append('--disable-mcdc')

            if selected_file:
                gen_cmd.extend(['--file', selected_file])

            if selected_model:
                gen_cmd.extend(['--model', selected_model])

            if selected_model and selected_model != 'ollama':
                gen_cmd.extend(['--api-key', api_key])

            success, _ = run_command(
                gen_cmd,
                cwd=workspace / 'CW_Test_Gen',
                description=(
                    f"Generating tests for {selected_file}" if selected_file else "Generating tests for whole repository"
                ),
            )
            operation_success = bool(success)

            if operation_success:
                # Always provide the numbered review/approve phase between generation and execution.
                start_review = input("\nProceed to Review & Approve phase now? (y/n): ").strip().lower()
                if start_review == 'y':
                    review_ok = run_review_and_approve_phase(repo_path)
                    if not review_ok:
                        operation_success = False

        elif run_review:
            operation_success = run_review_and_approve_phase(repo_path)

        elif run_execution:
            print_info("Running runner (build + ctest)...")
            coverage_ran = False
            runner_cmd = [
                sys.executable,
                '-m',
                'ai_test_runner.cli',
                str(repo_path),
                '--safety-level',
                getattr(args, 'safety_level', 'QM'),
            ]
            if getattr(args, 'policy_file', None):
                runner_cmd.extend(['--policy-file', str(args.policy_file)])
            if getattr(args, 'disable_mcdc', False):
                runner_cmd.append('--disable-mcdc')
            if ctest_regex:
                runner_cmd.extend(['--ctest-regex', ctest_regex])

            success, _ = run_command(
                runner_cmd,
                cwd=workspace / 'CW_Test_Run',
                description="Building and running tests",
                stream_output=True,
            )
            operation_success = bool(success)

            # Option 8: offer coverage after a successful run (do not auto-run).
            if prompt_for_coverage_after_execution and operation_success:
                run_cov = input("\nRun coverage now? (y/n): ").strip().lower()
                if run_cov == 'y':
                    also_run_coverage = True

            # Coverage execution (only if explicitly requested).
            if also_run_coverage and operation_success:
                cov_label = 'all'
                if selected_tests:
                    cov_label = selected_tests[0].stem if len(selected_tests) == 1 else 'selected'

                cov_cmd = [
                    sys.executable,
                    '-m',
                    'ai_c_test_coverage.cli',
                    str(repo_path),
                    '--safety-level',
                    getattr(args, 'safety_level', 'QM'),
                    *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                    '--build-dir',
                    'tests/build',
                    '--output-dir',
                    'tests/coverage_report',
                    '--report-base',
                    cov_label,
                    '--html',
                    '--allow-no-data',
                ]

                include_files: list[str] = []
                for test_path in (selected_tests or []):
                    stem = test_path.stem
                    unit = stem[5:] if stem.startswith('test_') else stem
                    candidates: list[Path] = []
                    for ext in ('.cpp', '.c', '.cc', '.cxx'):
                        candidates.extend((repo_path / 'src').rglob(f"{unit}{ext}"))
                    candidates = [p for p in candidates if 'tests' not in p.parts]
                    if candidates:
                        rel = candidates[0].relative_to(repo_path)
                        include_files.append(str(rel).replace('\\\\', '/'))

                for inc in include_files:
                    cov_cmd.extend(['--include-file', inc])

                success, _ = run_command(
                    cov_cmd,
                    cwd=workspace / 'CW_Test_Cov',
                    description="Running coverage analysis",
                )
                operation_success = operation_success and bool(success)
                coverage_ran = bool(success)

        elif run_coverage:
            cov_label = None
            if 'coverage_label' in locals() and coverage_label:
                cov_label = coverage_label
            else:
                cov_label = 'all'

            cov_cmd = [
                sys.executable,
                '-m',
                'ai_c_test_coverage.cli',
                str(repo_path),
                '--safety-level',
                getattr(args, 'safety_level', 'QM'),
                *( ['--policy-file', str(getattr(args, 'policy_file'))] if getattr(args, 'policy_file', None) else [] ),
                '--build-dir',
                'tests/build',
                '--output-dir',
                'tests/coverage_report',
                '--report-base',
                cov_label,
                '--html',
                '--allow-no-data',
            ]

            # Only include the mapped production files when we can resolve them.
            include_files = list(locals().get('coverage_include_files') or [])

            # Fallback: if a single test label was selected and mapping didn't populate,
            # infer the unit name from the label and include the matching src file.
            if not include_files and cov_label and cov_label not in ('all', 'selected'):
                unit = cov_label[5:] if cov_label.startswith('test_') else cov_label
                candidates = []
                for ext in ('.cpp', '.c', '.cc', '.cxx'):
                    candidates.extend((repo_path / 'src').rglob(f"{unit}{ext}"))
                candidates = [p for p in candidates if 'tests' not in p.parts]
                if candidates:
                    rel = candidates[0].relative_to(repo_path)
                    include_files.append(str(rel).replace('\\\\', '/'))

            for inc in include_files:
                cov_cmd.extend(['--include-file', inc])

            success, _ = run_command(
                cov_cmd,
                cwd=workspace / 'CW_Test_Cov',
                description="Running coverage analysis",
            )
            operation_success = bool(success)

        if operation_success:
            print_success(f"{selected_operation.title()} completed.")
        else:
            print_error(f"{selected_operation.title()} failed. Review errors above.")

        if not interactive_loop:
            return

        # Default UX: always return to main menu (no y/n loop).
        # Provide a lightweight next-step recommendation.
        if operation_success:
            if selected_operation in ('analyze_v2', 'analysis'):
                print_info("Next recommended: 2 (Generate BASE tests)")
            elif selected_operation in ('gen_base_v2', 'generation'):
                print_info("Next recommended: 3 (Approve pending sections / Review approvals)")
            elif selected_operation in ('approve_v2', 'review'):
                print_info("Next recommended: 4 (Build + Run regression)")
            elif selected_operation == 'execution':
                if 'coverage_ran' in locals() and locals().get('coverage_ran'):
                    print_info("Next recommended: 6 (MC/DC gap analysis)")
                else:
                    print_info("Next recommended: 5 (Coverage reports)")
            elif selected_operation == 'coverage':
                print_info("Next recommended: 6 (MC/DC gap analysis)")
        else:
            print_info("Next: fix errors above, then rerun this step.")

        # Return to main menu.
        continue
    
    # ============================================================================
    # PHASE 1: CODE ANALYSIS
    # ============================================================================
    if run_analysis:
        print_phase(1, "CODE ANALYSIS (CW_Test_Analyzer)")
        
        analysis_dir = repo_path / "tests" / "analysis"
        excel_file = analysis_dir / "analysis.xlsx"
        json_file = analysis_dir / "analysis.json"
        
        if excel_file.exists():
            print_info(f"Analysis already exists at {excel_file}")
            print_info("Delete it if you want to re-analyze")
            user_input = input("   Re-run analysis? (y/n): ")
            if user_input.lower() != 'y':
                print_info("Skipping analysis")
                phases_completed += 1
            else:
                success, _ = run_command(
                    [sys.executable, '-m', 'ai_c_test_analyzer.cli',
                     '--repo-path', str(repo_path),
                     '--disable-mcdc'],
                    cwd=workspace,
                    description="Analyzing codebase"
                )
                if success:
                    if excel_file.exists() or json_file.exists():
                        phases_completed += 1
                        print_success(f"Analysis saved to: {analysis_dir}")
                        if excel_file.exists():
                            print_info("Open analysis.xlsx to view results")
                    else:
                        print_error("Analyzer reported success, but expected output files were not found.")
                        print_info(f"Expected one of: {excel_file} or {json_file}")
                        operation_success = False
                else:
                    operation_success = False
        else:
            success, _ = run_command(
                [sys.executable, '-m', 'ai_c_test_analyzer.cli',
                 '--repo-path', str(repo_path),
                 '--disable-mcdc'],
                cwd=workspace,
                description="Analyzing codebase"
            )
            if success:
                if excel_file.exists() or json_file.exists():
                    phases_completed += 1
                    print_success(f"Analysis saved to: {analysis_dir}")
                else:
                    print_error("Analyzer reported success, but expected output files were not found.")
                    print_info(f"Expected one of: {excel_file} or {json_file}")
                    operation_success = False
            else:
                operation_success = False

    if args.only_analysis:
        if operation_success:
            print_success("Analysis complete.")
            print_info("Next: run generation with: python run_demo.py --repo-path <repo> (or add --only-generation)")
        else:
            print_error("Analysis failed. Check the errors above.")
            print_info("Fix the issues and try again.")
        return
    
    # ============================================================================
    # PHASE 2: AI TEST GENERATION
    # ============================================================================
    if run_generation:
        print_phase(2, "AI TEST GENERATION (CW_Test_Gen)")

        # Allow local .env for convenience (should not be committed).
        _load_dotenv_if_present(workspace / ".env")
        
        # Check for API key (only for generation with cloud models)
        api_key = args.api_key
        if selected_model and selected_model != 'ollama':
            if not api_key:
                env_var = f"{selected_model.upper()}_API_KEY"
                api_key = os.environ.get(env_var)
                if not api_key:
                    print_info(f"No {env_var} environment variable found.")
                    print_info("For security, set it permanently in your system environment variables.")
                    print_info(f"On Windows: setx {env_var} \"your_api_key_here\"")
                    print_info(f"On Linux/Mac: export {env_var}=your_api_key_here")
                    print_info("Or create a .env file in the workspace root (not committed to git).")
                    # Optional interactive prompt (does not echo).
                    print_info(f"Enter your {selected_model.upper()} API key below (input will be hidden):")
                    try:
                        api_key = getpass.getpass("")
                    except Exception:
                        # Fallback for environments where getpass doesn't work
                        print_info("getpass not available, falling back to regular input...")
                        api_key = input("API key (will be visible): ")
                    api_key = (api_key or "").strip()
                    api_key = (api_key or "").strip()
                    if not api_key:
                        print_error("No API key provided!")
                        print_info("Set the environment variable or provide via --api-key")
                        print_info("Or run with --skip-generation to use pre-generated tests")
                        sys.exit(1)
                    else:
                        # Optionally save to .env for convenience (user's choice)
                        save_to_env = input("Save API key to .env file for future runs? (y/n): ").strip().lower()
                        if save_to_env == 'y':
                            env_file = workspace / ".env"
                            try:
                                with open(env_file, 'a') as f:
                                    f.write(f"\n{env_var}={api_key}\n")
                                print_success(f"API key saved to {env_file} (remember to keep this file secure!)")
                            except Exception as e:
                                print_error(f"Failed to save to .env: {e}")
        
        # Use selected model (if any)
        model = selected_model if selected_model else 'none'
        if selected_model:
            if selected_model == 'ollama':
                print_info(f"Using AI model: {selected_model} (local, no API key needed)")
            else:
                print_info(f"Using AI model: {selected_model} (API key configured)")
        else:
            print_info("No AI model needed for this operation")

        test_output_dir = repo_path / "tests"

        if selected_file:
            # File-specific generation
            test_file_name = f"test_{selected_file.replace('.cpp', '.cpp')}"
            test_file = test_output_dir / test_file_name

            if test_file.exists():
                print_info(f"Test file already exists: {test_file}")
                user_input = input("   Re-generate tests? (y/n): ")
                if user_input.lower() != 'y':
                    print_info("Using existing test file")
                    phases_completed += 1
                else:
                    success, _ = run_command(
                        [sys.executable, '-m', 'ai_c_test_generator.cli',
                         '--repo-path', str(repo_path),
                         '--file', selected_file,
                         '--source-dir', args.source_dir,
                         '--output', str(test_output_dir),
                         '--model', model,
                         '--api-key', api_key],
                        cwd=workspace / 'CW_Test_Gen',
                        description=f"Generating tests for {selected_file}"
                    )
                    if success:
                        phases_completed += 1
                        print_success(f"Tests generated: {test_file}")
            else:
                success, _ = run_command(
                    [sys.executable, '-m', 'ai_c_test_generator.cli',
                     '--repo-path', str(repo_path),
                     '--file', selected_file,
                     '--source-dir', args.source_dir,
                     '--output', str(test_output_dir),
                     '--model', model,
                     '--api-key', api_key],
                    cwd=workspace / 'CW_Test_Gen',
                    description=f"Generating tests for {selected_file}"
                )
                if success:
                    phases_completed += 1
                    print_success(f"Tests generated: {test_file}")
                else:
                    operation_success = False
        else:
            # Whole repository generation
            print_info("Generating tests for all files in repository...")
            success, _ = run_command(
                [sys.executable, '-m', 'ai_c_test_generator.cli',
                 '--repo-path', str(repo_path),
                 '--source-dir', args.source_dir,
                 '--output', str(test_output_dir),
                 '--model', model,
                 '--api-key', api_key],
                cwd=workspace / 'CW_Test_Gen',
                description="Generating tests for whole repository"
            )
            if success:
                phases_completed += 1
                print_success("Tests generated for all files in repository")
            else:
                operation_success = False

        # Offer review phase after generation.
        if operation_success:
            start_review = input("\nStart manual review/approval now? (y/n): ").strip().lower()
            if start_review == 'y':
                review_ok = run_review_phase(repo_path)
                if not review_ok:
                    operation_success = False

    if args.only_generation:
        if operation_success:
            print_success("Generation complete.")
            print_info("Next: run build/tests with: python run_demo.py --repo-path <repo> --only-run")
        else:
            print_error("Generation failed. Check the errors above.")
            print_info("Fix the issues and try again.")
        return
    
    # ============================================================================
    # PHASE 3: COMPILATION & EXECUTION
    # ============================================================================
    if run_execution:
        print_phase(3, "COMPILATION & EXECUTION (CW_Test_Run)")

        # Phase 3 prerequisites: tests must exist.
        tests_dir = repo_path / "tests"
        if not tests_dir.exists():
            print_error("tests/ folder not found. Cannot build/run without generated tests.")
            print_info("Next: run generation first (Phase 2).")
            sys.exit(1)

        # Require at least one test_*.cpp file.
        test_glob = list(tests_dir.glob("test_*.cpp"))
        if not test_glob:
            print_error("No test_*.cpp files found. Cannot build/run without generated tests.")
            print_info("Next: run generation first (Phase 2).")
            sys.exit(1)

        # Review phase / approval gate
        # First, check V2 approvals: all active sections must be approved.
        approvals_path = repo_path / "tests" / ".approvals.json"
        if approvals_path.exists():
            try:
                reg = json.loads(approvals_path.read_text(encoding='utf-8'))
                sections = (reg or {}).get('sections', {})
                active = [s for s in sections.values() if isinstance(s, dict) and s.get('active') is True]
                unapproved = [s for s in active if s.get('approved') is not True]
                if unapproved:
                    print_error(f"Cannot build: {len(unapproved)} unapproved sections exist.")
                    print_info("Approve all sections before building (use option 3: Review approvals).")
                    operation_success = False
                    sys.exit(3)
            except Exception as e:
                print_error(f"Error reading approvals registry: {e}")
                operation_success = False
                sys.exit(3)

        try:
            enforce_manual_review_gate(repo_path)
        except SystemExit:
            # Offer interactive review to create approvals.
            print_info("Approvals missing or invalid. Starting review phase...")
            review_ok = run_review_phase(repo_path)
            if not review_ok:
                operation_success = False
                sys.exit(3)
            # Re-check gate after review.
            enforce_manual_review_gate(repo_path)
    
        print_info("Running runner (build + ctest)...")
        runner_cmd = [sys.executable, '-m', 'ai_test_runner.cli', str(repo_path)]
        # If we are in interactive execution mode, we may have computed a ctest regex.
        if 'ctest_regex' in locals() and ctest_regex:
            runner_cmd.extend(['--ctest-regex', ctest_regex])

        tests_success, output = run_command(
            runner_cmd,
            cwd=workspace / 'CW_Test_Run',
            description="Building and running tests",
            stream_output=True
        )

        if tests_success:
            print_success("Tests built and executed successfully!")
            phases_completed += 2
            print_info("Check tests/test_reports/ for detailed results")
        else:
            print_error("Build and/or test execution failed!")
            print_info("Check logs for details")
            operation_success = False

        if args.only_run:
            return
    
        # ============================================================================
        # PHASE 4: COVERAGE ANALYSIS
        # ============================================================================
        if run_coverage and tests_success:
            print_phase(4, "COVERAGE ANALYSIS (CW_Test_Cov)")

            cov_cmd = [
                sys.executable,
                '-m',
                'ai_c_test_coverage.cli',
                str(repo_path),
                '--build-dir',
                'tests/build',
                '--output-dir',
                'tests/coverage_report',
                '--html',
                '--allow-no-data',
            ]

            # Prefer include-only scoping if available (more precise than regex filters).
            for inc in (locals().get('coverage_include_files') or []):
                cov_cmd.extend(['--include-file', inc])

            # Back-compat: if older flows computed a regex filter, apply it.
            if 'coverage_filter' in locals() and coverage_filter and not (locals().get('coverage_include_files') or []):
                cov_cmd.extend(['--filter', coverage_filter])

            success, _ = run_command(
                cov_cmd,
                cwd=workspace / 'CW_Test_Cov',
                description="Running coverage analysis",
            )

            if success:
                print_success("Coverage analysis completed successfully!")
                phases_completed += 1
                print_info("Check tests/coverage_report/ for detailed results")
            else:
                print_error("Coverage analysis failed!")
                print_info("Check logs for details")
                operation_success = False
        elif run_coverage and not tests_success:
            print_info("Skipping coverage: build/tests failed (no reliable coverage data).")
    
    # ============================================================================
    # SUMMARY
    # ============================================================================
    elapsed = time.time() - start_time
    
    print(f"\n{Colors.HEADER}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}DEMO SUMMARY{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*70}{Colors.ENDC}\n")
    
    print(f"⏱️  Total Time: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
    print(f"✅ Operation: {selected_operation.title()} - {'SUCCESS' if operation_success else 'FAILED'}")
    
    print(f"\n{Colors.BOLD}Generated Artifacts:{Colors.ENDC}")
    artifacts_found = False

    if run_analysis:
        analysis_dir = repo_path / "tests" / "analysis"
        if analysis_dir.exists():
            excel_file = analysis_dir / "analysis.xlsx"
            json_file = analysis_dir / "analysis.json"
            if excel_file.exists() or json_file.exists():
                print(f"  📊 Analysis: {analysis_dir}/")
                artifacts_found = True
            else:
                print(f"  ⚠️  Analysis directory exists but no output files found: {analysis_dir}/")

    if run_generation:
        test_files = list(repo_path.glob("tests/test_*.cpp"))
        if test_files:
            print(f"  🤖 Tests: {len(test_files)} test files generated")
            artifacts_found = True

        if run_execution:
            test_reports_dir = repo_path / "tests" / "test_reports"
            if test_reports_dir.exists():
                print(f"  📋 Test Reports: {test_reports_dir}/")
                artifacts_found = True

        if run_coverage:
            coverage_dir = repo_path / "tests" / "coverage_report"
            if coverage_dir.exists():
                print(f"  📈 Coverage: {coverage_dir}/")
                artifacts_found = True

    build_dir = repo_path / "tests" / "build"
    if build_dir.exists():
        print(f"  🔨 Build: {build_dir}/")
        artifacts_found = True

    if not artifacts_found:
        print(f"  ℹ️  No artifacts generated yet")

    
    print(f"\n{Colors.BOLD}Next Steps:{Colors.ENDC}")
    if run_analysis:
        if operation_success and (repo_path / "tests" / "analysis" / "analysis.xlsx").exists():
            print(f"  1. Review analysis.xlsx for codebase insights")
            print(f"  2. Run test generation next")
        elif operation_success:
            print(f"  1. Review analysis outputs under tests/analysis/")
            print(f"  2. Run test generation next")
        else:
            print(f"  1. Fix the analysis error above")
            print(f"  2. Re-run analysis")
    elif run_generation:
        if operation_success and list(repo_path.glob("tests/test_*.cpp")):
            print(f"  1. Examine generated test_*.cpp files")
            print(f"  2. Run test execution next")
        else:
            print(f"  1. Fix the generation error above")
            print(f"  2. Re-run test generation")
    elif run_execution:
        if operation_success:
            print(f"  1. Check test execution reports")
            print(f"  2. Run coverage analysis next")
        else:
            print(f"  1. Fix the build/test error above")
            print(f"  2. Re-run test execution")
    elif run_coverage:
        if operation_success:
            print(f"  1. Review coverage reports")
            print(f"  2. All phases complete!")
        else:
            print(f"  1. Fix the coverage error above")
            print(f"  2. Re-run coverage")
    
    if operation_success:
        print(f"\n{Colors.OKGREEN}{Colors.BOLD}🎉 SUCCESS! {selected_operation.title()} completed.{Colors.ENDC}")
    else:
        print(f"\n{Colors.WARNING}⚠️  Operation failed. Review errors above.{Colors.ENDC}")
    
    print()

    # Professional demo loop: always return to the main menu.
    if not interactive_loop:
        return
    return

def _print_demo_menu():
    print('\nVERISAFE TOOL MENU - Pipeline-First Navigation\n')

    print('Left Sidebar (Pipeline Stages):')
    print('  ▶ Project')
    print('  ▶ Analyzer')
    print('  ▶ Safety Policy')
    print('  ▶ Scenarios')
    print('  ▶ Test Generation')
    print('  ▶ Validation')
    print('  ▶ Approval')
    print('  ▶ Execution')
    print('  ▶ Evidence')

    print('\nStage Details:')
    print('\n1) Project')
    print('   Purpose: Context & configuration (no AI).')
    print('   Menu: Project Overview | Source Tree | Configuration | Model Selection')
    print('   Note: Model Selection applies to all LLM-assisted stages (planning, generation).')

    print('\n2) Analyzer (Ground Truth)')
    print('   Purpose: read-only facts from static analysis.')
    print('   Menu: Analyzer Status | Function Index | Call Graph | Hardware Touchpoints')

    print('\n3) Safety Policy')
    print('   Purpose: define policy constraints and coverage rules.')
    print('   Menu: Active Policy | Policy Constraints | Coverage Rules')

    print('\n4) Scenarios (Scenario Architect)')
    print('   Purpose: safety intent (scenarios), not tests.')
    print('   Menu: Planned Scenarios | Rejected Scenarios | Exclusions')

    print('\n5) Test Generation (Test Coder)')
    print('   Purpose: LLM-assisted synthesis of per-function test blocks.')
    print('   Menu: Generation Status | Generated Test Blocks | Live Generation Log')
    print('   Live log must show model name, function name, elapsed time, and raw model output fragments (SSE).')

    print('\n6) Validation (Deterministic Gates)')
    print('   Purpose: enforce deterministic rules (no markdown, no using namespace, no invented helpers).')
    print('   Menu: Scenario Validation | Test Validation | Compilation Check')

    print('\n7) Approval (Human-in-the-loop)')
    print('   Purpose: explicit approvals before execution.')
    print('   Menu: Pending Approvals | Approved Artifacts | Approval History')

    print('\n8) Execution')
    print('   Purpose: controlled build & run of approved tests only.')
    print('   Menu: Execution Status | Test Results')

    print('\n9) Evidence (Export & Audit)')
    print('   Purpose: certification-ready bundles and audit trails.')
    print('   Menu: Evidence Bundle | Audit Trail | Artifact Hashes')

    print('\nDo NOT include: Generate Everything buttons | Chat panels | Auto-run toggles | Hidden defaults | Fake progress bars')
    print('\nUse: `python run_demo.py --demo-menu` to show this menu. Integrate into web UI as left-rail pipeline navigation.')


def _interactive_demo_menu():
    workspace = Path(__file__).parent.resolve()
    # Visual-friendly header
    print_phase('MENU', 'VERISAFE Interactive Demo')
    repo_input = input("Repo (workspace-relative) [RailwaySignalSystem]: ").strip()
    repo_input = repo_input or 'RailwaySignalSystem'
    repo_path = Path(repo_input) if Path(repo_input).is_absolute() else (workspace / repo_input)
    repo_path = repo_path.resolve()
    if not repo_path.exists():
        print_error(f"Repository not found: {repo_path}")
        return

    # Minimal args namespace for pipeline runners
    args = argparse.Namespace(
        api_key=None,
        source_dir='src/logic',
        target_file='Interlocking.cpp',
        policy_file=None,
        disable_mcdc=False,
        skip_analysis=False,
        skip_generation=False,
        only_analysis=False,
        only_generation=False,
        only_run=False,
        pipeline='incremental',
        model='ollama',
        safety_level='SIL0',
        engineering_menu=False
    )

    def _print_menu():
        print('\n' + Colors.HEADER + ('='*66) + Colors.ENDC)
        print(f"{Colors.BOLD}{Colors.OKCYAN} Target: {repo_path.name}   —   Model: {args.model}   —   Safety: {args.safety_level}{Colors.ENDC}")
        print(Colors.HEADER + ('='*66) + Colors.ENDC)
        print(f"{Colors.BOLD}Choose a stage to run (type number).{Colors.ENDC}")
        print()
        menu_rows = [
            ('1', 'Project', 'Overview & config (informational)'),
            ('2', 'Analyzer', 'Static analysis (Phase 0-1)'),
            ('3', 'Safety Policy', 'Show active policy file'),
            ('4', 'Scenarios', 'Show scenarios.json if present'),
            ('5', 'Test Generation', 'Generate base tests (Phase 2)'),
            ('6', 'Validation', 'Check deterministic validation/status'),
            ('7', 'Approval', 'Interactive approvals (v2)'),
            ('8', 'Execution', 'Build & run approved tests (Phase 4)'),
            ('9', 'Evidence', 'Execution + Coverage (Phases 4-5)'),
            ('Q', 'Quit', 'Exit this menu')
        ]
        for k, title, desc in menu_rows:
            print(f"  {Colors.OKBLUE}{k}{Colors.ENDC}  {Colors.BOLD}{title:14}{Colors.ENDC}  -  {desc}")

    while True:
        _print_menu()
        choice = input('\nSelect stage (1-9, Q): ').strip().lower()
        if choice in ('q', 'quit', 'exit'):
            print_info('Exiting demo menu.')
            return
        if choice == '1':
            print_info(f'Project path: {repo_path}')
            continue
        if choice == '2':
            print_info('Running Analyzer (Phase 0-1)...')
            ok = run_incremental_pipeline_v2(repo_path=repo_path, workspace=workspace, args=args, from_phase=0, to_phase=1, selected_model=None, interactive_loop=False)
            print_success('Analyzer completed' if ok else 'Analyzer failed')
            continue
        if choice == '3':
            policy_file = workspace / 'safety_policy.yaml'
            if (repo_path / 'safety_policy.yaml').exists():
                policy_file = repo_path / 'safety_policy.yaml'
            try:
                print((policy_file).read_text(encoding='utf-8'))
            except Exception as e:
                print_error(f'Could not read policy: {e}')
            continue
        if choice == '4':
            scen = workspace / 'work' / 'scenarios.json'
            if (repo_path / 'work' / 'scenarios.json').exists():
                scen = repo_path / 'work' / 'scenarios.json'
            if scen.exists():
                try:
                    print(scen.read_text(encoding='utf-8'))
                except Exception as e:
                    print_error(f'Could not read scenarios: {e}')
            else:
                print_info('No scenarios.json found.')
            continue
        if choice == '5':
            print_info('Starting Test Generation (Phase 2)')
            ok = run_incremental_pipeline_v2(repo_path=repo_path, workspace=workspace, args=args, from_phase=2, to_phase=2, selected_model=args.model, interactive_loop=False, generation_source_dir=args.source_dir, generation_file=args.target_file)
            print_success('Generation completed' if ok else 'Generation failed')
            continue
        if choice == '6':
            print_info('Checking validation status...')
            ok, out = run_command([sys.executable, '-m', 'ai_c_test_generator.cli', 'status', '--repo-path', str(repo_path)], cwd=workspace / 'CW_Test_Gen', description='Checking validation status')
            if ok:
                print_success('Validation status OK')
                if out:
                    print(out)
            else:
                print_error('Validation reported issues')
                if out:
                    print(out)
            continue
        if choice == '7':
            print_info('Launching interactive approval UI (v2)')
            try:
                _interactive_approve_pending_sections_v2(repo_path=repo_path, workspace=workspace, interactive_loop=True)
            except Exception as e:
                print_error(f'Approval UI failed: {e}')
            continue
        if choice == '8':
            print_info('Running Execution (build + run)')
            ok = run_incremental_pipeline_v2(repo_path=repo_path, workspace=workspace, args=args, from_phase=4, to_phase=4, selected_model=None, interactive_loop=False)
            print_success('Execution completed' if ok else 'Execution failed')
            continue
        if choice == '9':
            print_info('Running Evidence steps (Execution + Coverage)')
            ok = run_incremental_pipeline_v2(repo_path=repo_path, workspace=workspace, args=args, from_phase=4, to_phase=5, selected_model=None, interactive_loop=False)
            print_success('Evidence steps completed' if ok else 'Evidence steps failed')
            continue
        print_error('Invalid selection. Choose 1-9 or Q to quit.')


if '--demo-menu' in sys.argv:
    _interactive_demo_menu()
    sys.exit(0)


if __name__ == "__main__":
    main()
