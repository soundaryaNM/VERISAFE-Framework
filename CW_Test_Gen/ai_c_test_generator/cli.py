#!/usr/bin/env python3
"""
CLI interface for AI C Test Generator
"""

import argparse
import os
import sys
import json
import re
from pathlib import Path

# Support running as an installed module (preferred) and as a direct script.
try:
    from .generator import SmartTestGenerator
    from .validator import TestValidator
    from .approvals import (
        ApprovalsRegistry,
        parse_sections,
        repo_relpath,
        update_section_header,
    )
except ImportError:
    # Direct execution (e.g. `python CW_Test_Gen/ai_c_test_generator/cli.py ...`)
    # has no package context, so relative imports fail.
    this_file = Path(__file__).resolve()
    cw_test_gen_root = this_file.parents[1]  # .../CW_Test_Gen
    repo_root_guess = cw_test_gen_root.parent
    cw_test_analyzer_root = repo_root_guess / "CW_Test_Analyzer"

    for p in (cw_test_gen_root, cw_test_analyzer_root):
        if p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))

    from ai_c_test_generator.generator import SmartTestGenerator
    from ai_c_test_generator.validator import TestValidator
    from ai_c_test_generator.approvals import (
        ApprovalsRegistry,
        parse_sections,
        repo_relpath,
        update_section_header,
    )

from .safety_policy import SafetyPolicy

# Add compatibility for older Python versions
try:
    from importlib.metadata import packages_distributions
except ImportError:
    # Python < 3.10 compatibility
    try:
        from importlib_metadata import packages_distributions
    except ImportError:
        # Fallback implementation
        def packages_distributions():
            return {}

