from typing import Optional, Any, Dict
from pathlib import Path

from actions.actions import analyze_repo, plan_repo, generate_for_files
from .state import SessionState


class SessionController:
    """Orchestrates calls to action functions. Does not perform I/O or prompt."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()

    def dispatch(self, action: str, state: SessionState, payload: Optional[dict] = None) -> Dict[str, Any]:
        payload = payload or {}
        if action == "analyze":
            result = analyze_repo(state.selected_repo, workspace_root=self.workspace_root, safety_level=state.safety_level)
        elif action == "plan":
            files = state.selected_files or payload.get("files", [])
            result = plan_repo(state.selected_repo, files, workspace_root=self.workspace_root, model=payload.get("model", "ollama"))
        elif action == "generate":
            files = state.selected_files or payload.get("files", [])
            result = generate_for_files(state.selected_repo, files, workspace_root=self.workspace_root, model=payload.get("model"))
        else:
            result = {"success": False, "error": f"unknown action: {action}"}

        # Update per-file execution flags where appropriate
        state.record(action, result)

        return result
