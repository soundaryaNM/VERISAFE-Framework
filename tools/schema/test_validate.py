import json
import importlib.util
import os
from pathlib import Path
import uuid

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATOR_PATH = REPO_ROOT / 'tools' / 'schema' / 'validate.py'
ORIG_SCHEMA = REPO_ROOT / 'schemas' / 'planning_constraints.schema.json'


def _load_validator_module(tmp_cwd: Path):
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


def test_failure_writes_violation(tmp_path: Path):
    # prepare isolated cwd
    tmp_cwd = tmp_path / 'env'
    tmp_cwd.mkdir()
    _copy_schema_to(tmp_cwd)

    # create invalid artifact (missing required fields)
    artifact = tmp_cwd / 'work' / 'planning_constraints.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}", encoding='utf-8')

    # chdir and run
    old_cwd = Path.cwd()
    os.chdir(tmp_cwd)
    try:
        mod = _load_validator_module(tmp_cwd)
        validate_or_halt = getattr(mod, 'validate_or_halt')
        with pytest.raises(SystemExit) as exc:
            validate_or_halt(json.loads(artifact.read_text(encoding='utf-8')), str(tmp_cwd / 'schemas' / ORIG_SCHEMA.name), artifact_name=artifact.name)
        assert exc.value.code == 2
        vio = tmp_cwd / 'work' / 'policy_violations.json'
        assert vio.exists()
        data = json.loads(vio.read_text(encoding='utf-8'))
        assert data.get('artifact_name') == artifact.name
        assert any('schema_version' in e.get('message', '') or 'required' in e.get('message', '') for e in data.get('errors', []))
    finally:
        os.chdir(old_cwd)


def test_success_no_violation(tmp_path: Path):
    tmp_cwd = tmp_path / 'env2'
    tmp_cwd.mkdir()
    _copy_schema_to(tmp_cwd)

    valid = {
        "schema_version": "planning_constraints-1.0",
        "policy_id": "example-policy",
        "policy_version": "1.0",
        "constraints": {
            "must_generate_test_types": ["BASE"],
            "mcdc_required": False,
            "approval_required": True,
            "coverage_targets": {"branch": 80, "statement": 90}
        }
    }

    artifact = tmp_cwd / 'work' / 'planning_constraints.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(valid), encoding='utf-8')

    old_cwd = Path.cwd()
    os.chdir(tmp_cwd)
    try:
        mod = _load_validator_module(tmp_cwd)
        validate_or_halt = getattr(mod, 'validate_or_halt')
        # Should not raise
        validate_or_halt(valid, str(tmp_cwd / 'schemas' / ORIG_SCHEMA.name), artifact_name=artifact.name)
        vio = tmp_cwd / 'work' / 'policy_violations.json'
        assert not vio.exists()
    finally:
        os.chdir(old_cwd)
