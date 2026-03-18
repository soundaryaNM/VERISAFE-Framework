"""
Planner: generate scenarios.json from repo analysis using the planner LLM role.
"""
from pathlib import Path
import json
import time
import requests
from .llm_config import get_model_for_role


class Planner:
    def __init__(self, repo_path: str, model_choice: str = 'ollama', ollama_url: str = 'http://127.0.0.1:11434/api/generate') -> None:
        self.repo_path = Path(repo_path).resolve()
        self.model_choice = model_choice
        self.ollama_url = ollama_url
        self.enable_streaming = True if model_choice == 'ollama' else False

    def _call_ollama(self, model_tag: str, prompt: str) -> str:
        payload = {"model": model_tag, "prompt": prompt, "stream": bool(self.enable_streaming)}
        if self.enable_streaming:
            with requests.post(self.ollama_url, json=payload, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                acc = []
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    try:
                        if isinstance(raw, bytes):
                            text = raw.decode('utf-8', errors='replace')
                        else:
                            text = str(raw)
                        # Ollama streams JSON event objects per-line; extract the 'response' field when possible
                        try:
                            evt = json.loads(text)
                            piece = evt.get('response', '')
                        except Exception:
                            piece = text
                        acc.append(piece)
                        print(piece, end='', flush=True)
                    except Exception:
                        pass
                print()
                return ''.join(acc)
        else:
            r = requests.post(self.ollama_url, json=payload, timeout=300)
            r.raise_for_status()
            try:
                return r.json().get('response', r.text)
            except Exception:
                return r.text

    def build_prompt(self, repo_scan: dict) -> str:
        # Minimal prompt instructing the planner to emit scenarios JSON per schema
        summary = {
            'files': list(repo_scan.get('file_index', {}).keys()),
            'functions': list(repo_scan.get('function_index', {}).keys()),
        }
        prompt = (
            "You are a planner that emits a JSON object matching the project's scenarios.schema.json.\n"
            "Produce a JSON document with keys: schema_version and scenarios.\n"
            "Each scenario should include file, function, inputs, expected, and tags where applicable.\n"
            "Respond ONLY with the JSON output (no commentary).\n\n"
            f"Repository summary: {json.dumps(summary)}\n"
        )
        return prompt

    def plan(self, repo_scan: dict) -> dict:
        model_tag = get_model_for_role('planner')
        prompt = self.build_prompt(repo_scan)
        raw = self._call_ollama(model_tag, prompt)
        # Try to parse JSON from the response
        try:
            obj = json.loads(raw)
        except Exception:
            # Attempt to find JSON substring
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end != -1 and end > start:
                try:
                    obj = json.loads(raw[start:end+1])
                except Exception:
                    raise RuntimeError('Planner produced non-JSON output')
            else:
                raise RuntimeError('Planner produced non-JSON output')

        # Persist to work/scenarios.json
        out_dir = self.repo_path / 'work'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / 'scenarios.json'
        out_path.write_text(json.dumps(obj, indent=2), encoding='utf-8', newline='\n')
        return obj
