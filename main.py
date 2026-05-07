#!/usr/bin/env python3
"""Qobuz-DL WebUI — FastAPI backend that wraps qobuz-dl CLI commands with SSE streaming."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
QOBUZ_DL_BIN = os.environ.get("QOBUZ_DL_BIN", str(Path(__file__).parent / '.venv' / 'bin' / 'qobuz-dl'))
WORK_DIR = os.environ.get("QOBUZ_DL_DIR", ".")
PORT = int(os.environ.get("PORT", "8080"))

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    command: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def get_version() -> str:
    """Get qobuz-dl version from package metadata."""
    try:
        # Try to read version from installed package metadata
        import importlib.metadata as metadata
        return metadata.version("qobuz-dl-ultimate")
    except Exception:
        pass
    try:
        # Fallback: read from setup.py
        setup_path = Path(QOBUZ_DL_BIN).parent.parent.parent / 'setup.py'
        if setup_path.exists():
            content = setup_path.read_text()
            import re
            match = re.search(r'version=["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
    except Exception:
        pass
    return "installed"


def classify_line(line: str) -> str:
    """Classify a log line type."""
    lower = line.lower()
    if any(w in lower for w in ("error", "failed", "fail", "exception", "traceback")):
        return "error"
    if any(w in lower for w in ("warning", "warn", "deprecated")):
        return "warn"
    if any(w in lower for w in ("done", "completed", "success", "saved", "downloaded", "✓")):
        return "ok"
    return "info"


# ---------------------------------------------------------------------------
# SSE stream generator
# ---------------------------------------------------------------------------
async def run_command_stream(command: str):
    """Run a shell command and stream output as SSE events."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORK_DIR,
        )
    except FileNotFoundError:
        yield f"data: {json.dumps({'type': 'error', 'text': f'Binaire non trouvé: {QOBUZ_DL_BIN}'})}\n\n"
        return

    async def read_stream(stream, tag):
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip("\n\r")
            typ = classify_line(text)
            yield f"data: {json.dumps({'type': typ, 'text': text, 'tag': tag})}\n\n"

    # Read stdout and stderr concurrently
    stdout_task = asyncio.create_task(read_stream(proc.stdout, "stdout"))
    stderr_task = asyncio.create_task(read_stream(proc.stderr, "stderr"))

    # Wait for both
    await asyncio.gather(stdout_task, stderr_task)
    await proc.wait()

    yield f"data: {json.dumps({'type': 'ok', 'text': f'Exit code: {proc.returncode}'})}\n\n"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    version = await get_version()
    app.state.qobuz_dl_version = version
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Qobuz-DL WebUI", lifespan=lifespan)

# Serve static files from the build directory
app.mount("/static", StaticFiles(directory=".", html=True), name="static")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": getattr(app.state, "qobuz_dl_version", "unknown"),
        "binary": QOBUZ_DL_BIN,
        "workdir": WORK_DIR,
    }


@app.post("/api/run")
async def run_cmd(req: CommandRequest):
    """Execute a qobuz-dl command and stream the output via SSE."""
    cmd = req.command.strip()
    if not cmd:
        raise HTTPException(400, "Empty command")

    # Safety: only allow qobuz-dl commands
    if not cmd.startswith("qobuz-dl") and not cmd.startswith("QOBUZ_DL"):
        raise HTTPException(400, "Only qobuz-dl commands are allowed")

    return StreamingResponse(
        run_command_stream(cmd),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/", response_class=HTMLResponse)
async def index():
    return Path("index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
