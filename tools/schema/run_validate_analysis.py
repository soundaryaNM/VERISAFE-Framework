"""Copy repo-local analysis.json to work/ and run schema validator on it."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

src = ROOT / 'RailwaySignalSystem' / 'tests' / 'analysis' / 'analysis.json'
dst = ROOT / 'work' / 'analysis.json'
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
print(f"Copied {src} -> {dst}")

try:
    # Build a condensed repo_scan artifact from the analyzer output so it matches the schema
    analysis = json.loads(dst.read_text(encoding='utf-8'))
    repo_scan = {
        'schema_version': 'repo_scan-1.0',
        'repo_root': str(Path(ROOT / 'RailwaySignalSystem').resolve()),
        'functions': [],
        'call_graph': [],
        'call_depth': {},
        'hardware_flags': {},
    }
    import hashlib
    fn_index = analysis.get('function_index', {}) if isinstance(analysis, dict) else {}
    for fid, info in fn_index.items():
        name = info.get('name') or fid
        file = info.get('file') or ''
        signature = info.get('signature') or name
        body = info.get('body') or ''
        source_hash = hashlib.sha256(body.encode('utf-8')).hexdigest() if isinstance(body, str) else ''
        repo_scan['functions'].append({'id': fid, 'name': name, 'file': file, 'signature': signature, 'source_hash': source_hash})
    cg = analysis.get('call_graph', {})
    if isinstance(cg, dict):
        for caller, callees in cg.items():
            if isinstance(callees, list):
                for callee in callees:
                    repo_scan['call_graph'].append({'caller_id': caller, 'callee_id': callee})
    cd = analysis.get('call_depths') or analysis.get('call_depth') or {}
    if isinstance(cd, dict):
        for k, v in cd.items():
            repo_scan['call_depth'][k] = int(v)
    hf = analysis.get('hardware_flags', {})
    if isinstance(hf, dict):
        repo_scan['hardware_flags'] = {k: bool(v) for k, v in hf.items()}

    repo_scan_path = ROOT / 'work' / 'repo_scan.json'
    repo_scan_path.write_text(json.dumps(repo_scan, indent=2), encoding='utf-8')
    print('Wrote repo_scan.json')

    import importlib.util
    spec = importlib.util.spec_from_file_location('validator', str(ROOT / 'tools' / 'schema' / 'validate.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    validate_or_halt = getattr(mod, 'validate_or_halt')
    validate_or_halt(repo_scan, str(ROOT / 'schemas' / 'repo_scan.schema.json'), artifact_name='repo_scan.json')
    print('Validation OK')
except SystemExit as e:
    print('Validator exited with', e)
except Exception as e:
    print('Validator error:', e)
