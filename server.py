#!/usr/bin/env python3
"""
Mobile-friendly chess analysis web server.

Usage:
  uvicorn server:app --host 0.0.0.0 --port 8000

Then open the forwarded Codespace URL in any browser.
Requires ANTHROPIC_API_KEY environment variable.
"""

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
import anthropic

sys.path.insert(0, str(Path(__file__).parent))
from analyze import analyze_game, DEPTH_TIERS
from chat import build_system_prompt

app = FastAPI()


class AnalyzeRequest(BaseModel):
    pgn: str
    tier: str = "standard"


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    analysis: dict
    messages: list[Message]


def sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.get("/")
async def index():
    return HTMLResponse(Path("static/index.html").read_text())


@app.post("/analyze")
async def analyze_endpoint(req: AnalyzeRequest):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def run():
        try:
            def on_move(move_data: dict):
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "move", "data": move_data}), loop
                )

            t = DEPTH_TIERS.get(req.tier, DEPTH_TIERS["standard"])
            result = analyze_game(
                req.pgn,
                depth=t["depth"],
                critical_depth=t["critical_depth"],
                pv_length=t["pv"],
                critical_pv_length=t["critical_pv"],
                progress_callback=on_move,
                silent=True,
            )
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "done", "analysis": result}), loop
            )
        except FileNotFoundError:
            asyncio.run_coroutine_threadsafe(
                queue.put({
                    "type": "error",
                    "message": "Stockfish not found. Install with: sudo apt-get update && sudo apt-get install stockfish",
                }),
                loop,
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(e)}), loop
            )

    threading.Thread(target=run, daemon=True).start()

    async def generate():
        while True:
            item = await asyncio.wait_for(queue.get(), timeout=600)
            yield sse(item)
            if item["type"] in ("done", "error"):
                break

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is not set on the server. Add it as a Codespace secret.",
        )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    system = build_system_prompt(req.analysis)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    async def generate():
        try:
            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield sse({"type": "token", "text": text})
            yield sse({"type": "done"})
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)
