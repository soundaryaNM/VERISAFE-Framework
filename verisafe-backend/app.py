from flask import Flask, request, Response, stream_with_context
import subprocess
import shlex
import os
import sys

app = Flask(__name__)

# Simple SSE helper
def sse_event(data: str):
    return f"data: {data}\n\n"

@app.route('/generate', methods=['POST'])
def generate():
    """Run the demo generation command and stream stdout/stderr as Server-Sent Events.

    Expects JSON or form data with optional keys: repo_path, source_dir, target_file, model
    """
    repo = request.form.get('repo_path') or (request.json or {}).get('repo_path') or 'RailwaySignalSystem'
    source_dir = request.form.get('source_dir') or (request.json or {}).get('source_dir') or 'src/logic'
    target_file = request.form.get('target_file') or (request.json or {}).get('target_file') or 'Interlocking.cpp'
    model = request.form.get('model') or (request.json or {}).get('model') or 'ollama'

    # Build the command to run generation only via run_demo.py
    # Use the workspace root as cwd
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    py = sys.executable or 'python'
    cmd = [py, 'run_demo.py', '--repo-path', repo, '--only-generation', '--source-dir', source_dir, '--target-file', target_file, '--model', model]

    def stream():
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace'
            )
        except Exception as e:
            yield sse_event(f"ERROR: failed to start process: {e}")
            return

        yield sse_event(f"STARTED: {' '.join(shlex.quote(p) for p in cmd)}")

        assert proc.stdout is not None
        for line in proc.stdout:
            # Trim trailing newlines for SSE
            text = line.rstrip('\n')
            yield sse_event(text)

        rc = proc.wait()
        yield sse_event(f"PROCESS_EXIT: {rc}")

    headers = {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    }
    return Response(stream_with_context(stream()), headers=headers)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=False)
