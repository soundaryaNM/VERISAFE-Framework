from typing import Optional
from pathlib import Path

from session.state import SessionState
from session.controller import SessionController


def _print_header(state: SessionState):
    """Prints a persistent dashboard status."""
    print("\n" + "="*70)
    print(f" VERISAFE SAFETY CONTROL PANEL - {state.selected_repo}")
    print("="*70)
    
    # Status Indicators with color coding
    def status_tag(label, ok):
        return f"\033[92m[{label} OK]\033[0m" if ok else f"\033[90m[{label} ---]\033[0m"
    
    target = state.selected_files[0] if state.selected_files else 'None selected'
    if len(state.selected_files) > 1:
        target += f" (+{len(state.selected_files)-1} others)"
    
    print(f" Target File: {target}")
    print(f" Status Check: {status_tag('ANALYZED', state.analyzed)} -> "
                  f"{status_tag('PLANNED', state.planned)} -> "
                  f"{status_tag('GENERATED', state.generated)}")
    
    cov = f"{state.coverage_percent}%" if state.coverage_percent is not None else "N/A"
    print(f" Live Coverage: {cov}")
    print("-" * 70)


def _list_scenarios(repo_root: Path):
    scen = repo_root / 'work' / 'scenarios.json'
    if not scen.exists():
        print("\n\033[93m[!] No scenarios found. Run planner first.\033[0m")
        return
    try:
        import json
        data = json.loads(scen.read_text(encoding='utf-8'))
        scenarios = data.get('scenarios', {})
        print(f"\n--- {len(scenarios)} Scenarios Found in scenarios.json ---")
        for i, (fn, items) in enumerate(scenarios.items()):
            print(f" {i+1}. {fn} ({len(items)} cases)")
            for case in items[:2]:
                print(f"    - {case.get('id', '??')}: {case.get('description', '')[:50]}...")
    except Exception as e:
        print(f"Failed to read scenarios.json: {e}")


def run_main_menu(state: SessionState, controller: SessionController):
    """Main interactive menu. Only updates SessionState and dispatches actions."""

    repo_root = Path(controller.workspace_root) / state.selected_repo

    def _ensure_selected_files() -> None:
        """If no files selected, prompt user to pick from repo source files."""
        candidates = []
        for ext in ("*.cpp", "*.c", "*.h", "*.hpp"):
            for p in sorted(repo_root.rglob(f"**/{ext}")):
                parts = {part for part in p.parts}
                if 'build' in parts or '.git' in parts or '.github' in parts:
                    continue
                try:
                    rel = p.relative_to(repo_root).as_posix()
                except Exception:
                    rel = str(p)
                candidates.append(rel)

        if not candidates:
            print("No source files found in repo!")
            return

        print(f"\nTarget Selection for {state.selected_repo}:")
        for idx, rel in enumerate(candidates, start=1):
            print(f"{idx:3d}. {rel}")
        print(" a) All files")
        
        pick = input("\nSelect target file number(s) (e.g. 1 or 1,3) > ").strip().lower()
        if not pick:
            return
            
        if pick in ('a', 'all'):
            state.selected_files = candidates
        else:
            parts = [p.strip() for p in pick.split(',') if p.strip()]
            picks = []
            for part in parts:
                if part.isdigit():
                    i = int(part)
                    if 1 <= i <= len(candidates):
                        picks.append(candidates[i - 1])
            if picks:
                state.selected_files = picks
                state.planned = False
                state.generated = False

    if not state.selected_files:
        _ensure_selected_files()

    while state.running:
        _print_header(state)
        
        h_a = " \033[94m(<- Start Here)\033[0m" if not state.analyzed else ""
        h_p = " \033[94m(<- Next Step)\033[0m" if state.analyzed and not state.planned else ""
        h_g = " \033[94m(<- Ready to Synthesize)\033[0m" if state.planned and not state.generated else ""

        print(f" 1) Load Code Analysis{h_a}")
        print(f" 2) Test Engineering (Run Planner){h_p}")
        print(f" 3) Synthesize Unit Tests (Run Generator){h_g}")
        print(f" 4) Inspect Scenarios")
        print("-" * 20)
        print(" 5) Switch Target File")
        print(" q) Exit Session")
        print("")

        choice = input("Controller Action > ").strip().lower()
        
        if choice == '1':
            print("\033[96m[ACTION] Loading repository maps...\033[0m")
            controller.dispatch('analyze', state)
        elif choice == '2':
            print("\033[96m[ACTION] Generating test blueprints...\033[0m")
            controller.dispatch('plan', state, payload={'model': 'ollama'})
        elif choice == '3':
            if not state.planned:
                print("\033[93m[!] Planning required before generation.\033[0m")
            else:
                print("\033[96m[ACTION] Materializing C++ source code...\033[0m")
                controller.dispatch('generate', state)
        elif choice == '4':
            _list_scenarios(repo_root)
            input("\nPress ENTER to return to dashboard...")
            continue
        elif choice == '5':
            _ensure_selected_files()
            continue
        elif choice == 'q':
            state.running = False
            break
        
        if state.last_action_result:
            res = state.last_action_result
            if res.get('success'):
                print(f"\n\033[92m[✓] SUCCESS\033[0m: Completed {state.history[-1]['action']}")
            else:
                err = res.get('error', 'Unknown Error')
                print(f"\n\033[91m[✗] ERROR\033[0m: {err}")
            input("\nPress ENTER to continue...")