from ai_c_test_analyzer.analyzer import DependencyAnalyzer

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\032[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def create_parser():
    """Create argument parser for the CLI tool"""
    parser = argparse.ArgumentParser(
        description="AI-powered C and C++ unit test generator using Ollama, Google Gemini, or Groq",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate tests for all C files in current directory
  ai-c-testgen --api-key YOUR_API_KEY

  # Generate tests for specific directory
  ai-c-testgen --repo-path /path/to/c/project --api-key YOUR_API_KEY

  # Use environment variable for API key
  export GEMINI_API_KEY=your_key_here
  ai-c-testgen --repo-path /path/to/c/project

  # Use Groq for faster generation
  ai-c-testgen --repo-path /path/to/c/project --model groq --api-key YOUR_GROQ_KEY

  # Enable automatic regeneration for low-quality tests
  ai-c-testgen --repo-path /path/to/c/project --regenerate-on-low-quality --max-regeneration-attempts 3

  # Set quality threshold (only regenerate if below medium quality)
  ai-c-testgen --repo-path /path/to/c/project --regenerate-on-low-quality --quality-threshold medium
        """
    )

    parser.add_argument(
        '--repo-path',
        type=str,
        default='.',
        help='Path to the C repository (default: current directory)'
    )

    parser.add_argument(
        '--safety-level',
        choices=list(SafetyPolicy.allowed_levels()),
        default='QM',
        help=(
            'Configures which analyses, test types, and review gates are required so generated tests align with SIL expectations '
            'without claiming certification. Allowed: QM, SIL1, SIL2, SIL3.'
        ),
    )
    parser.add_argument(
        '--policy-file',
        default=None,
        help='Optional path to safety_policy.yaml (default: <repo>/safety_policy.yaml then <workspace>/safety_policy.yaml)'
    )
    parser.add_argument(
        '--disable-mcdc',
        action='store_true',
        help='Advanced override: disable MC/DC analysis/generation even if the selected safety level would enable it.'
    )

    parser.add_argument(
        '--output',
        type=str,
        default='tests',
        help='Output directory for generated tests (default: tests)'
    )

    parser.add_argument(
        '--api-key',
        type=str,
        help='API key for cloud models (Gemini or Groq, can also use GEMINI_API_KEY or GROQ_API_KEY env vars)'
    )

    parser.add_argument(
        '--source-dir',
        type=str,
        default='src',
        help='Source directory containing C files (default: src)'
    )

    parser.add_argument(
        '--file',
        type=str,
        help='Specific C/C++ file to process (optional, processes all if not specified)'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )

    parser.add_argument(
        '--wait-before-exit',
        action='store_true',
        help='Wait for user input before exiting to preserve terminal output (useful when running from GUI or tasks)'
    )

    parser.add_argument(
        '--model',
        type=str,
        choices=['ollama', 'gemini', 'groq', 'github'],
        default='gemini',
        help='AI model to use: ollama (local, safe), gemini (cloud, requires API key), groq (fast cloud, requires API key), or github (GitHub Models, requires GITHUB_TOKEN)'
    )
    parser.add_argument(
        '--log-file',
        type=str,
        default=None,
        help='Optional path to a file where CLI output will be logged (relative to repo root)'
    )

    parser.add_argument(
        '--version',
        action='version',
        version='%(prog)s 1.0.0'
    )

    parser.add_argument(
        '--max-regeneration-attempts',
        type=int,
        default=2,
        help='Maximum number of regeneration attempts for low-quality tests (default: 2)'
    )

    parser.add_argument(
        '--regenerate-on-low-quality',
        action='store_true',
        help='Automatically regenerate tests that are validated as low quality'
    )

    parser.add_argument(
        '--redact-sensitive',
        action='store_true',
        help='Redact sensitive content (comments, strings, credentials) before sending to API'
    )

    parser.add_argument(
        '--quality-threshold',
        type=str,
        choices=['low', 'medium', 'high'],
        default='medium',
        help='Quality threshold for regeneration (low, medium, high). Only regenerate tests below this threshold (default: medium)'
    )

    parser.add_argument(
        '--max-api-retries',
        type=int,
        default=5,
        help='Maximum number of API retries for timeouts and rate limits (default: 5)'
    )

    parser.add_argument(
        '--no-cleanup-reports',
        action='store_true',
        help='Skip cleaning up old verification and compilation reports before generating new ones'
    )

    parser.add_argument(
        '--skip-if-valid',
        action='store_true',
        help='Skip generation if a valid (compilable and realistic) test file already exists'
    )

    parser.add_argument(
        '--mode',
        type=str,
        choices=['generate', 'analyze'],
        default='generate',
        help='Operation mode: generate tests (default) or analyze repository only'
    )

    parser.add_argument(
        '--analysis-file',
        type=str,
        help='Path to pre-computed analysis JSON file (for generate mode)'
    )

    parser.add_argument(
        '--enable-gmock',
        action='store_true',
        help='Opt-in: allow GoogleMock (gmock) generation ONLY when the repo exposes real virtual interfaces. Default: disabled (use compile-time stubs/fakes).'
    )

    return parser


def list_functions_for_file(file_path, repo_path, verbose=False):
    """List functions in a file for debugging/mapping"""
    try:
        analyzer = DependencyAnalyzer(repo_path)
        # Extract functions directly from the file
        functions = analyzer._extract_functions(file_path)
        if verbose and functions:
            print(f"   [INFO] [MAPPED] Found {len(functions)} functions in {os.path.basename(file_path)}:")
            for func in functions[:10]:  # Limit to first 10 for brevity
                print(f"     - {func['name']} ({func.get('signature', 'unknown')})")
            if len(functions) > 10:
                print(f"     ... and {len(functions) - 10} more")
        return functions
    except Exception as e:
        if verbose:
            print(f"   [WARN] [WARN] Could not map functions for {os.path.basename(file_path)}: {e}")
        return []


def validate_environment(args):
    """Validate environment and arguments"""
    print("[INFO] [DEBUG] Validating environment...")
    # Check repository path
    if not os.path.exists(args.repo_path):
        print(f"[ERROR] Repository path '{args.repo_path}' does not exist")
        return False

    print(f"[INFO] [DEBUG] Repo path exists: {args.repo_path}")
    # Check for C files in entire repository
    c_files = []
    for root, dirs, files in os.walk(args.repo_path):  # Scan entire repo
        for file in files:
            if file.endswith('.cpp'):
                # Skip files in tests/ directories to avoid processing generated tests
                if 'tests' in root.split(os.sep):
                    continue
                c_files.append(os.path.join(root, file))

    if not c_files:
        print(f"[ERROR] No C++ files found in '{args.repo_path}'")
        return False

    print(f"[INFO] [DEBUG] Found {len(c_files)} files")
    # Check API key only if cloud model is selected
    if args.model in ['gemini', 'groq', 'github']:
        api_key = args.api_key or os.getenv('GEMINI_API_KEY') or os.getenv('GROQ_API_KEY') or os.getenv('GITHUB_TOKEN')
        if not api_key:
            env_var = f"{args.model.upper()}_API_KEY" if args.model != 'github' else "GITHUB_TOKEN"
            print(f"[ERROR] {args.model.title()} model requires API key. Set {env_var} environment variable or use --api-key")
            if args.model == 'gemini':
                print("   Get your API key from: https://makersuite.google.com/app/apikey")
            elif args.model == 'groq':
                print("   Get your API key from: https://console.groq.com/keys")
            elif args.model == 'github':
                print("   Get your GitHub token from: https://github.com/settings/tokens")
            return False
        print(f"[INFO] [DEBUG] API key found for {args.model.title()}")
    else:
        # Ollama selected (no API key required)
        print("[INFO] [DEBUG] Using Ollama -- no API key required")
    return True


def main():
    """Main CLI entry point"""
    # Ensure Unicode output works on Windows consoles that default to a legacy codepage.
    # Prevents crashes when printing emojis / box-drawing characters.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def _approvals_status(argv: list[str]) -> None:
        p = argparse.ArgumentParser(prog="ai-c-testgen status")
        p.add_argument("--repo-path", default=".")
        p.add_argument("--all", action="store_true", help="Show inactive sections too")
        a = p.parse_args(argv)

        repo_root = Path(a.repo_path).resolve()
        reg = ApprovalsRegistry(repo_root)
        reg.load()

        sections = list(reg.iter_sections())
        if not a.all:
            sections = [s for s in sections if s.get("active") is True]

        if not sections:
            print("No sections tracked")
            raise SystemExit(0)

        pending = [s for s in sections if s.get("approved") is not True and s.get("active") is True]

        def _public_label(s: dict) -> str:
            kind = str(s.get("kind") or "base").lower()
            name = str(s.get("name") or "")
            idx = None
            m = re.fullmatch(r"[a-z_]+_(\d+)", name)
            if m:
                try:
                    idx = int(m.group(1))
                except Exception:
                    idx = None

            if kind == "base":
                return "BASE_TESTS"
            if kind == "mcdc":
                return "MCDC_TESTS" + (f" (Decision_{idx})" if idx and idx > 1 else "")
            if kind == "boundary":
                return "BOUNDARY_TESTS"
            if kind == "error_path":
                return "ERROR_PATH_TESTS"
            return "GENERATED_TESTS"

        for s in sorted(sections, key=lambda x: (x.get("test_file_rel", ""), x.get("name", ""))):
            active = "ACTIVE" if s.get("active") is True else "INACTIVE"
            approved = "APPROVED" if s.get("approved") is True else "PENDING"
            print(f"{approved} {active} {_public_label(s)} {s.get('test_file_rel','')}")

        if pending:
            print(f"\nPending approvals (active): {len(pending)}")
            raise SystemExit(2)
        raise SystemExit(0)

    def _approvals_approve(argv: list[str]) -> None:
        p = argparse.ArgumentParser(prog="ai-c-testgen approve")
        p.add_argument("--repo-path", default=".")
        p.add_argument("--file", required=True, help="Source file or test file")
        p.add_argument(
            "--section",
            required=True,
            help="Section label (BASE_TESTS / MCDC_TESTS / BOUNDARY_TESTS / ERROR_PATH_TESTS)",
        )
        p.add_argument("--reviewed-by", required=True)
        a = p.parse_args(argv)

        repo_root = Path(a.repo_path).resolve()
        input_path = (repo_root / a.file).resolve() if not Path(a.file).is_absolute() else Path(a.file).resolve()
        input_rel = repo_relpath(input_path, repo_root)

        requested = (a.section or "").strip()
        if not requested:
            raise SystemExit("--section is required")

        reg = ApprovalsRegistry(repo_root)
        reg.load()

        # Determine which test file contains the section headers.
        # --file may be either the test file or the source file; the registry maps source->test.
        test_file_rel: str | None = None
        if input_rel.startswith("tests/") and (repo_root / input_rel).exists():
            test_file_rel = input_rel
        else:
            for s in reg.iter_sections():
                if not isinstance(s, dict):
                    continue
                if s.get("test_file_rel") == input_rel and s.get("test_file_rel"):
                    test_file_rel = str(s.get("test_file_rel"))
                    break
                if s.get("source_rel") == input_rel and s.get("test_file_rel"):
                    test_file_rel = str(s.get("test_file_rel"))
                    break

        if not test_file_rel:
            raise SystemExit(f"Could not resolve a test file for --file {a.file}")

        test_file = (repo_root / test_file_rel).resolve()
        try:
            test_text = test_file.read_text(encoding="utf-8")
        except Exception as e:
            raise SystemExit(f"Could not read test file: {test_file_rel} ({e})")

        parsed = parse_sections(test_text)
        if not parsed:
            raise SystemExit(f"No AI test sections found in: {test_file_rel}")

        sections_dict = reg.data.setdefault("sections", {})
        if not isinstance(sections_dict, dict):
            raise SystemExit("Invalid approvals registry format (sections is not a dict)")

        label = requested.upper()
        allowed = {"BASE_TESTS", "MCDC_TESTS", "BOUNDARY_TESTS", "ERROR_PATH_TESTS"}
        if label not in allowed:
            raise SystemExit("Unknown section label")

        # Approve all matching sections in this file.
        approved_any = False
        for ps in parsed:
            ps_label = str(ps.meta.get("Section") or "").strip().upper()
            if ps_label != label:
                continue
            section_sha = ps.section_sha256
            entry = sections_dict.get(section_sha)
            if not isinstance(entry, dict):
                continue

            reg.approve(section_sha256=section_sha, reviewed_by=a.reviewed_by)
            approved_any = True

        if not approved_any:
            raise SystemExit("No matching sections found")

        reg.save()

        # Update in-file headers (best-effort).
        try:
            updated = test_text
            for ps in parsed:
                ps_label = str(ps.meta.get("Section") or "").strip().upper()
                if ps_label != label:
                    continue
                section_sha = ps.section_sha256
                reviewed_at_iso = None
                entry = sections_dict.get(section_sha)
                if isinstance(entry, dict):
                    reviewed_at_iso = entry.get("reviewed_at")
                updated = update_section_header(
                    updated,
                    section_sha256=section_sha,
                    approved=True,
                    reviewed_by=a.reviewed_by,
                    reviewed_at_iso=reviewed_at_iso,
                )
            test_file.write_text(updated, encoding="utf-8", newline="\n")
        except Exception:
            pass

        print(f"Approved: {label}")
        raise SystemExit(0)

    def _mcdc_generate(argv: list[str]) -> None:
        p = argparse.ArgumentParser(prog="ai-c-testgen mcdc-generate")
        p.add_argument("--repo-path", default=".")
        p.add_argument("--output", default="tests")
        p.add_argument("--source-dir", default="src")
        p.add_argument("--file", required=True, help="Target source file name (e.g., Interlocking.cpp)")
        p.add_argument(
            '--safety-level',
            choices=list(SafetyPolicy.allowed_levels()),
            default='QM',
        )
        p.add_argument('--policy-file', default=None)
        p.add_argument('--disable-mcdc', action='store_true')
        p.add_argument(
            "--model",
            choices=['ollama', 'gemini', 'groq', 'github'],
            default='gemini',
        )
        p.add_argument("--api-key", default=None)
        p.add_argument("--max-api-retries", type=int, default=5)
        p.add_argument("--redact-sensitive", action='store_true')
        p.add_argument("--enable-gmock", action='store_true')
        a = p.parse_args(argv)

        repo_root = Path(a.repo_path).resolve()

        policy = SafetyPolicy.load(
            safety_level=a.safety_level,
            repo_root=repo_root,
            policy_file=a.policy_file,
            disable_mcdc=bool(a.disable_mcdc),
        )

        # Locate source file under --source-dir.
        src_dir = (repo_root / a.source_dir).resolve()
        src_file = src_dir / a.file
        if not src_file.exists():
            raise SystemExit(f"Source file not found: {src_file}")

        # Load mcdc gaps.
        gaps_path = repo_root / 'tests' / 'analysis' / 'mcdc_gaps.json'
        if not gaps_path.exists():
            raise SystemExit("mcdc_gaps.json not found. Run: ai-c-test-analyzer --repo-path <repo> --mcdc")

        gaps = json.loads(gaps_path.read_text(encoding='utf-8'))
        decisions = (gaps or {}).get('files', {}).get(repo_relpath(src_file, repo_root), [])
        if not decisions:
            print("No MC/DC candidate decisions found for this file.")
            raise SystemExit(0)

        # API key resolution.
        api_key = None
        if a.model == 'gemini':
            api_key = a.api_key or os.getenv('GEMINI_API_KEY')
        elif a.model == 'groq':
            api_key = a.api_key or os.getenv('GROQ_API_KEY')
        elif a.model == 'github':
            api_key = a.api_key or os.getenv('GITHUB_TOKEN')
        else:
            api_key = a.api_key or ""

        try:
            generator = SmartTestGenerator(
                api_key=api_key or "",
                repo_path=str(repo_root),
                redact_sensitive=bool(a.redact_sensitive),
                max_api_retries=int(a.max_api_retries),
                model_choice=str(a.model),
                enable_gmock=bool(a.enable_gmock),
                safety_policy=policy,
            )
        except Exception as e:
            print(f"❌ Failed to initialize model '{a.model}': {e}")
            raise SystemExit(1)

        repo_scan = generator.build_dependency_map(str(repo_root))
        last_test_file: str | None = None
        appended = 0
        failures: list[str] = []

        for idx, d in enumerate(decisions, start=1):
            try:
                # One appended section per decision for clear mapping + independent approvals.
                result = generator.generate_tests_for_file(
                    str(src_file),
                    str(repo_root),
                    str(Path(a.output)),
                    repo_scan,
                    validation_feedback=None,
                    mcdc_decisions=[d],
                    section_kind="mcdc",
                    decision_meta=d,
                )
            except Exception as e:
                msg = str(e)
                failures.append(f"decision {idx}/{len(decisions)}: {msg}")
                # If we got rate-limited, stop here to avoid hammering the API.
                if "429" in msg or "Too Many Requests" in msg:
                    break
                continue

            if not result.get('success'):
                failures.append(f"decision {idx}/{len(decisions)}: generation returned success=false")
                continue

            appended += 1
            last_test_file = result.get('test_file')

        if appended == 0:
            if failures:
                print("❌ MC/DC generation failed. First error:")
                print(f"   {failures[0]}")
            raise SystemExit(1)

        # Partial success is still useful: sections were appended and approvals were updated.
        summary = f"✅ MC/DC sections appended to: {last_test_file} ({appended}/{len(decisions)} decisions)"
        print(summary)
        if failures:
            print("⚠️ Some decisions were not generated:")
            for line in failures[:5]:
                print(f"   - {line}")
            if len(failures) > 5:
                print(f"   - ... and {len(failures) - 5} more")
            print("ℹ️ Tip: re-run later to generate the remaining decisions (rate limits may apply).")

        raise SystemExit(0)

    # Lightweight subcommands without breaking legacy flags.
    if len(sys.argv) > 1 and sys.argv[1] in ("status", "approve", "mcdc-generate", "plan"):
        cmd = sys.argv[1]
        rest = sys.argv[2:]
        if cmd == "status":
            _approvals_status(rest)
        if cmd == "approve":
            _approvals_approve(rest)
        if cmd == "mcdc-generate":
            _mcdc_generate(rest)
        if cmd == "plan":
            _plan(rest)

    print(f"\n{Colors.BOLD}{'='*70}")
    print(f"   AI-ASSISTED UNIT TEST GENERATION - LIVE DEMO")
    print(f"{'='*70}{Colors.ENDC}\n")

    print("[INFO] [DEBUG] CLI started, parsing args...")
    parser = create_parser()
    args = parser.parse_args()
    print(f"[INFO] [DEBUG] Args parsed: repo_path={args.repo_path}, file={getattr(args, 'file', None)}, verbose={args.verbose}")

    if not validate_environment(args):
        print("[ERROR] [DEBUG] Environment validation failed")
        sys.exit(1)

    # Handle Analysis Mode
    if args.mode == 'analyze':
        print("[START] AI C Test Generator - Analysis Mode")
        print(f"   Repository: {args.repo_path}")
        
        try:
            # Find repo root (parent of repo_path if repo_path is a subdirectory)
            repo_root = args.repo_path
            if os.path.exists(os.path.join(args.repo_path, '.git')):
                repo_root = args.repo_path
            else:
                # Try to find git root by going up directories
                current = args.repo_path
                for _ in range(3):  # Go up max 3 levels
                    parent = os.path.dirname(current)
                    if os.path.exists(os.path.join(parent, '.git')):
                        repo_root = parent
                        break
                    current = parent

            analyzer = DependencyAnalyzer(args.repo_path, base_path=repo_root)
            scan_results = analyzer.perform_repo_scan()
            
            # Save to JSON
            output_path = args.output
            # If output is relative, make it relative to repo_root
            if not os.path.isabs(output_path):
                output_path = os.path.join(repo_root, output_path)
                
            if not output_path.endswith('.json'):
                 output_path = os.path.join(output_path, 'analysis', 'analysis.json')
            
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            
            # Convert sets to lists for JSON serialization
            def set_default(obj):
                if isinstance(obj, set):
                    return list(obj)
                raise TypeError
                
            with open(output_path, 'w') as f:
                json.dump(scan_results, f, default=set_default, indent=2)
                
            print(f"✅ Analysis saved to {output_path}")
            sys.exit(0)
        except Exception as e:
            print(f"[ERROR] Analysis failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)

    # Generate Mode - API Key Validation
    api_key = None
    if args.model == 'gemini':
        api_key = args.api_key or os.getenv('GEMINI_API_KEY')
        if not api_key:
            print("[ERROR] Gemini model requires API key. Set GEMINI_API_KEY environment variable or use --api-key")
            print("   Get your API key from: https://makersuite.google.com/app/apikey")
            sys.exit(1)
    elif args.model == 'groq':
        api_key = args.api_key or os.getenv('GROQ_API_KEY')
        if not api_key:
            print("[ERROR] Groq model requires API key. Set GROQ_API_KEY environment variable or use --api-key")
            print("   Get your API key from: https://console.groq.com/keys")
            sys.exit(1)
    elif args.model == 'github':
        api_key = args.api_key or os.getenv('GITHUB_TOKEN')
        if not api_key:
            print("[ERROR] GitHub model requires token. Set GITHUB_TOKEN environment variable or use --api-key")
            print("   Get your GitHub token from: https://github.com/settings/tokens")
            sys.exit(1)
    elif args.model == 'ollama':
        # Ollama doesn't need API key
        pass
    else:
        print(f"[ERROR] Invalid model: {args.model}. Choose 'ollama', 'gemini', 'groq', or 'github'")
        sys.exit(1)

    print(f"[INFO] [DEBUG] API key found: {'Yes' if api_key else 'No (using Ollama)'}")

    print("[START] AI C Test Generator")
    print(f"   Repository: {args.repo_path}")
    print(f"   Source dir: {args.source_dir}")
    print(f"   Output dir: {args.output}")
    print()

    import logging
    try:
        # Initialize components
        print("[INFO] [INFO] Initializing AI model and validator...")
        # Setup simple logging to file if requested
        if args.log_file:
            log_path = args.log_file
            if not os.path.isabs(log_path):
                # Make the log path relative to the repo root
                log_path = os.path.join(args.repo_path, log_path)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            logging.basicConfig(filename=log_path, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            # Mirror starting line to logging as well
            logging.info("AI C Test Generator started")
        repo_root = Path(args.repo_path).resolve()
        policy = SafetyPolicy.load(
            safety_level=args.safety_level,
            repo_root=repo_root,
            policy_file=args.policy_file,
            disable_mcdc=bool(args.disable_mcdc),
        )

        generator = SmartTestGenerator(
            api_key,
            args.repo_path,
            redact_sensitive=args.redact_sensitive,
            max_api_retries=args.max_api_retries,
            model_choice=args.model,
            enable_gmock=args.enable_gmock,
            safety_policy=policy,
        )
        print(f"🤖 [MODEL] Using AI model: {generator.current_model_name}")
        validator = TestValidator(args.repo_path)

        # Build repo scan
        repo_scan = {}
        if args.analysis_file:
            print(f"[INFO] Loading analysis from {args.analysis_file}...", flush=True)
            try:
                with open(args.analysis_file, 'r') as f:
                    repo_scan = json.load(f)
                print(f"[INFO] Loaded analysis for {len(repo_scan.get('function_index', {}))} functions", flush=True)
                
                # Convert lists back to sets for hardware_flags if needed (though generator handles lists now)
                # But let's be safe and ensure consistency if generator expects sets in some places
                if 'hardware_flags' in repo_scan:
                    for func, flags in repo_scan['hardware_flags'].items():
                        if isinstance(flags, list):
                            repo_scan['hardware_flags'][func] = set(flags)
                            
            except Exception as e:
                print(f"[ERROR] Failed to load analysis file: {e}")
                sys.exit(1)
        elif not args.file:
            if args.verbose:
                print("[INFO] [DEBUG] Performing repo scan...", flush=True)
            repo_scan = generator.build_dependency_map(args.repo_path)
            if args.verbose:
                print(f"[INFO] [DEBUG] Repo scan complete ({len(repo_scan.get('function_index', {}))} functions indexed)", flush=True)
        else:
            print("[INFO] [DEBUG] Skipping global repo scan for single file mode", flush=True)

        # Log analysis source prominently
        if args.analysis_file:
            print(f"[ANALYSIS] Loaded from file: {args.analysis_file}")
        elif not args.file:
            analysis_path = os.path.join(args.repo_path, 'tests', 'analysis', 'analysis.json')
            if os.path.exists(analysis_path):
                print(f"[ANALYSIS] Reused cached analysis: {analysis_path}")
            else:
                print("[ANALYSIS] Performed fresh repo scan (no cache found)")
        else:
            print("[ANALYSIS] Single-file mode (no global analysis)")

        def _resolve_selected_file() -> str:
            """Resolve args.file to an absolute path.

            Accepts:
            - absolute paths
            - paths relative to repo root
            - paths relative to repo/src (common when callers omit 'src/')
            - paths relative to --source-dir
            """
            if not args.file:
                return ""

            candidate_inputs = [args.file]
            # If user provided a Windows-style path in a quoted arg, it may contain '/' or '\\'.
            # os.path.normpath will normalize either.
            candidate_inputs = [os.path.normpath(p) for p in candidate_inputs]

            bases = []
            bases.append(args.repo_path)
            bases.append(os.path.join(args.repo_path, "src"))
            if args.source_dir:
                bases.append(os.path.join(args.repo_path, os.path.normpath(args.source_dir)))

            # 1) Direct absolute path
            for raw in candidate_inputs:
                if os.path.isabs(raw) and os.path.exists(raw):
                    return os.path.abspath(raw)

            # 2) Relative to known bases
            for raw in candidate_inputs:
                for base in bases:
                    candidate = os.path.abspath(os.path.join(base, raw))
                    if os.path.exists(candidate):
                        return candidate


            def _plan(argv: list[str]) -> None:
                """Run planner-only: produce work/scenarios.json using the planner LLM role."""
                import argparse as _arg
                p = _arg.ArgumentParser(prog='ai-c-testgen plan')
                p.add_argument('--repo-path', required=True)
                p.add_argument('--analysis-file', default=None)
                p.add_argument('--model', default='ollama', choices=['ollama', 'gemini', 'groq', 'github'])
                p.add_argument('--verbose', action='store_true')
                args = p.parse_args(argv)

                repo_root = Path(args.repo_path).resolve()
                # Load analysis
                analysis = {}
                if args.analysis_file:
                    try:
                        analysis = json.loads(Path(args.analysis_file).read_text(encoding='utf-8'))
                    except Exception as e:
                        print(f"[ERROR] Failed to load analysis file: {e}")
                        raise SystemExit(1)
                else:
                    # Try canonical work/analysis.json
                    cand = repo_root / 'work' / 'analysis.json'
                    legacy = repo_root / 'tests' / 'analysis' / 'analysis.json'
                    if cand.exists():
                        analysis = json.loads(cand.read_text(encoding='utf-8'))
                    elif legacy.exists():
                        analysis = json.loads(legacy.read_text(encoding='utf-8'))
                    else:
                        print("[ERROR] No analysis.json found; run analysis mode first or pass --analysis-file")
                        raise SystemExit(2)

                print("[PLAN] Running planner to produce work/scenarios.json...")
                try:
                    from .planner import Planner
                    planner = Planner(str(repo_root), model_choice=args.model)
                    scenarios = planner.plan(analysis)
                    print(f"[PLAN] Wrote work/scenarios.json with {len(scenarios.get('scenarios', []))} scenarios")
                    raise SystemExit(0)
                except SystemExit:
                    raise
                except Exception as e:
                    print(f"[ERROR] Planner failed: {e}")
                    if args.verbose:
                        import traceback
                        traceback.print_exc()
                    raise SystemExit(1)

            # 3) Fallback: match by basename within scan scope (only if unique)
            wanted_base = os.path.basename(candidate_inputs[0])
            matches = []
            for base in bases:
                if not os.path.isdir(base):
                    continue
                for root, _, files in os.walk(base):
                    for fname in files:
                        if fname == wanted_base:
                            matches.append(os.path.abspath(os.path.join(root, fname)))
            matches = sorted(set(matches))
            if len(matches) == 1:
                return matches[0]

            if len(matches) > 1:
                raise RuntimeError(
                    f"--file '{args.file}' is ambiguous (found {len(matches)} matches). "
                    f"Provide a path relative to repo root or an absolute path."
                )

            raise RuntimeError(
                f"--file '{args.file}' not found. Provide a path relative to repo root (or src/), "
                f"or an absolute path."
            )

        selected_file_abs = ""
        if args.file:
            selected_file_abs = _resolve_selected_file()
            try:
                rel_selected = os.path.relpath(selected_file_abs, args.repo_path)
            except Exception:
                rel_selected = selected_file_abs
            print(f"[INFO] [DEBUG] Single-file target resolved to: {rel_selected}", flush=True)

        # Find C++ files under the configured source directory (excluding tests/, build/, and CMakeFiles/ directories)
        scan_root = os.path.join(args.repo_path, os.path.normpath(args.source_dir)) if args.source_dir else args.repo_path
        if not os.path.isdir(scan_root):
            if args.verbose:
                print(f"[WARN] Source dir '{args.source_dir}' not found. Falling back to repo root scan.", flush=True)
            scan_root = args.repo_path

        c_files = []
        for root, dirs, files in os.walk(scan_root):
            for file in files:
                if file.endswith('.cpp'):  # Process .cpp files only, not .c or headers
                    # Skip files in tests/, build/, CMakeFiles/, ai_test_build/ directories to avoid processing generated files
                    if any(skip_dir in root.split(os.sep) for skip_dir in ['tests', 'build', 'CMakeFiles', 'ai_test_build']):
                        if args.verbose:
                            print(f"[SKIP] Skipping {os.path.join(root, file)} (in build/generated directory)")
                        continue
                    # Skip main.cpp as it's not suitable for unit testing
                    if file == 'main.cpp':
                        if args.verbose:
                            print(f"[SKIP] Skipping {file} (application entry point)")
                        continue
                    # Skip files starting with test_ as they are generated test files
                    if file.startswith('test_'):
                        if args.verbose:
                            print(f"[SKIP] Skipping {file} (generated test file)")
                        continue
                    c_files.append(os.path.join(root, file))

        # If a specific file was requested, restrict the list now (so we don't print/process others)
        if selected_file_abs:
            # Basic sanity: only allow .cpp in this generator mode.
            if not selected_file_abs.endswith('.cpp'):
                raise RuntimeError(f"Selected file must be a .cpp file, got: {args.file}")
            c_files = [selected_file_abs]

        if args.verbose:
            print(f"[INFO] Found {len(c_files)} C/C++ files to process")

        # Map functions for all files if verbose
        if args.verbose:
            print("[INFO] [INFO] Mapping functions for all files...")
            for file_path in c_files:
                list_functions_for_file(file_path, args.repo_path, verbose=True)

        # Track output directories where we actually write artifacts.
        output_dirs = set()

        # Clean up old verification and compilation reports (unless disabled)
        if not args.no_cleanup_reports:
            print("[CLEAN] Cleaning up old verification and compilation reports...")
            try:
                import shutil
                # Clean up all report-related directories and files in all potential test directories
                for root, dirs, files in os.walk(args.repo_path):
                    if 'tests' in dirs:
                        tests_dir = os.path.join(root, 'tests')
                        if os.path.exists(tests_dir):
                            # Clean up report directories in each tests folder
                            for item in os.listdir(tests_dir):
                                item_path = os.path.join(tests_dir, item)
                                if os.path.isdir(item_path):
                                    # Clean directories that match report patterns
                                    if any(pattern in item.lower() for pattern in ['compilation_report', 'validation_report', 'reports', 'logs']):
                                        try:
                                            shutil.rmtree(item_path)
                                            print(f"   [DEL] Removed directory: {os.path.relpath(item_path, args.repo_path)}")
                                        except (OSError, PermissionError) as e:
                                            print(f"   [WARN] Could not remove directory {item}: {e}")
                                elif os.path.isfile(item_path):
                                    # Clean files that match report patterns
                                    if any(pattern in item.lower() for pattern in ['report', 'log', 'validation', 'compilation']):
                                        try:
                                            os.remove(item_path)
                                            print(f"   [DEL] Removed file: {os.path.relpath(item_path, args.repo_path)}")
                                        except (OSError, PermissionError) as e:
                                            print(f"   [WARN] Could not remove file {item}: {e}")
            except Exception as e:
                print(f"[WARN] Error during cleanup: {e}")
        else:
            print("[SKIP] Skipping cleanup of old reports (--no-cleanup-reports enabled)")

        # Process each file
        successful_generations = 0
        validation_reports = []
        regeneration_stats = {'total_regenerations': 0, 'successful_regenerations': 0}

        # Mandatory Human Review Gate inputs
        generated_test_files = []  # absolute paths
        skipped_functions_by_file = {}  # rel_source_path -> list[{name, reason, ...}]
        detected_hardware_deps = set()  # strings

        # Determine output directory (central tests/ directory at repo root)
        # Find repo root (parent of repo_path if repo_path is a subdirectory)
        repo_root = args.repo_path
        if os.path.exists(os.path.join(args.repo_path, '.git')):
            repo_root = args.repo_path
        else:
            # Try to find git root by going up directories
            current = args.repo_path
            for _ in range(3):  # Go up max 3 levels
                parent = os.path.dirname(current)
                if os.path.exists(os.path.join(parent, '.git')):
                    repo_root = parent
                    break
                current = parent
        
        output_dir = os.path.join(repo_root, args.output)
        os.makedirs(output_dir, exist_ok=True)
        output_dirs.add(output_dir)

        for file_path in c_files:
            rel_path = os.path.relpath(file_path, args.repo_path)
            print(f"[PROC] Processing: {rel_path}")

            # Check if valid test already exists
            if args.skip_if_valid:
                # Determine expected test file path
                file_name = os.path.basename(file_path)
                base_name = os.path.splitext(file_name)[0]
                # We assume .cpp extension for generated tests as we are using GTest
                test_file_name = f"test_{base_name}.cpp" 
                test_file_path = os.path.join(output_dir, test_file_name)
                
                if os.path.exists(test_file_path):
                    if args.verbose:
                        print(f"   [CHECK] Checking existing test file: {test_file_name}")
                    
                    # Validate existing file
                    try:
                        validation_result = validator.validate_test_file(test_file_path, file_path)
                        
                        if validation_result['compiles'] and validation_result['realistic']:
                            print(f"   [SKIP] Valid test file already exists: {test_file_name} ({validation_result['quality']} quality)")
                            successful_generations += 1
                            continue
                        else:
                            if args.verbose:
                                print(f"   [INFO] Existing test file is invalid or unrealistic. Regenerating...")
                    except Exception as e:
                        if args.verbose:
                            print(f"   [WARN] Could not validate existing file: {e}")

            # Map functions for this file
            functions = list_functions_for_file(file_path, args.repo_path, args.verbose)
            if not functions:
                print(f"   [WARN] [WARN] No functions found in {rel_path} - skipping test generation")
                continue

            # MANDATORY HUMAN REVIEW GATE CONTRACT:
            # - Do NOT auto-fix or regenerate
            # - Exactly one generation attempt per file
            attempt = 1
            successful_results = []
            final_validation = None

            try:
                # Generate tests for this file
                result = generator.generate_tests_for_file(
                    file_path, args.repo_path, output_dir, repo_scan, None
                )

                if not result.get('success'):
                    reason = result.get('reason') or result.get('error') or 'unknown_error'
                    print(f"   [ERROR] Generation failed: {reason}")
                    # Still record skipped functions/hardware deps for review.
                    skipped_functions_by_file[rel_path] = result.get('skipped_functions', [])
                    for dep in result.get('hardware_dependencies', []) or []:
                        detected_hardware_deps.add(str(dep))
                    continue

                # Record review metadata
                if result.get('test_file'):
                    generated_test_files.append(result['test_file'])
                skipped_functions_by_file[rel_path] = result.get('skipped_functions', [])
                for dep in result.get('hardware_dependencies', []) or []:
                    detected_hardware_deps.add(str(dep))
                for dep in result.get('functions_that_need_stubs', []) or []:
                    detected_hardware_deps.add(str(dep))

                # Validate the generated test (validation is allowed; no regeneration is performed)
                if args.verbose:
                    print(f"   [CHECK] Validating (single attempt)...")
                validation_result = validator.validate_test_file(result['test_file'], file_path)

                successful_results.append((result, validation_result))
                final_validation = validation_result

            except Exception as e:
                print(f"   [ERROR] Error processing {rel_path}: {str(e)}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                continue

            # Process successful results - keep the last successful one
            if successful_results:
                final_result, final_validation = successful_results[-1]  # Keep the last (potentially best) result
                successful_generations += 1
                validation_reports.append(final_validation)

                print(f"   [PASS] [OK] Final: {os.path.basename(final_result['test_file'])} ({final_validation['quality']} quality)")
            else:
                print(f"   [ERROR] [ERROR] Failed to generate acceptable test for {rel_path}")

        # Save validation reports
        if validation_reports:
            print(f"\n[STATS] [POST] Saving validation reports...")
            for output_dir in output_dirs:
                report_dir = os.path.join(output_dir, "compilation_report")
                os.makedirs(report_dir, exist_ok=True)
                # Save reports for files in this output directory
                for report in validation_reports:
                    # Check if this report belongs to a file in this output_dir
                    test_file_path = report.get('test_file', '')
                    if test_file_path.startswith(output_dir):
                        validator.save_validation_report(report, report_dir)

        # Print summary
        print(f"\n[DONE] [DONE] COMPLETED!")
        total_targets = len(c_files)
        print(f"✅ [OK] Generated: {successful_generations}/{total_targets} file(s)")

        # Only claim output locations when something was actually produced.
        if successful_generations > 0 or validation_reports or generated_test_files:
            print(f"   [SAVE] [SAVE] Tests saved to:")
            for out_dir in sorted(output_dirs):
                rel_dir = os.path.relpath(out_dir, args.repo_path)
                print(f"     - {rel_dir}")
            if validation_reports:
                print(f"   [SAVE] [SAVE] Reports saved in respective test directories")

        if args.regenerate_on_low_quality:
            print("⚠️ [WARN] Auto-regeneration is DISABLED by the mandatory human review gate.")

        # Check quality of all generated tests
        quality_levels = {'low': 0, 'medium': 1, 'high': 2}
        threshold_quality_level = quality_levels.get(args.quality_threshold.lower(), 2)

        low_quality_tests = []
        for report in validation_reports:
            current_quality_level = quality_levels.get(report['quality'].lower(), 0)
            if current_quality_level < threshold_quality_level:
                low_quality_tests.append(report['file'])

        if low_quality_tests:
            if args.regenerate_on_low_quality:
                # When regeneration is enabled, warn but don't fail
                print(f"[WARN] [WARN] {len(low_quality_tests)} test(s) still below {args.quality_threshold} quality threshold after regeneration:")
                for test_file in low_quality_tests:
                    print(f"   [WARN] [WARN] - {test_file}")
                print("[TIP] [INFO] Consider increasing --max-regeneration-attempts or relaxing --quality-threshold")
            else:
                # When regeneration is disabled, just warn instead of failing
                print(f"[WARN] [WARN] {len(low_quality_tests)} test(s) failed to meet {args.quality_threshold} quality threshold:")
                for test_file in low_quality_tests:
                    print(f"   [WARN] [WARN] - {test_file}")
                print("[TIP] [INFO] Use --regenerate-on-low-quality to automatically improve test quality")
                # sys.exit(1)  <-- DISABLED: Do not fail on low quality

        # Overall success check - only fail if no tests were generated at all
        if successful_generations == 0:
            print("[ERROR] [ERROR] No tests were successfully generated")
            sys.exit(1)
        elif successful_generations < total_targets:
            print(f"[WARN] [WARN] {successful_generations}/{total_targets} file(s) successfully generated tests - check validation reports")
            print("[TIP] [INFO] Some files failed to generate tests (likely due to API timeouts or other issues)")
            # Don't exit with error - allow CI/CD to continue with partial success

        # If log file is enabled, flush a final line for visibility
        if args.log_file:
            try:
                logging.info("Process finished")
            except Exception:
                pass

        # Optionally wait before exiting to keep the terminal visible
        if args.wait_before_exit:
            try:
                _ = input("Press ENTER to exit and return to the shell...\n")
            except Exception:
                pass

        # Mandatory Human Review Gate: write review artifacts and STOP.
        review_dir = os.path.join(output_dir, "review")
        os.makedirs(review_dir, exist_ok=True)

        # Review artifacts
        review_required_path = os.path.join(review_dir, "review_required.md")
        hardware_deps_path = os.path.join(review_dir, "hardware_dependencies.txt")
        skipped_path = os.path.join(review_dir, "skipped_functions.txt")

        # Normalize paths for readability
        repo_root_abs = os.path.abspath(repo_root)
        generated_rel = [os.path.relpath(os.path.abspath(p), repo_root_abs) for p in generated_test_files]
        generated_rel = [p.replace('\\', '/') for p in generated_rel]

        with open(hardware_deps_path, "w", encoding="utf-8") as f:
            for dep in sorted(detected_hardware_deps):
                f.write(f"{dep}\n")

        with open(skipped_path, "w", encoding="utf-8") as f:
            for src_rel, skipped_list in sorted(skipped_functions_by_file.items()):
                if not skipped_list:
                    continue
                f.write(f"{src_rel}\n")
                for item in skipped_list:
                    name = item.get('name', '').strip()
                    reason = item.get('reason', '').strip()
                    if name:
                        f.write(f"  - {name}: {reason}\n")

        with open(review_required_path, "w", encoding="utf-8") as f:
            f.write("# Manual Review Required\n\n")
            f.write("This repository contains AI-generated test code. **Human review is mandatory before any build/test execution.**\n\n")
            f.write("## Generated test files\n")
            if generated_rel:
                for p in generated_rel:
                    f.write(f"- {p}\n")
            else:
                f.write("- (none)\n")

            f.write("\n## Skipped functions (with reasons)\n")
            any_skipped = any(bool(v) for v in skipped_functions_by_file.values())
            if any_skipped:
                for src_rel, skipped_list in sorted(skipped_functions_by_file.items()):
                    if not skipped_list:
                        continue
                    f.write(f"- {src_rel}\n")
                    for item in skipped_list:
                        name = item.get('name', '').strip()
                        reason = item.get('reason', '').strip()
                        if name:
                            f.write(f"  - {name}: {reason}\n")
            else:
                f.write("- (none)\n")

            f.write("\n## Hardware dependencies detected\n")
            if detected_hardware_deps:
                for dep in sorted(detected_hardware_deps):
                    f.write(f"- {dep}\n")
            else:
                f.write("- (none)\n")

            f.write("\n## Known limitations / assumptions\n")
            f.write("- Generated tests are AI-produced and may contain incorrect assumptions; review is required.\n")
            f.write("- No compilation/build/test execution is performed until approval is recorded.\n")
            f.write("- Hardware-dependent behavior is not simulated; hardware-touching functions may be skipped or require stubs/mocks.\n")
            f.write("\n## Approval gate\n")
            f.write("Create an approval file for EACH generated test file before building or running tests:\n\n")
            if generated_rel:
                review_dir_rel = os.path.relpath(review_dir, repo_root_abs).replace('\\', '/')
                f.write("Required approval files (preferred; mirrors project structure under tests/):\n")
                for p in generated_rel:
                    # Preferred: strip leading tests/ so approvals mirror the repo layout directly.
                    # Example: tests/src/app/Foo/test_Bar.cpp -> tests/review/src/app/Foo/test_Bar.cpp.flag
                    p_norm = p.replace('\\', '/').lstrip('/')
                    if p_norm.startswith('tests/'):
                        p_norm = p_norm[len('tests/'):]
                    f.write(f"- {review_dir_rel}/{p_norm}.flag\n")

                f.write("\nAlso accepted (back-compat):\n")
                for p in generated_rel:
                    # Previous mirrored scheme kept the full repo-relative test path under review/.
                    f.write(f"- {review_dir_rel}/{p}.flag\n")
                for p in generated_rel:
                    approval_name = f"APPROVED.{os.path.basename(p)}.flag"
                    f.write(f"- {review_dir_rel}/{approval_name}\n")
                f.write("\n")
            f.write("Each approval file contents must be exactly:\n\n")
            f.write("approved = true\n")
            f.write("reviewed_by = <human_name>\n")
            f.write("date = <ISO date>\n")

        print("🎉 [SUCCESS] Test generation completed!")
        print(f"   [INFO] Review artifact written: {os.path.relpath(review_required_path, repo_root)}")
        print("⛔ Manual review required before build.")
        sys.exit(0)

    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Fatal error: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()