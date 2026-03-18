from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ai_c_test_coverage.coverage import run_gcovr

from ai_c_test_coverage.safety_policy import SafetyPolicy, save_safety_summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CW_Test_Cov - Coverage analysis for executed C/C++ unit tests (gcovr-based)"
    )
    parser.add_argument(
        "repo_path",
        help="Path to the target repository (e.g. RailwaySignalSystem)",
    )
    parser.add_argument(
        "--build-dir",
        default="tests/build",
        help="Build directory containing coverage artifacts (default: tests/build)",
    )
    parser.add_argument(
        "--output-dir",
        default="tests/coverage_report",
        help="Output directory under repo for coverage reports (default: tests/coverage_report)",
    )
    parser.add_argument(
        "--report-base",
        default="interlocking_test_report",
        help="Base name for coverage artifacts (default: interlocking_test_report)",
    )
    parser.add_argument(
        "--html",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate a single HTML coverage summary (<report-base>_coverage.html). Default: enabled. Use --no-html to disable.",
    )
    parser.add_argument(
        "--html-details",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate detailed per-file HTML pages (creates many files). Default: enabled. Use --no-html-details to disable.",
    )
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        help="Regex filter(s) passed through to gcovr to include only matching files. Can be repeated.",
    )
    parser.add_argument(
        "--include-file",
        action="append",
        default=[],
        help="Include only this file in the report (path relative to repo, or absolute). Can be repeated.",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include coverage for test sources under tests/ (default: excluded).",
    )
    parser.add_argument(
        "--allow-no-data",
        action="store_true",
        help="Do not fail if no *.gcda files are found (still writes a text report with the error output).",
    )

    parser.add_argument(
        "--auto-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Auto-configure/build with coverage flags and run tests (to generate *.gcda) before running gcovr. "
            "Default: enabled. Use --no-auto-run to only report existing coverage artifacts."
        ),
    )
    parser.add_argument(
        "--build-config",
        default="Debug",
        help="CMake/CTest configuration (multi-config generators). Default: Debug.",
    )
    parser.add_argument(
        "--ctest-regex",
        default=None,
        help="Optional CTest regex to limit which tests are executed before coverage collection.",
    )

    parser.add_argument(
        "--safety-level",
        choices=list(SafetyPolicy.allowed_levels()),
        default="QM",
        help=(
            "Configures which analyses, test types, and review gates are required so generated tests align with SIL expectations "
            "without claiming certification."
        ),
    )
    parser.add_argument("--policy-file", default=None)

    args = parser.parse_args()

    repo_root = Path(args.repo_path).resolve()

    # Auto-detect common build directory when the default doesn't exist.
    # RailwaySignalSystem uses <repo>/build by default.
    if args.build_dir == "tests/build":
        if (repo_root / "build").exists() and not (repo_root / "tests" / "build").exists():
            args.build_dir = "build"

    build_dir = (repo_root / args.build_dir).resolve() if not Path(args.build_dir).is_absolute() else Path(args.build_dir).resolve()
    base_output_dir = (repo_root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir).resolve()

    def _run(cmd: list[str], cwd: Path) -> int:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            # Keep output concise; print tail only.
            combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            lines = [ln for ln in combined.splitlines() if ln.strip()]
            tail = "\n".join(lines[-80:])
            print(f"❌ Command failed: {' '.join(cmd)}")
            if tail:
                print(tail)
        return proc.returncode

    def _has_gcda_files() -> bool:
        try:
            return any(build_dir.rglob("*.gcda"))
        except Exception:
            return False

    def _cmake_option_exists(opt_name: str) -> bool:
        cmakelists = repo_root / "CMakeLists.txt"
        if not cmakelists.exists():
            return False
        try:
            text = cmakelists.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        return opt_name in text

    def _ensure_coverage_build_and_tests() -> bool:
        """Best-effort: build with coverage flags, run tests, then coverage can be collected.

        Rationale: gcovr needs *.gcno (from compilation) and *.gcda (from execution).
        """

        build_dir.mkdir(parents=True, exist_ok=True)

        # Configure with coverage.
        cmake_args = ["cmake", "-S", str(repo_root), "-B", str(build_dir)]

        # Prefer repo-provided option if available.
        if _cmake_option_exists("RAILWAY_ENABLE_COVERAGE"):
            cmake_args.append("-DRAILWAY_ENABLE_COVERAGE=ON")
        else:
            # Generic fallback for GCC/Clang-like toolchains.
            cmake_args += [
                "-DCMAKE_C_FLAGS=--coverage",
                "-DCMAKE_CXX_FLAGS=--coverage",
                "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
                "-DCMAKE_SHARED_LINKER_FLAGS=--coverage",
            ]

        # If the repo supports fetching gtest (like RailwaySignalSystem), enable it automatically
        # so tests can build without additional user flags.
        if _cmake_option_exists("RAILWAY_FETCH_GTEST"):
            cmake_args.append("-DRAILWAY_FETCH_GTEST=ON")

        if _run(cmake_args, cwd=repo_root) != 0:
            return False

        # Build.
        build_cmd = ["cmake", "--build", str(build_dir), "--config", str(args.build_config)]
        if _run(build_cmd, cwd=repo_root) != 0:
            return False

        # Run tests.
        ctest_cmd = ["ctest", "--test-dir", str(build_dir), "-C", str(args.build_config), "--output-on-failure"]
        if args.ctest_regex:
            ctest_cmd += ["-R", str(args.ctest_regex)]
        if _run(ctest_cmd, cwd=repo_root) != 0:
            return False

        return True

    include_files: list[Path] = []
    for p in (args.include_file or []):
        if not p:
            continue
        path = Path(p)
        if not path.is_absolute():
            path = repo_root / path
        include_files.append(path)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    include_files_unique: list[Path] = []
    for p in include_files:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        include_files_unique.append(p)
    include_files = include_files_unique

    def _sanitize_report_base(s: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (s or "").strip())
        return safe or "interlocking_test_report"

    def _per_file_output_dir(file_path: Path) -> Path:
        try:
            inc_rel = file_path.resolve().relative_to(repo_root)
            # Put each file's coverage artifacts in its own folder for easy navigation.
            # Example: src/logic/ControllerLogic.cpp -> tests/coverage_report/src/logic/ControllerLogic.cpp/
            return (base_output_dir / inc_rel.parent / inc_rel.name).resolve()
        except Exception:
            return base_output_dir

    def _per_file_report_base(file_path: Path) -> str:
        # User requirement: report base should be the source filename (including extension).
        # Example: src/logic/ControllerLogic.cpp -> ControllerLogic.cpp_coverage.html
        try:
            inc_rel = file_path.resolve().relative_to(repo_root)
            name = inc_rel.name
        except Exception:
            name = file_path.name
        return _sanitize_report_base(name)

    try:
        # Ensure we have coverage execution artifacts before running gcovr.
        # If no gcda exists, auto-run the build+tests unless explicitly disabled.
        if args.auto_run and not _has_gcda_files():
            ok = _ensure_coverage_build_and_tests()
            if not ok and not args.allow_no_data:
                return 1

        # If multiple files are requested, behave like "single-file coverage" for each:
        # run gcovr once per file, mirror folder structure, and promote html-details to the main html.
        if len(include_files) > 1:
            all_outputs = []
            for file_path in include_files:
                outputs = run_gcovr(
                    repo_root=repo_root,
                    build_dir=build_dir,
                    output_dir=_per_file_output_dir(file_path),
                    report_base=_per_file_report_base(file_path),
                    html=bool(args.html),
                    html_details=bool(args.html and args.html_details),
                    filters=args.filter,
                    include_files=[file_path],
                    exclude_tests=not args.include_tests,
                    fail_if_no_data=not args.allow_no_data,
                )
                all_outputs.append(outputs)

            print(f"✅ Coverage reports generated for {len(all_outputs)} file(s)")
            # Keep output concise: print the base output dir and a count.
            print(f"   📁 Root: {base_output_dir}")
            return 0

        # Single-file or repo-wide coverage.
        output_dir = base_output_dir
        if len(include_files) == 1:
            output_dir = _per_file_output_dir(include_files[0])

        outputs = run_gcovr(
            repo_root=repo_root,
            build_dir=build_dir,
            output_dir=output_dir,
            report_base=_sanitize_report_base(args.report_base) if not include_files else _per_file_report_base(include_files[0]),
            html=bool(args.html),
            html_details=bool(args.html and args.html_details),
            filters=args.filter,
            include_files=include_files,
            exclude_tests=not args.include_tests,
            fail_if_no_data=not args.allow_no_data,
        )
    except Exception as e:
        print(f"❌ Coverage failed: {e}")
        return 1

    # Best-effort: update safety summary with coverage status.
    try:
        policy = SafetyPolicy.load(
            safety_level=args.safety_level,
            repo_root=repo_root,
            policy_file=args.policy_file,
        )

        text = outputs.text_report.read_text(encoding="utf-8", errors="replace") if outputs.text_report.exists() else ""

        def _find_pct_any(labels: list[str]) -> float | None:
            # Matches common gcovr summary lines like:
            #   Lines: 90.0% (9 out of 10)
            #   Branches: 80.0% (4 out of 5)
            # Some environments add leading whitespace or vary capitalization.
            for label in labels:
                rx = re.compile(
                    rf"^\s*{re.escape(label)}\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
                    re.MULTILINE | re.IGNORECASE,
                )
                m = rx.search(text)
                if not m:
                    continue
                try:
                    return float(m.group(1))
                except Exception:
                    continue
            return None

        statement_pct = _find_pct_any(["Lines", "Line", "Line Coverage", "Line coverage"]) 
        branch_pct = _find_pct_any(["Branches", "Branch", "Branch Coverage", "Branch coverage"])

        def _find_pct_from_cobertura_xml(xml_path: Path) -> tuple[float | None, float | None]:
            if not xml_path.exists():
                return None, None
            try:
                root = ET.parse(str(xml_path)).getroot()
            except Exception:
                return None, None

            # Cobertura-style root: <coverage line-rate="0.85" branch-rate="0.5" ...>
            lr = root.attrib.get("line-rate")
            br = root.attrib.get("branch-rate")

            def _rate_to_pct(rate: str | None) -> float | None:
                if rate is None:
                    return None
                try:
                    v = float(rate)
                except Exception:
                    return None
                # Some tools may already emit percentages; assume <=1 is a rate.
                if 0.0 <= v <= 1.0:
                    return v * 100.0
                if 0.0 <= v <= 100.0:
                    return v
                return None

            return _rate_to_pct(lr), _rate_to_pct(br)

        # Fallback: if the text report doesn't contain summary percentages, try the XML.
        if statement_pct is None or branch_pct is None:
            xml_stmt, xml_br = _find_pct_from_cobertura_xml(outputs.xml_report)
            if statement_pct is None:
                statement_pct = xml_stmt
            if branch_pct is None:
                branch_pct = xml_br

        stmt_target = policy.coverage_target.get("statement")
        br_target = policy.coverage_target.get("branch")

        def _status(actual: float | None, target: object) -> str:
            if actual is None:
                return "UNKNOWN"
            try:
                t = float(target)
            except Exception:
                return "UNKNOWN"
            return "PASS" if actual + 1e-9 >= t else "FAIL"

        save_safety_summary(
            repo_root,
            {
                "safety_level": policy.safety_level,
                "coverage": {
                    "statement_pct": statement_pct,
                    "branch_pct": branch_pct,
                    "targets": policy.coverage_target,
                },
                "coverage_status": {
                    "statement": _status(statement_pct, stmt_target),
                    "branch": _status(branch_pct, br_target),
                },
            },
        )
    except Exception:
        pass

    print("✅ Coverage reports generated")
    print(f"   📄 Text: {outputs.text_report}")
    print(f"   � XML:  {outputs.xml_report}")
    if outputs.html_report is not None:
        print(f"   🌐 HTML: {outputs.html_report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
