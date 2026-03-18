from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ALLOWED_LEVELS = ("QM", "SIL0", "SIL1", "SIL2", "SIL3", "SIL4")


def _coerce_scalar(value: str) -> Any:
    v = value.strip()
    if not v:
        return ""
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
            return int(v)
    except Exception:
        pass
    return v


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, root)]

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        if ":" not in stripped:
            raise ValueError(f"Invalid YAML line (missing ':'): {raw}")
        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        cur = stack[-1][1]
        if rest == "":
            child: dict[str, Any] = {}
            cur[key] = child
            stack.append((indent, child))
        else:
            cur[key] = _coerce_scalar(rest)
    return root


@dataclass(frozen=True)
class SafetyPolicy:
    safety_level: str
    base_tests: bool
    boundary_tests: bool
    error_path_tests: bool
    mcdc_analysis: str | bool
    mcdc_generation: str | bool
    approval_required: bool
    coverage_target: dict[str, Any]

    @staticmethod
    def allowed_levels() -> tuple[str, ...]:
        return _ALLOWED_LEVELS

    @classmethod
    def load(
        cls,
        *,
        safety_level: str,
        repo_root: Path,
        policy_file: str | None = None,
        disable_mcdc: bool = False,
        workspace_root: Path | None = None,
    ) -> "SafetyPolicy":
        level = (safety_level or "QM").upper().strip()
        if level not in _ALLOWED_LEVELS:
            raise ValueError(f"Invalid safety level: {safety_level}. Allowed: {', '.join(_ALLOWED_LEVELS)}")

        repo_root = repo_root.resolve()
        if workspace_root is None:
            workspace_root = Path(__file__).resolve().parents[2]

        if policy_file:
            policy_path = Path(policy_file)
            if not policy_path.is_absolute():
                policy_path = (workspace_root / policy_path).resolve()
        else:
            policy_path = repo_root / "safety_policy.yaml"
            if not policy_path.exists():
                policy_path = workspace_root / "safety_policy.yaml"

        if not policy_path.exists():
            raise FileNotFoundError(
                "safety_policy.yaml not found. Provide --policy-file or place it in the target repo root or workspace root."
            )

        data = _load_simple_yaml(policy_path)
        if level not in data or not isinstance(data.get(level), dict):
            raise ValueError(f"Policy file missing top-level key: {level}")

        cfg = data[level]
        assert isinstance(cfg, dict)

        mcdc_analysis = cfg.get("mcdc_analysis", False)
        mcdc_generation = cfg.get("mcdc_generation", False)
        if disable_mcdc:
            mcdc_analysis = False
            mcdc_generation = False

        return cls(
            safety_level=level,
            base_tests=bool(cfg.get("base_tests", True)),
            boundary_tests=bool(cfg.get("boundary_tests", False)),
            error_path_tests=bool(cfg.get("error_path_tests", False)),
            mcdc_analysis=mcdc_analysis,
            mcdc_generation=mcdc_generation,
            approval_required=bool(cfg.get("approval_required", False)),
            coverage_target=dict(cfg.get("coverage_target", {}) or {}),
        )

    def mcdc_analysis_required(self) -> bool:
        v = self.mcdc_analysis
        if v is True:
            return True
        if isinstance(v, str):
            return v.lower().strip() == "mandatory"
        return False

    def mcdc_analysis_enabled(self) -> bool:
        v = self.mcdc_analysis
        if v is True:
            return True
        if isinstance(v, str):
            return v.lower().strip() in ("mandatory", "optional")
        return False


def _summary_path(repo_root: Path) -> Path:
    return (repo_root / "tests" / "safety_summary.json").resolve()


def load_safety_summary(repo_root: Path) -> dict[str, Any]:
    path = _summary_path(repo_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_safety_summary(repo_root: Path, update: dict[str, Any]) -> Path:
    repo_root = repo_root.resolve()
    out = _summary_path(repo_root)
    out.parent.mkdir(parents=True, exist_ok=True)

    data = load_safety_summary(repo_root)
    merged = dict(data)
    for k, v in (update or {}).items():
        if k == "coverage_status" and isinstance(v, dict) and isinstance(merged.get("coverage_status"), dict):
            cv = dict(merged["coverage_status"])
            cv.update(v)
            merged["coverage_status"] = cv
        else:
            merged[k] = v

    out.write_text(json.dumps(merged, indent=2), encoding="utf-8", newline="\n")
    return out
