import subprocess
import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import re


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_cmd(cmd: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        out = stdout + stderr
        result = {"success": proc.returncode == 0, "rc": proc.returncode, "output": out, "stdout": stdout, "stderr": stderr}

        # Try to parse JSON from stdout if present (many tools can emit JSON)
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                result.update(parsed)
        except Exception:
            pass

        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def _run_full_analyzer(repo_path: Path, root: Path, safety_level: Optional[str]) -> Dict[str, Any]:
    """Invoke ai_c_test_analyzer CLI to mirror run_demo analysis output."""
    analyzer_root = root / 'CW_Test_Analyzer'
    if not analyzer_root.exists():
        return {"success": False, "error": f"CW_Test_Analyzer not found at {analyzer_root}"}

    cmd = [
        sys.executable,
        '-m', 'ai_c_test_analyzer.cli',
        '--repo-path', str(repo_path),
    ]
    level = (safety_level or 'SIL2').strip()
    if level:
        cmd.extend(['--safety-level', level])
    policy_file = root / 'safety_policy.yaml'
    if policy_file.exists():
        cmd.extend(['--policy-file', str(policy_file)])
    cmd.append('--disable-mcdc')

    return _run_cmd(cmd, cwd=analyzer_root)


def analyze_repo(repo: str, workspace_root: Optional[Path] = None, safety_level: Optional[str] = None) -> Dict[str, Any]:
    """Run repository analysis. Mirrors run_demo pipeline (CW_Test_Analyzer) when available."""
    root = Path(workspace_root or _root())
    repo_path = (root / repo).resolve()
    if not repo_path.exists():
        return {"success": False, "error": f"repo not found: {repo_path}"}

    analysis_path = repo_path / 'tests' / 'analysis' / 'analysis.json'
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []

    analyzer_result = _run_full_analyzer(repo_path, root, safety_level or 'SIL2')
    analyzer_success = analyzer_result.get('success') if isinstance(analyzer_result, dict) else False
    if not analyzer_success:
        msg = (analyzer_result or {}).get('error') or (analyzer_result or {}).get('stderr') or (analyzer_result or {}).get('output')
        if msg:
            warnings.append(f"Full analyzer unavailable, falling back to lightweight scan: {msg.splitlines()[0][:200]}")
        else:
            warnings.append("Full analyzer unavailable, falling back to lightweight scan")

    if analysis_path.exists():
        try:
            text = analysis_path.read_text(encoding='utf-8')
            try:
                parsed = json.loads(text)
                result = {"success": True, "source": "analyzer" if analyzer_success else "artifact", "path": str(analysis_path), "analysis": parsed}
                if warnings and not analyzer_success:
                    result['warnings'] = warnings
                return result
            except Exception:
                result = {"success": True, "source": "analyzer" if analyzer_success else "artifact", "path": str(analysis_path), "output": text[:4096]}
                if warnings and not analyzer_success:
                    result['warnings'] = warnings
                return result
        except Exception as e:
            warnings.append(f"Failed to read analyzer output: {e}")

    # Fallback: try a lightweight in-repo scan and produce a minimal analysis artifact.
    try:
        # Prefer scanning only the project's `src/` directory to avoid picking up
        # `tests/.../src` folders. If `src/` does not exist, fall back to whole repo.
        search_root = repo_path / 'src'
        if not search_root.exists():
            search_root = repo_path

        # Collect C/C++ source files under the chosen search root
        src_files = list(search_root.rglob('*.c')) + list(search_root.rglob('*.cpp')) + list(search_root.rglob('*.h')) + list(search_root.rglob('*.hpp'))
        file_index = {}
        function_index = {}

        import re
        fn_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_:<>~]*)\s*\([^;\)]*\)\s*\{", re.MULTILINE)

        for f in src_files:
            try:
                text = f.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                text = ''
            rel = f.relative_to(repo_path).as_posix()
            file_index[rel] = {
                "path": rel,
                "language": 'cpp' if f.suffix in ('.cpp', '.c') else 'c/cpp',
                "is_header": f.suffix in ('.h', '.hpp')
            }

            # find simple function definitions
            for m in fn_re.finditer(text):
                name = m.group(1)
                function_index[name] = {"name": name, "file": rel, "touches_hardware": False}

            # heuristic: detect hardware-related tokens
            lower = text.lower()
            hw = any(x in lower for x in ('hal', 'gpio', 'port', 'digital', 'adc', 'spi', 'i2c', 'uart', 'readreg', 'writereg'))
            file_index[rel]['touches_hardware'] = hw

        analysis = {"file_index": file_index, "function_index": function_index}

        # persist artifact for repeatability
        try:
            analysis_dir = analysis_path.parent
            analysis_dir.mkdir(parents=True, exist_ok=True)
            analysis_path.write_text(json.dumps(analysis, indent=2), encoding='utf-8')
            result = {"success": True, "source": "generated", "path": str(analysis_path), "analysis": analysis}
            if warnings:
                result['warnings'] = warnings
            return result
        except Exception as exc:
            return {"success": False, "error": f"failed to write analysis artifact: {exc}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def _light_validate_scenarios(scenarios: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Basic validation when analysis.json is not available: check .cpp and expected present."""
    valid: List[Dict[str, Any]] = []
    rejections: List[Dict[str, str]] = []

    def norm(p: Any) -> str:
        try:
            return str(p).replace('\\', '/').strip()
        except Exception:
            return ''

    for s in scenarios:
        sid = s.get('scenario_id') or s.get('id') or '<unknown>'
        f = norm(s.get('file'))
        if not f or not f.endswith('.cpp'):
            rejections.append({'scenario_id': sid, 'reason': 'file is not a .cpp implementation'})
            continue
        expected = s.get('expected_behavior') or s.get('expected')
        if not expected:
            rejections.append({'scenario_id': sid, 'reason': 'missing expected behavior'})
            continue
        # Accept
        valid.append(s)

    return valid, rejections


def _validate_scenarios_against_analysis(repo_path: Path, scenarios: List[Dict[str, Any]], analysis: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Validate scenarios deterministically against analysis.json.

    Returns (valid_scenarios, rejections) where each rejection is a dict with
    keys: 'scenario_id' and 'reason'.
    """
    valid: List[Dict[str, Any]] = []
    rejections: List[Dict[str, str]] = []

    fn_index = analysis.get('function_index', {}) or {}
    file_index = analysis.get('file_index', {}) or {}

    def norm(p: str) -> str:
        return p.replace('\\', '/').strip()

    # Normalize file_index keys to forward slashes
    file_index_norm = {norm(k): v for k, v in file_index.items()}

    # Build reverse map: file -> set(function names)
    file_to_fns = {}
    for fname, finfo in fn_index.items():
        f = norm(finfo.get('file', ''))
        file_to_fns.setdefault(f, set()).add(fname)

    for s in scenarios:
        sid = s.get('scenario_id') or s.get('id') or '<unknown>'
        # Basic required fields
        fpath = s.get('file')
        if not fpath:
            rejections.append({'scenario_id': sid, 'reason': 'missing file field'})
            continue
        fpath_n = norm(fpath)
        if not fpath_n.endswith('.cpp'):
            rejections.append({'scenario_id': sid, 'reason': 'file must be a .cpp implementation, got: ' + fpath_n})
            continue
        # Must map to an indexed file in analysis
        if fpath_n not in file_index_norm:
            rejections.append({'scenario_id': sid, 'reason': f'file not found in analysis: {fpath_n}'})
            continue
        # Reject header-only entries
        if file_index_norm.get(fpath_n, {}).get('is_header'):
            rejections.append({'scenario_id': sid, 'reason': 'file is a header; scenarios must map to implementation (.cpp) files'})
            continue

        # Function must exist in analysis and be declared in that file
        fn = s.get('function')
        if not fn:
            rejections.append({'scenario_id': sid, 'reason': 'missing function name'})
            continue
        # Accept either exact match or namespaced variants
        fn_matches = file_to_fns.get(fpath_n, set())
        if fn not in fn_matches:
            # try relaxed match by suffix (e.g., Class::method)
            if not any(x.endswith(fn) or x.split('::')[-1] == fn for x in fn_matches):
                rejections.append({'scenario_id': sid, 'reason': f'function {fn} not found in analysis for file {fpath_n}'})
                continue

        # Condition must look like a decision (contain in. or relational operators)
        cond = (s.get('condition') or '')
        if not cond or ('in.' not in cond and not any(op in cond for op in ['==', '!=', '<', '>', '<=', '>='])):
            rejections.append({'scenario_id': sid, 'reason': 'condition does not appear to be a decision expression'})
            continue

        # Expected behavior must be present and non-generic
        expected = s.get('expected_behavior') or s.get('expected') or s.get('expected_behavior')
        if not expected or (isinstance(expected, dict) and len(expected) == 0):
            rejections.append({'scenario_id': sid, 'reason': 'missing or empty expected behavior'})
            continue

        # Policy linkage
        if not s.get('policy_source') and not s.get('policy_justification'):
            rejections.append({'scenario_id': sid, 'reason': 'missing policy linkage (policy_source or policy_justification)'})
            continue

        # If all checks passed, accept
        valid.append(s)

    return valid, rejections


def plan_repo(repo: str, files: List[str], workspace_root: Optional[Path] = None, model: str = "ollama") -> Dict[str, Any]:
    """Invoke the planner tool (tools/run_planner.py)."""
    root = Path(workspace_root or _root())
    planner_script = root / 'tools' / 'run_planner.py'
    if not planner_script.exists():
        return {"success": False, "error": f"planner script not found: {planner_script}"}

    cmd = [sys.executable, str(planner_script), '--repo', repo, '--model', model]
    # The planner script does not accept a --files filter. We run planner for the
    # repo and then optionally filter produced scenarios by the requested files.

    result = _run_cmd(cmd, cwd=root)

    # Always expose planner stdout/stderr snippets for UI diagnostics
    result.setdefault('stdout', result.get('stdout', ''))
    result.setdefault('stderr', result.get('stderr', ''))

    # Capture planner failures but continue with deterministic extraction.
    rc = result.get('rc')
    if rc is not None and rc != 0:
        def _excerpt(text: str, max_chars: int = 4000) -> str:
            if not text:
                return ''
            return text if len(text) <= max_chars else text[-max_chars:]

        result.setdefault('warnings', []).append(
            f"Planner process exited with code {rc}; falling back to deterministic scenarios."
        )
        result['stdout'] = _excerpt(result.get('stdout', ''))
        result['stderr'] = _excerpt(result.get('stderr', ''))
        result['success'] = False

    # If planner emits JSON with per-file scenarios/coverage, pass it through.
    # Expecting possible keys: 'files', 'file_results', 'scenarios'
    if result.get('success') and isinstance(result.get('stdout'), str):
        try:
            parsed = json.loads(result['stdout'])
            if isinstance(parsed, dict):
                result.update(parsed)
        except Exception:
            pass

    repo_path = Path(repo)
    if not repo_path.is_absolute():
        repo_path = (root / repo).resolve()
    scenarios_path = repo_path / 'work' / 'scenarios.json'
    analysis_path = repo_path / 'tests' / 'analysis' / 'analysis.json'

    if result.get('success'):
        if scenarios_path.exists():
            try:
                payload = json.loads(scenarios_path.read_text(encoding='utf-8'))
                scenarios = payload.get('scenarios', [])

                # Deterministic filters to reject non-behavioural or irrelevant scenarios
                def is_header_file(path: str) -> bool:
                    return path.lower().endswith(('.h', '.hpp'))

                def is_test_path(path: str) -> bool:
                    p = path.replace('\\', '/').lower()
                    return p.startswith('tests/') or '/tests/' in p or 'oldtests' in p

                def is_language_construct(fn: Optional[str]) -> bool:
                    if not fn:
                        return True
                    low = fn.strip().lower()
                    constructs = ('if', 'switch', 'for', 'while', 'case', 'operator', 'test', 'test_f')
                    return any(low == c or low.startswith(c + ' ') for c in constructs)

                def is_generic_expected(text: Optional[str]) -> bool:
                    if not text:
                        return True
                    low = text.lower()
                    # Reject expected values that describe language constructs or generic test placeholders
                    bad_phrases = ('if statement', 'switch statement', 'test function', 'a test function', 'constructor', 'string created', 'an if statement')
                    return any(p in low for p in bad_phrases)

                filtered = []
                dropped_reasons = []
                for s in scenarios:
                    fpath = s.get('file', '')
                    fn = s.get('function')
                    exp = s.get('expected')

                    if not fpath or is_test_path(fpath) or is_header_file(fpath):
                        dropped_reasons.append((s, 'test/header file'))
                        continue
                    if is_language_construct(fn):
                        dropped_reasons.append((s, 'language construct'))
                        continue
                    if is_generic_expected(exp):
                        dropped_reasons.append((s, 'generic expected'))
                        continue

                    # passes basic filters -> keep
                    filtered.append(s)

                result['scenarios'] = filtered
                result.setdefault('warnings', [])
                if dropped_reasons:
                    result['warnings'].append(f"Filtered out {len(dropped_reasons)} non-behavioural scenarios (see logs)")
                result.setdefault('artifacts', {})['scenarios'] = str(scenarios_path)
                result['scenario_summary'] = {
                    "count": len(result['scenarios']),
                    "files": sorted({s.get('file') for s in result['scenarios'] if s.get('file')})
                }
                # Validate planner-produced scenarios against analysis if available,
                # otherwise run a light validator to reject obvious header/test entries.
                try:
                    analysis_path = repo_path / 'tests' / 'analysis' / 'analysis.json'
                    if analysis_path.exists():
                        try:
                            analysis = json.loads(analysis_path.read_text(encoding='utf-8'))
                        except Exception as e:
                            analysis = None
                            result.setdefault('warnings', []).append(f"Failed to load analysis.json for validation: {e}")
                        if analysis:
                            valid, rejections = _validate_scenarios_against_analysis(repo_path, result['scenarios'], analysis)
                        else:
                            valid, rejections = _light_validate_scenarios(result['scenarios'])
                    else:
                        valid, rejections = _light_validate_scenarios(result['scenarios'])

                    if rejections:
                        result.setdefault('rejections', []).extend(rejections)
                    result['scenarios'] = valid
                    result['scenario_summary'] = {"count": len(valid), "files": sorted({s.get('file') for s in valid if s.get('file')})}
                    # persist validated scenarios
                    try:
                        (repo_path / 'work').mkdir(parents=True, exist_ok=True)
                        scenarios_path.write_text(json.dumps({"schema_version": "1.0", "scenarios": valid}, indent=2), encoding='utf-8')
                        result.setdefault('artifacts', {})['scenarios'] = str(scenarios_path)
                    except Exception as e:
                        result.setdefault('warnings', []).append(f"Failed to persist validated planner scenarios: {e}")
                    if not valid:
                        result.setdefault('warnings', []).append('Validator rejected all planner-produced scenarios')
                        result['success'] = False
                except Exception:
                    # Non-fatal: keep original filtered scenarios if validation fails unexpectedly
                    pass
            except Exception as exc:
                result.setdefault('warnings', []).append(f"Unable to read scenarios.json: {exc}")

    # Deterministic extraction always runs to enforce policy-by-law
    try:
        if analysis_path.exists():
            try:
                analysis = json.loads(analysis_path.read_text(encoding='utf-8'))
            except Exception as ee:
                analysis = None
                result.setdefault('warnings', []).append(f"Failed to read analysis.json: {ee}")
            if analysis:
                extracted, extraction_notes = _extract_decision_points_from_analysis(repo_path, analysis, files=files)
                if extracted:
                    valid, rejections = _validate_scenarios_against_analysis(repo_path, extracted, analysis)
                    if rejections:
                        result.setdefault('rejections', []).extend(rejections)

                    if valid:
                        result.setdefault('warnings', []).append(
                            f"Using {len(valid)} deterministic decision-point scenarios from analysis.json (validated)"
                        )
                        result['scenarios'] = valid
                        result['scenario_summary'] = {
                            "count": len(valid),
                            "files": sorted({s.get('file') for s in valid if s.get('file')})
                        }
                        result.setdefault('artifacts', {})['decisions'] = str(analysis_path)
                        result['success'] = True
                        try:
                            scenarios_payload = {"schema_version": "1.0", "scenarios": valid}
                            (repo_path / 'work').mkdir(parents=True, exist_ok=True)
                            scenarios_path.write_text(json.dumps(scenarios_payload, indent=2), encoding='utf-8')
                            result['artifacts']['scenarios'] = str(scenarios_path)
                        except Exception as write_err:
                            result.setdefault('warnings', []).append(f"Failed to persist validated scenarios: {write_err}")
                    else:
                        warn = "All deterministic decision-point scenarios were rejected by validator"
                        if extraction_notes:
                            warn += f" ({'; '.join(sorted(set(extraction_notes)))})"
                        result.setdefault('warnings', []).append(warn)
                        result['scenarios'] = []
                        result['scenario_summary'] = {"count": 0, "files": []}
                        result.setdefault('artifacts', {})['decisions'] = str(analysis_path)
                        result['success'] = False
                else:
                    warn = "No valid decision-point scenarios found"
                    if extraction_notes:
                        warn += f" ({'; '.join(sorted(set(extraction_notes)))})"
                    result.setdefault('warnings', []).append(warn)
                    result['scenarios'] = []
                    result['scenario_summary'] = {"count": 0, "files": []}
                    result.setdefault('artifacts', {})['decisions'] = str(analysis_path)
                    result['success'] = False
        else:
            result.setdefault('warnings', []).append(
                "tests/analysis/analysis.json missing; cannot derive deterministic scenarios"
            )
            result['success'] = False
    except Exception as err:
        result.setdefault('warnings', []).append(f"Deterministic scenario extraction failed: {err}")
        result['success'] = False

    return result


def generate_for_files(repo: str, files: List[str], workspace_root: Optional[Path] = None, model: Optional[str] = None) -> Dict[str, Any]:
    """Invoke the generator (ai_c_test_generator) for specified files.
    This function intentionally does not interact with the user or print.
    """
    root = Path(workspace_root or _root())

    # Try to invoke the generator module via -m if installed in-tree.
    gen_cmd = [sys.executable, '-m', 'ai_c_test_generator.cli', 'mcdc-generate',
               '--repo-path', str(root / repo),
               '--output', str(root / repo / 'tests')]
    if files:
        # For simplicity, pass first file's directory & filename pair in this wrapper.
        # The CLI supports --source-dir and --file; if multiple files are provided, caller should loop.
        first = files[0]
        p = Path(first)
        gen_cmd.extend(['--source-dir', str(p.parent.as_posix()), '--file', p.name])
    if model:
        gen_cmd.extend(['--model', model])

    result = _run_cmd(gen_cmd, cwd=root)

    # Generator CLIs sometimes emit structured JSON about created tests and coverage.
    if result.get('success') and isinstance(result.get('stdout'), str):
        try:
            parsed = json.loads(result['stdout'])
            if isinstance(parsed, dict):
                result.update(parsed)
        except Exception:
            pass

    # Normalize file-level results: if generator returned an array of file objects, convert to map
    if result.get('success'):
        if 'files' in result and isinstance(result['files'], list):
            file_results = {}
            for item in result['files']:
                # item may be a dict with 'path' and optional 'coverage'/'executed'
                if isinstance(item, dict) and 'path' in item:
                    file_results[item['path']] = item
            if file_results:
                result['file_results'] = file_results

    return result


def _extract_decision_points_from_analysis(repo_path: Path, analysis: Dict[str, Any], files: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Derive deterministic decision-point scenarios from analyzer output."""
    scenarios: List[Dict[str, Any]] = []
    notes: List[str] = []

    file_index = analysis.get('file_index', {}) or {}
    fn_index = analysis.get('function_index', {}) or {}
    file_summaries = analysis.get('file_summaries', {}) or {}

    def norm(path: str) -> str:
        return path.replace('\\', '/').strip()

    def skip_path(path: str) -> bool:
        low = norm(path).lower()
        if not low.endswith('.cpp'):
            return True
        if low.startswith('tests/') or '/tests/' in low or 'oldtests' in low:
            return True
        return False

    def file_hardware_free(path: str) -> bool:
        meta = file_summaries.get(norm(path)) or file_summaries.get(path) or {}
        return bool(meta.get('hardware_free', True))

    def match_functions(rel_path: str) -> List[Dict[str, Any]]:
        matches = []
        for fname, finfo in fn_index.items():
            ffile = norm(finfo.get('file', ''))
            if ffile == norm(rel_path):
                entry = dict(finfo)
                entry['_name'] = fname
                matches.append(entry)
        return matches

    files_set = {norm(f) for f in files} if files else None

    decision_pattern = re.compile(r'if\s*\((?P<cond>[^)]*?)\)\s*\{(?P<body>[^{}]*?)\}', re.S)

    def parse_expected(block: str) -> Dict[str, str]:
        expected: Dict[str, str] = {}
        for field in ('aspect', 'reason', 'health'):
            m = re.search(rf'out\.{field}\s*=\s*([^;]+);', block)
            if m:
                expected[f'out.{field}'] = m.group(1).strip()
        return expected

    def parse_inputs(cond: str) -> Dict[str, Any]:
        inputs: Dict[str, Any] = {}
        seen: set[str] = set()
        for m in re.finditer(r'in\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<op>[!=]=)\s*(?P<value>[A-Za-z0-9_:]+)', cond):
            field = m.group('field')
            if field in seen:
                continue
            op = m.group('op')
            val = m.group('value')
            inputs[f'in.{field}'] = f"{op} {val}"
            seen.add(field)
        for m in re.finditer(r'(!?)\s*in\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)', cond):
            field = m.group('field')
            if field in seen:
                continue
            negate = bool(m.group(1) and '!' in m.group(1))
            inputs[f'in.{field}'] = not negate
            seen.add(field)
        return inputs

    def summarize_expected(expected: Dict[str, str]) -> str:
        if not expected:
            return "Observable outputs set"
        parts = [f"{k.split('.')[-1].capitalize()} = {v}" for k, v in expected.items()]
        return ', '.join(parts)

    scenario_id = 1
    for rel, meta in file_index.items():
        rel_norm = norm(rel)
        if skip_path(rel_norm):
            continue
        if files_set and rel_norm not in files_set:
            continue
        if not file_hardware_free(rel_norm):
            notes.append(f"Skipped {rel_norm} (hardware-bound file)")
            continue

        functions = match_functions(rel_norm)
        if not functions:
            notes.append(f"No functions indexed for {rel_norm}")
            continue

        src_path = (repo_path / rel_norm).resolve()
        file_text = ''
        try:
            file_text = src_path.read_text(encoding='utf-8', errors='ignore') if src_path.exists() else ''
        except Exception:
            file_text = ''

        for fn_info in functions:
            fn_name = fn_info.get('_name') or fn_info.get('name')
            touches_hw = fn_info.get('touches_hardware')
            if touches_hw:
                notes.append(f"Skipped {fn_name} in {rel_norm} (touches hardware)")
                continue

            body = fn_info.get('body') or ''
            if not body and file_text:
                body = file_text
            if not body:
                notes.append(f"No function body available for {fn_name} in {rel_norm}")
                continue

            decision_blocks = list(decision_pattern.finditer(body))
            if not decision_blocks:
                notes.append(f"No decision points found in {fn_name} ({rel_norm})")
                continue

            for block in decision_blocks:
                cond = block.group('cond').strip()
                block_text = block.group('body')
                expected = parse_expected(block_text)
                if not expected:
                    continue
                inputs = parse_inputs(cond)
                expected_summary = summarize_expected(expected)
                blocking = any('stop' in v.lower() for v in expected.values()) or any(
                    keyword in expected_summary.lower() for keyword in ('fault', 'degrad'))

                scenario = {
                    'scenario_id': f"DEC-{scenario_id:03d}",
                    'file': rel_norm,
                    'function': fn_name or 'unknown',
                    'condition': cond,
                    'inputs': inputs,
                    'expected_behavior': expected,
                    'expected_summary': expected_summary,
                    'policy_justification': 'SIL2 branch coverage – verify decision outcome',
                    'policy_source': 'SIL2 branch coverage',
                    'blocking': blocking,
                    'test_intent': 'branch',
                    'justification': f"Derived from decision in {fn_name or 'function'} ({rel_norm})",
                }
                scenario_id += 1
                scenarios.append(scenario)

    return scenarios, notes
