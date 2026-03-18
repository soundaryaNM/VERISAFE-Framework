"""
Function-level manifest for tracking production function signatures and test mappings.

Used for change detection and selective regeneration.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, Any, List


def compute_function_signature_hash(function_name: str, return_type: str, param_types: List[str], qualifiers: str = "") -> str:
    """Compute a stable hash for a function signature."""
    components = [function_name, return_type] + list(param_types) + [qualifiers]
    data = "|".join(components).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_function_content_hash(signature_hash: str, body: str) -> str:
    """Compute a stable hash that changes when function body or signature changes."""
    payload = f"{signature_hash}|{body or ''}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class FunctionManifest:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.path = self.repo_root / "tests" / ".function_manifest.json"
        self.data: Dict[str, Any] = {"version": 1, "functions": {}}

    def load(self) -> None:
        if not self.path.exists():
            self.data = {"version": 1, "functions": {}}
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.data, dict):
            self.data = {"version": 1, "functions": {}}
        self.data.setdefault("version", 1)
        self.data.setdefault("functions", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def update_function(
        self,
        *,
        function_name: str,
        file_rel: str,
        signature_hash: str,
        return_type: str,
        param_types: List[str],
        qualifiers: str = "",
        test_inc_file: str | None = None,
        content_hash: str | None = None,
    ) -> None:
        functions = self.data.setdefault("functions", {})
        functions[function_name] = {
            "file_rel": file_rel,
            "signature_hash": signature_hash,
            "content_hash": content_hash,
            "return_type": return_type,
            "param_types": param_types,
            "qualifiers": qualifiers,
            "test_inc_file": test_inc_file,
        }

    def get_changed_functions(self, current_functions: Dict[str, Dict[str, Any]]) -> List[str]:
        """Return list of function names that have changed or are new."""
        changed = []
        existing = self.data.get("functions", {})
        for func_name, func_info in current_functions.items():
            if func_name not in existing:
                changed.append(func_name)
            else:
                prev = existing.get(func_name) or {}
                prev_content = prev.get("content_hash")
                next_content = func_info.get("content_hash")
                if prev_content and next_content:
                    if prev_content != next_content:
                        changed.append(func_name)
                elif prev.get("signature_hash") != func_info.get("signature_hash"):
                    changed.append(func_name)
        return changed

    def get_deleted_functions(self, current_functions: Dict[str, Dict[str, Any]]) -> List[str]:
        """Return list of function names that have been deleted."""
        current_names = set(current_functions.keys())
        existing_names = set(self.data.get("functions", {}).keys())
        return list(existing_names - current_names)