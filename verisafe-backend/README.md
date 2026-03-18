# VERISAFE Backend (Streaming Generation)

This small Flask app exposes a `/generate` endpoint that runs the demo generation and streams live logs as Server-Sent Events.

Run (from workspace root):

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r verisafe-backend/requirements.txt
python verisafe-backend/app.py
```

Then POST to `http://127.0.0.1:8080/generate` (form or JSON) with optional fields: `repo_path`, `source_dir`, `target_file`, `model`.

Example (curl):

```bash
curl -N -X POST http://127.0.0.1:8080/generate -d "repo_path=RailwaySignalSystem" -d "model=ollama"
```

In the browser, use an `EventSource('/generate'...)` or similar client to receive real-time `data:` events.
