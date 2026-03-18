import requests,sys,json
endpoints=['/v1/models','/api/models']
base='http://127.0.0.1:11434'
models=None
print('Probing model-list endpoints...')
for ep in endpoints:
    url=base+ep
    try:
        r=requests.get(url, timeout=10)
        print('GET', ep, '->', r.status_code)
        text=r.text
        print(text[:2000])
        try:
            j=r.json()
            print('JSON OK; type=', type(j))
            if isinstance(j, list) and j:
                first=j[0]
                if isinstance(first, dict) and 'name' in first:
                    models=[m.get('name') for m in j if isinstance(m, dict) and 'name' in m]
                else:
                    models=[str(x) for x in j]
                break
            elif isinstance(j, dict) and 'models' in j:
                arr=j['models']
                if isinstance(arr, list) and arr:
                    models=[(m.get('id') or m.get('name') or str(m)) for m in arr]
                    break
        except Exception:
            pass
    except Exception as e:
        print('ERROR', repr(e))

if not models:
    print('\nNo model list found; will still attempt a generate POST to common endpoints.')
else:
    print('\nDetected models:', models[:5])

# Try generate endpoints
gen_endpoints=['/api/generate','/v1/generate']
prompt='Respond with: OK'
for ep in gen_endpoints:
    url=base+ep
    model_name = models[0] if models else 'ollama'
    payload={'model': model_name, 'prompt': prompt, 'stream': False}
    try:
        print('POST', ep, 'with model=', model_name, '->', end=' ')
        r=requests.post(url, json=payload, timeout=120)
        print(r.status_code)
        text=r.text
        print('--- response start ---')
        print(text[:4000])
        print('--- response end ---')
        break
    except Exception as e:
        print('ERROR', repr(e))

print('\nDone')
