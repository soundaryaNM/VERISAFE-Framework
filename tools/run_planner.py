"""Run the planner directly (simple wrapper).
Usage:
  python tools/run_planner.py --repo "path/to/repo" --model ollama
"""
import argparse
import json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--model', default='ollama')
    args = p.parse_args()

    repo = Path(args.repo).resolve()
    # Load analysis (prefer work/analysis.json)
    analysis = {}
    cand = repo / 'work' / 'analysis.json'
    legacy = repo / 'tests' / 'analysis' / 'analysis.json'
    workspace_repo_scan = Path(__file__).parent.parent / 'work' / 'repo_scan.json'
    if cand.exists():
        analysis = json.loads(cand.read_text(encoding='utf-8'))
    elif legacy.exists():
        analysis = json.loads(legacy.read_text(encoding='utf-8'))
    elif workspace_repo_scan.exists():
        print(f"[INFO] Using workspace repo_scan.json at {workspace_repo_scan}")
        analysis = json.loads(workspace_repo_scan.read_text(encoding='utf-8'))
    else:
        raise SystemExit('No analysis.json or repo_scan.json found; run analyzer first')

    # Import Planner and run
    try:
        from CW_Test_Gen.ai_c_test_generator.planner import Planner
    except Exception:
        # Try adding CW_Test_Gen to sys.path (when running from workspace root)
        import sys
        this_file = Path(__file__).resolve()
        ws_root = this_file.parent.parent
        gen_root = ws_root / 'CW_Test_Gen'
        if gen_root.exists():
            sys.path.insert(0, str(gen_root))
        try:
            from ai_c_test_generator.planner import Planner
        except Exception as e:
            print(f"[ERROR] Could not import Planner: {e}")
            raise
    planner = Planner(str(repo), model_choice=args.model)
    scenarios = planner.plan(analysis)
    print(f"Planner wrote work/scenarios.json ({len(scenarios.get('scenarios', []))} scenarios)")

if __name__ == '__main__':
    main()
