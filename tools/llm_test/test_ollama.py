"""Quick connectivity and latency test for local Ollama models.

Usage: run the project's venv python to execute this script.
"""
import time
import json
import requests
from CW_Test_Gen.ai_c_test_generator.llm_config import get_model_for_role


def main():
    roles = ["planner", "synthesizer", "fix_it"]
    print("Model mapping:")
    for r in roles:
        print(f" - {r}: {get_model_for_role(r)}")

    ollama_url = "http://127.0.0.1:11434/api/generate"
    model = get_model_for_role("synthesizer")
    payload = {"model": model, "prompt": "Ping", "stream": False}

    print(f"\nTesting Ollama endpoint {ollama_url} with model {model} (5s timeout)...")
    start = time.time()
    try:
        resp = requests.post(ollama_url, json=payload, timeout=5)
        elapsed = time.time() - start
        print(f"HTTP {resp.status_code} in {elapsed:.2f}s")
        try:
            js = resp.json()
            print("Response keys:", ",".join(js.keys()))
            # print truncated body
            body = json.dumps(js)[:1000]
            print("Response (truncated):", body)
        except Exception:
            print("Non-JSON response, length:", len(resp.text))
    except Exception as e:
        elapsed = time.time() - start
        print(f"Request failed after {elapsed:.2f}s: {e}")


if __name__ == "__main__":
    main()
