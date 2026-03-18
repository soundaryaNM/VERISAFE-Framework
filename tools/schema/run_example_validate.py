#!/usr/bin/env python3
"""Run a quick validation of work/example_repo_scan.json using the schema validator."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.schema.validate import validate_or_halt


def main():
    p = Path(ROOT) / 'work' / 'example_repo_scan.json'
    if not p.exists():
        print("Example artifact not found:", p)
        sys.exit(1)
    obj = json.loads(p.read_text(encoding='utf-8'))
    schema_path = Path(ROOT) / 'schemas' / 'repo_scan.schema.json'
    validate_or_halt(obj, str(schema_path), artifact_name='example_repo_scan.json')
    print("Validation OK")


if __name__ == '__main__':
    main()
