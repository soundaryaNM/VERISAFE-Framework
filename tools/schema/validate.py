#!/usr/bin/env python3
"""
Minimal schema validator utility for VERISAFE.

Behavior:
- validate_or_halt(obj, schema_path, artifact_name)
  - validates JSON object against schema
  - on failure writes exactly one `work/policy_violations.json` and exits non-zero
  - never lets exceptions escape
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from typing import Any, Dict
from jsonschema import validate, ValidationError, SchemaError


WORK_DIR = "work"
VIOLATIONS_PATH = os.path.join(WORK_DIR, "policy_violations.json")


def _ensure_work_dir():
    os.makedirs(WORK_DIR, exist_ok=True)


def load_schema(schema_path: str) -> Dict[str, Any]:
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    dirpath = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, sort_keys=True)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _write_violation(artifact_name: str, schema_path: str, errors: Any, schema_version: str = None) -> None:
    _ensure_work_dir()
    payload = {
        "schema_version": schema_version or "unknown",
        "artifact_name": artifact_name,
        "schema_path": schema_path,
        "errors": errors if isinstance(errors, list) else [str(errors)],
        "received_at": datetime.utcnow().isoformat() + "Z"
    }
    _atomic_write(VIOLATIONS_PATH, payload)


def validate_or_halt(obj: Dict[str, Any], schema_path: str, artifact_name: str) -> None:
    """
    Validate `obj` against JSON schema at `schema_path`.
    On success: return None.
    On failure: write exactly one `work/policy_violations.json` and exit non-zero.
    This function must not let exceptions propagate to caller.
    """
    try:
        schema = load_schema(schema_path)
        # Extract schema_version if present in schema definition
        schema_version = None
        try:
            sv = schema.get("properties", {}).get("schema_version", {})
            # support const or enum
            if "const" in sv:
                schema_version = sv["const"]
            elif "enum" in sv and isinstance(sv["enum"], list) and len(sv["enum"]) > 0:
                schema_version = sv["enum"][0]
        except Exception:
            schema_version = None

        validate(instance=obj, schema=schema)
        return
    except ValidationError as ve:
        errors = [{"message": ve.message, "path": list(ve.path), "schema_path": list(ve.schema_path)}]
        _write_violation(artifact_name=artifact_name, schema_path=schema_path, errors=errors, schema_version=schema_version)
        # fail-fast: single exit point
        sys.exit(2)
    except SchemaError as se:
        errors = [{"message": "Schema error: " + str(se)}]
        _write_violation(artifact_name=artifact_name, schema_path=schema_path, errors=errors, schema_version=schema_version)
        sys.exit(3)
    except Exception as e:
        # Unexpected error: write generic violation and exit non-zero
        errors = [{"message": "Unexpected validator error: " + str(e)}]
        _write_violation(artifact_name=artifact_name, schema_path=schema_path, errors=errors, schema_version=schema_version)
        sys.exit(4)
