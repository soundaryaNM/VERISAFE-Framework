"""Benchmark local Ollama models: cold vs warm latency for roles.

Run with the project's venv python.
"""
import time
import requests
import sys
from pathlib import Path

# Ensure repo root is on sys.path so CW_Test_Gen package can be imported when
# running this script directly from tools/llm_test
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from CW_Test_Gen.ai_c_test_generator.llm_config import get_model_for_role


def ping(model, timeout=120):
    url = "http://127.0.0.1:11434/api/generate"
    payload = {"model": model, "prompt": "Ping", "stream": False}
    start = time.time()
    resp = requests.post(url, json=payload, timeout=timeout)
    dur = time.time() - start
    return resp, dur


def run_benchmark():
    roles = ["planner", "synthesizer"]
    results = {}
    for role in roles:
        model = get_model_for_role(role)
        print(f"\n=== Role: {role} model: {model} ===")
        # Cold ping
        print("Cold ping (may include model load):")
        try:
            r, t = ping(model, timeout=300)
            print(f" status={r.status_code} time={t:.2f}s")
            try:
                js = r.json()
                print(" response keys:", list(js.keys()))
            except Exception:
                print(" non-json response, len=", len(r.text))
        except Exception as e:
            print(" Cold ping failed:", e)

        # Warm pings
        warm_times = []
        n = 5
        print(f"Warm pings x{n}:")
        for i in range(n):
            try:
                r, t = ping(model, timeout=60)
                warm_times.append(t)
                print(f" {i+1}: status={r.status_code} time={t:.2f}s")
            except Exception as e:
                print(f" {i+1}: failed: {e}")
        if warm_times:
            print(f" warm avg={sum(warm_times)/len(warm_times):.2f}s min={min(warm_times):.2f}s max={max(warm_times):.2f}s")


if __name__ == '__main__':
    run_benchmark()
