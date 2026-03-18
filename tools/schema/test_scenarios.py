import json
import importlib.util
import os
from pathlib import Path
import uuid

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATOR_PATH = REPO_ROOT / 'tools' / 'schema' / 'validate.py'
ORIG_SCHEMA = REPO_ROOT / 'schemas' / 'scenarios.schema.json'


def _load_validator_module():
    name = f"verisafe_validator_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, str(VALIDATOR_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _copy_schema_to(tmp_cwd: Path):
    dest_dir = tmp_cwd / 'schemas'
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / ORIG_SCHEMA.name
    dst.write_text(ORIG_SCHEMA.read_text(encoding='utf-8'), encoding='utf-8')
    return dst


def test_scenarios_failure_writes_violation(tmp_path: Path):
    tmp_cwd = tmp_path / 'senv'
    tmp_cwd.mkdir()
    _copy_schema_to(tmp_cwd)

    # broken: missing top-level schema_version
    artifact = tmp_cwd / 'work' / 'scenarios.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({
        "scenarios": [
            {
                "scenario_id": "b1",
                "function_id": "fn_unknown",
                "test_type": "BASE",
                "justification": "NEW_FUNCTION",
                "required_inputs": [],
                "policy_source": "example",
                "blocking": False
            }
        ]
    }), encoding='utf-8')

    old_cwd = Path.cwd()
    os.chdir(tmp_cwd)
    try:
        mod = _load_validator_module()
        validate_or_halt = getattr(mod, 'validate_or_halt')
        with pytest.raises(SystemExit) as exc:
            validate_or_halt(json.loads(artifact.read_text(encoding='utf-8')), str(tmp_cwd / 'schemas' / ORIG_SCHEMA.name), artifact_name=artifact.name)
        assert exc.value.code == 2
        vio = tmp_cwd / 'work' / 'policy_violations.json'
        assert vio.exists()
    finally:
        os.chdir(old_cwd)


def test_scenarios_success_proceeds(tmp_path: Path):
    tmp_cwd = tmp_path / 'senv2'
    tmp_cwd.mkdir()
    _copy_schema_to(tmp_cwd)

    valid = {
        "schema_version": "scenarios-1.0",
        "scenarios": [
            {
                "scenario_id": "s_ok",
                "function_id": "fn1",
                "test_type": "BASE",
                "justification": "NEW_FUNCTION",
                "required_inputs": [],
                "policy_source": "example",
                "blocking": False
            }
        ]
    }

    artifact = tmp_cwd / 'work' / 'scenarios.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(valid), encoding='utf-8')

    old_cwd = Path.cwd()
    os.chdir(tmp_cwd)
    try:
        mod = _load_validator_module()
        validate_or_halt = getattr(mod, 'validate_or_halt')
        # Should not raise
        validate_or_halt(valid, str(tmp_cwd / 'schemas' / ORIG_SCHEMA.name), artifact_name=artifact.name)
        vio = tmp_cwd / 'work' / 'policy_violations.json'
        assert not vio.exists()
    finally:
        os.chdir(old_cwd)
