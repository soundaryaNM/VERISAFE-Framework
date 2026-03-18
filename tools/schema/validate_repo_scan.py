import json
import sys
try:
    from jsonschema import Draft7Validator
except Exception:
    print('jsonschema package is required')
    sys.exit(2)

with open('work/repo_scan.json','r',encoding='utf-8') as f:
    data = json.load(f)
with open('schemas/repo_scan.schema.json','r',encoding='utf-8') as f:
    schema = json.load(f)

validator = Draft7Validator(schema)
errors = list(validator.iter_errors(data))
if errors:
    print('INVALID')
    for e in errors:
        print('-', e.message)
    sys.exit(2)
print('Validation OK')
