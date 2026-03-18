#!/usr/bin/env python3
import json
import importlib.util
import sys
from pathlib import Path

validator_file = Path('tools') / 'schema' / 'validate.py'
if not validator_file.exists():
    print('Validator not found at tools/schema/validate.py', file=sys.stderr)
    sys.exit(1)

spec = importlib.util.spec_from_file_location('verisafe_validator', str(validator_file))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
validate_or_halt = getattr(mod, 'validate_or_halt', None)
if not validate_or_halt:
    print('validate_or_halt not found in validator module', file=sys.stderr)
    sys.exit(1)

vc_path = Path('work') / 'planning_constraints.json'
schema_path = Path('schemas') / 'planning_constraints.schema.json'
if not vc_path.exists():
    print(f'Artifact not found: {vc_path}', file=sys.stderr)
    sys.exit(1)
if not schema_path.exists():
    print(f'Schema not found: {schema_path}', file=sys.stderr)
    sys.exit(1)

try:
    obj = json.loads(vc_path.read_text(encoding='utf-8'))
except Exception as e:
    print(f'Failed to read artifact: {e}', file=sys.stderr)
    sys.exit(1)

validate_or_halt(obj, str(schema_path), artifact_name=vc_path.name)
print('Validation OK')
