import json
import hashlib
import sys
from pathlib import Path

# Usage: python convert_analysis.py <advanced_analysis.json> <repo_root> <output_repo_scan.json>

def main():
    if len(sys.argv) != 4:
        print("Usage: python convert_analysis.py <advanced_analysis.json> <repo_root> <output_repo_scan.json>")
        sys.exit(1)

    advanced_path = Path(sys.argv[1])
    repo_root = str(Path(sys.argv[2]).resolve())
    output_path = Path(sys.argv[3])

    with advanced_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Build repo_scan.json structure
    repo_scan = {
        "schema_version": "repo_scan-1.0",
        "repo_root": repo_root,
        "functions": [],
        "call_graph": [],
        "call_depth": {},
        "hardware_flags": {},
    }

    # Functions
    fn_index = data.get("function_index", {})
    for fid, info in fn_index.items():
        name = info.get("name") or fid
        file = info.get("file") or ""
        signature = info.get("signature") or name
        body = info.get("body") or ""
        source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if isinstance(body, str) else ""
        repo_scan["functions"].append({
            "id": fid,
            "name": name,
            "file": file,
            "signature": signature,
            "source_hash": source_hash,
        })

    # Call graph
    cg = data.get("call_graph", {})
    if isinstance(cg, dict):
        for caller, callees in cg.items():
            if isinstance(callees, list):
                for callee in callees:
                    repo_scan["call_graph"].append({"caller_id": caller, "callee_id": callee})

    # Call depth
    cd = data.get("call_depths") or data.get("call_depth") or {}
    if isinstance(cd, dict):
        for k, v in cd.items():
            repo_scan["call_depth"][k] = int(v)

    # Hardware flags
    hf = data.get("hardware_flags", {})
    if isinstance(hf, dict):
        repo_scan["hardware_flags"] = {k: bool(v) for k, v in hf.items()}

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(repo_scan, f, indent=2)
    print(f"Wrote schema-compliant repo_scan.json to {output_path}")

if __name__ == "__main__":
    main()
