from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict


@dataclass
class SessionState:
    """Holds UI-visible session state only. CLI updates this; controller/actions read it."""

    running: bool = True
    selected_repo: str = "RailwaySignalSystem"
    selected_files: List[str] = field(default_factory=list)
    last_action_result: Optional[dict] = None
    history: List[dict] = field(default_factory=list)

    # Pipeline Status for UI
    analyzed: bool = False
    planned: bool = False
    generated: bool = False
    coverage_percent: Optional[float] = None
    # Per-file status map: path -> {analyzed, planned, generated, executed, coverage}
    file_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    file_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scenario_plan: Dict[str, Any] = field(default_factory=dict)
    analysis_summary: Dict[str, Any] = field(default_factory=dict)
    generated_test_count: int = 0
    test_intent: str = "Decision & branch coverage"
    safety_level: str = "SIL2"
    approval_status: Dict[str, Any] = field(default_factory=lambda: {
        "generated": 0,
        "approved": 0,
        "execution_blocked": True
    })
    pipeline_snapshot_data: Dict[str, Any] = field(default_factory=dict)

    def record(self, action: str, result: dict) -> None:
        self.last_action_result = result
        self.history.append({"action": action, "result": result})
        normalized_selection = [self._normalize_path(f) for f in self.selected_files]
        self.selected_files = normalized_selection

        if action == "analyze" and result.get("success"):
            self.analyzed = True
            for f in self.selected_files:
                s = self.file_status.setdefault(f, {})
                s['analyzed'] = True
        elif action == "plan" and result.get("success"):
            self.planned = True
            for f in self.selected_files:
                s = self.file_status.setdefault(f, {})
                s['planned'] = True
        elif action == "generate" and result.get("success"):
            self.generated = True
            for f in self.selected_files:
                s = self.file_status.setdefault(f, {})
                s['generated'] = True
        # capture execution and coverage if returned per-file
        if result.get('success'):
            # global coverage field applies to all selected files
            cov = result.get('coverage')
            if cov is not None:
                self.coverage_percent = cov
                for f in self.selected_files:
                    s = self.file_status.setdefault(f, {})
                    s['coverage'] = cov

            # file_results is a map path -> info
            file_results = result.get('file_results')
            if isinstance(file_results, dict):
                for path, info in file_results.items():
                    norm = self._normalize_path(path)
                    s = self.file_status.setdefault(norm, {})
                    # merge known keys
                    if isinstance(info, dict):
                        if info.get('coverage') is not None:
                            s['coverage'] = info.get('coverage')
                        if info.get('executed') is not None:
                            s['executed'] = bool(info.get('executed'))

            if analysis := result.get('analysis'):
                self._ingest_analysis(analysis)

            if scenarios := result.get('scenarios'):
                self._ingest_scenarios(scenarios)

            if action == 'generate':
                self._capture_generation_metrics(result)

            self._update_pipeline_snapshot()

    # Snapshot helpers -------------------------------------------------
    def scope_snapshot(self) -> Dict[str, Any]:
        if not self.selected_files:
            return {"has_selection": False}
        target = self.selected_files[0]
        meta = self.file_metadata.get(target)
        functions = (meta or {}).get('functions', [])
        scenarios = self.scenario_plan.get('per_file', {}).get(target, [])
        has_analysis = bool(meta and meta.get('function_count'))
        return {
            "has_selection": True,
            "file": target,
            "has_analysis": has_analysis,
            "function_count": meta.get('function_count') if meta else None,
            "functions": [fn.get('name') for fn in functions[:4]] if functions else [],
            "touches_hardware": (meta or {}).get('touches_hardware'),
            "scenario_count": len(scenarios),
            "safety_level": self.safety_level,
            "test_intent": self.test_intent
        }

    def pipeline_snapshot(self) -> Dict[str, Any]:
        if not self.pipeline_snapshot_data:
            self._update_pipeline_snapshot()
        return self.pipeline_snapshot_data

    def scenario_preview(self, limit: int = 6) -> Dict[str, Any]:
        scenarios = self.scenario_plan.get('scenarios', [])
        return {
            "count": len(scenarios),
            "items": scenarios[:limit]
        }

    def safety_gates_snapshot(self) -> List[dict]:
        approved = self.approval_status.get('approved', 0)
        generated = self.approval_status.get('generated', 0)
        return [
            {"label": "Deterministic analysis", "status": self.analyzed, "detail": "Structure frozen from analyzer"},
            {"label": "Compiler-based validation", "status": self.generated, "detail": "Pending host build once tests ready"},
            {"label": "Manual approval required", "status": True, "detail": "Engineers must review before execution"},
            {"label": "Tests approved", "status": approved > 0, "detail": f"{approved}/{generated} approved"}
        ]

    def approval_snapshot(self) -> Dict[str, Any]:
        return {
            "generated": self.approval_status.get('generated', 0),
            "approved": self.approval_status.get('approved', 0),
            "execution_blocked": self.approval_status.get('execution_blocked', True)
        }

    # Internal helpers -------------------------------------------------
    def _normalize_path(self, path: str) -> str:
        return path.replace('\\', '/') if isinstance(path, str) else path

    def _ingest_analysis(self, analysis: dict) -> None:
        file_index = analysis.get('file_index') or {}
        function_index = analysis.get('function_index') or {}

        for key, info in file_index.items():
            path = self._normalize_path(info.get('path') or key)
            entry = self.file_metadata.setdefault(path, {"functions": []})
            entry['language'] = info.get('language')
            entry['is_header'] = info.get('is_header')
            entry['includes'] = info.get('includes', [])
            status = self.file_status.setdefault(path, {})
            status['analyzed'] = True

        for func_name, func_info in function_index.items():
            path = self._normalize_path(func_info.get('file') or '')
            if not path:
                continue
            entry = self.file_metadata.setdefault(path, {"functions": []})
            fn = {
                "name": func_info.get('name') or func_name,
                "touches_hardware": func_info.get('touches_hardware'),
                "calls": func_info.get('calls', [])
            }
            entry.setdefault('functions', [])
            entry['functions'].append(fn)

        for path, entry in self.file_metadata.items():
            funcs = entry.get('functions', [])
            entry['function_count'] = len(funcs)
            entry['touches_hardware'] = any(fn.get('touches_hardware') for fn in funcs)

        self.analysis_summary = {
            "files_indexed": len(self.file_metadata),
            "functions_indexed": sum(e.get('function_count', 0) for e in self.file_metadata.values())
        }

    def _ingest_scenarios(self, scenarios: List[dict]) -> None:
        per_file: Dict[str, List[dict]] = {}
        for scenario in scenarios:
            path = self._normalize_path(scenario.get('file') or '')
            if not path:
                continue
            per_file.setdefault(path, []).append(scenario)
            status = self.file_status.setdefault(path, {})
            status['planned'] = True

        self.scenario_plan = {
            "total": len(scenarios),
            "per_file": per_file,
            "scenarios": scenarios
        }

    def _capture_generation_metrics(self, result: dict) -> None:
        produced = 0
        file_results = result.get('file_results') or {}
        if isinstance(file_results, dict) and file_results:
            produced = len(file_results)
        elif result.get('generated_tests') is not None:
            produced = int(result.get('generated_tests'))
        elif self.selected_files:
            produced = len(self.selected_files)

        if produced:
            self.generated_test_count += produced
            self.approval_status['generated'] += produced
            self.approval_status['execution_blocked'] = True

    def _update_pipeline_snapshot(self) -> None:
        has_selection = bool(self.selected_files)

        def status_detail(stage: str) -> Dict[str, str]:
            # Pipeline states when no scope selected
            if not has_selection:
                return {"status": "N/A", "detail": "Select a file or repository to activate pipeline"}

            # With a scope selected, stages follow dependency rules
            if stage == 'analysis':
                if not self.analyzed:
                    return {"status": "READY", "detail": "Run analysis to index functions"}
                return {"status": "DONE", "detail": f"{self.analysis_summary.get('functions_indexed', 0)} functions indexed"}

            if stage == 'planning':
                if not self.analyzed:
                    return {"status": "LOCKED", "detail": "Requires analysis"}
                if not self.planned:
                    return {"status": "READY", "detail": "Prepare scenarios (planning)"}
                return {"status": "DONE", "detail": f"{self.scenario_plan.get('total', 0)} scenarios staged"}

            if stage == 'generation':
                if not self.planned:
                    return {"status": "LOCKED", "detail": "Requires planning"}
                if not self.generated:
                    return {"status": "READY", "detail": "Synthesize tests from scenarios"}
                return {"status": "DONE", "detail": f"{self.generated_test_count} test files produced"}

            return {"status": "", "detail": ""}

        analysis_meta = status_detail('analysis')
        planning_meta = status_detail('planning')
        generation_meta = status_detail('generation')

        self.pipeline_snapshot_data = {
            "status": "ACTIVE" if has_selection else "INACTIVE",
            "analysis": {"done": self.analyzed and has_selection, **analysis_meta},
            "planning": {"done": self.planned and has_selection, **planning_meta},
            "generation": {"done": self.generated and has_selection, **generation_meta}
        }
