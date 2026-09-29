import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from hindsight_client import Hindsight
from pydantic import BaseModel, Field
from pptx import Presentation

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(exist_ok=True)
load_dotenv(ROOT.parent / ".env")

app = FastAPI(title="ContextForge")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/downloads", StaticFiles(directory=OUTPUTS), name="downloads")


class AnalyzeRequest(BaseModel):
    conversation: str = Field(min_length=3)
    selections: list[str] = Field(default_factory=lambda: ["Knowledge"])
    focus: str = ""
    bank_id: str = "contextforge-demo-v2"


class ExportRequest(BaseModel):
    format: Literal["txt", "md", "docx", "pptx"]
    title: str = "ContextForge Brief"
    content: str
    memories: list[str] = Field(default_factory=list)


def hindsight_client() -> Hindsight:
    url = os.getenv("HINDSIGHT_API_URL")
    if not url:
        raise HTTPException(503, "Hindsight is not configured. Set HINDSIGHT_API_URL; ContextForge never uses mock memory.")
    kwargs: dict[str, Any] = {"base_url": url}
    if os.getenv("HINDSIGHT_API_KEY"):
        kwargs["api_key"] = os.environ["HINDSIGHT_API_KEY"]
    return Hindsight(**kwargs)


def normalize(value: Any) -> list[str]:
    """Extract readable memory text across Hindsight client response versions."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict"):
        value = value.dict()
    if isinstance(value, dict):
        for key in ("results", "memories", "items", "data"):
            if key in value and isinstance(value[key], list):
                return normalize(value[key])
        return [str(value.get("content") or value.get("text") or value.get("memory") or value)]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(normalize(item))
        return list(dict.fromkeys(out))
    return [str(value)]


async def llm_extract(conversation: str, selections: list[str], focus: str) -> dict[str, list[str]] | None:
    key, endpoint, model = None, None, None
    if os.getenv("OPENROUTER_API_KEY"):
        key = os.environ["OPENROUTER_API_KEY"]
        endpoint = "https://openrouter.ai/api/v1/chat/completions"
        model = os.getenv("OPENROUTER_MODEL", "x-ai/grok-4.3")
    elif os.getenv("GROQ_API_KEY"):
        key, endpoint, model = os.environ["GROQ_API_KEY"], "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"
    elif os.getenv("GEMINI_API_KEY"):
        key = os.environ["GEMINI_API_KEY"]
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
    if not key:
        return None
    prompt = f"Extract only these categories: {', '.join(selections)}. {('Custom focus: ' + focus) if focus else ''} Return a strict JSON object mapping each requested category to a non-empty array of concise factual strings drawn from the conversation. Every requested category must have at least one item. Conversation:\n{conversation}"
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            if model:
                response = await client.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json={"model": model, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "max_tokens": 2000})
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]
            else:
                response = await client.post(endpoint, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}})
                response.raise_for_status()
                raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
        cleaned: dict[str, list[str]] = {}
        for key, value in parsed.items():
            items = value if isinstance(value, list) else [value]
            items = [str(item).strip() for item in items if str(item).strip()]
            if items:
                cleaned[key] = items
        return cleaned or None
    except Exception:
        return None


def local_extract(conversation: str, selections: list[str], focus: str) -> dict[str, list[str]]:
    clean = " ".join(conversation.split())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    result: dict[str, list[str]] = {}
    for choice in selections:
        lower = choice.lower()
        if lower == "preferences":
            matches = [s for s in sentences if re.search(r"\b(prefer|keep|use|avoid|like|want)\b", s, re.I)]
            result[choice] = matches or ["No explicit preference found in this conversation."]
        elif lower == "decisions":
            matches = [s for s in sentences if re.search(r"\b(decide|will|choose|agreed|should)\b", s, re.I)]
            result[choice] = matches or ["No explicit decision found."]
        elif lower == "reusable skill":
            result[choice] = ["Reusable workflow derived from the conversation: " + (sentences[0] if sentences else clean[:220])]
        elif lower == "custom focus":
            result[choice] = [f"Focus ({focus or 'general'}): " + (sentences[0] if sentences else clean[:220])]
        else:
            result[choice] = sentences[:3] or [clean[:280]]
    return result


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    try:
        hindsight_client()
        return {"hindsight": "configured", "message": "Hindsight is configured. Retain/recall will be verified on use."}
    except HTTPException as exc:
        return {"hindsight": "needs configuration", "message": exc.detail}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".txt", ".md")):
        raise HTTPException(400, "Upload a .txt or .md conversation file.")
    data = await file.read()
    return {"conversation": data.decode("utf-8", errors="replace")}


@app.post("/api/analyze")
async def analyze(payload: AnalyzeRequest):
    memory = hindsight_client()
    try:
        try:
            recalled = normalize(await memory.arecall(bank_id=payload.bank_id, query=payload.conversation))[:6]
        except Exception as exc:
            # A first-time ContextForge user has no bank yet. Create its Hindsight bank,
            # then continue with an empty recall result rather than using any fallback store.
            if "not found" not in str(exc).lower():
                raise HTTPException(503, f"Hindsight recall failed: {exc}")
            try:
                await memory.acreate_bank(
                    bank_id=payload.bank_id,
                    name="ContextForge memory",
                    mission="Retain reusable knowledge, decisions, skills, and user preferences from conversations.",
                )
                recalled = []
            except Exception as exc:
                raise HTTPException(503, f"Hindsight bank setup failed: {exc}")

        extracted = await llm_extract(payload.conversation, payload.selections, payload.focus) or local_extract(payload.conversation, payload.selections, payload.focus)
        # Guarantee every category the user asked for is represented, backfilling
        # any the LLM omitted or returned empty with the deterministic extractor.
        fallback = local_extract(payload.conversation, payload.selections, payload.focus)
        for category in payload.selections:
            if not extracted.get(category):
                extracted[category] = fallback[category]
        retained = []
        for category, facts in extracted.items():
            for fact in facts:
                if fact.startswith("No explicit"):
                    continue
                record = f"[{category}] {fact}"
                try:
                    await memory.aretain(bank_id=payload.bank_id, content=record)
                    retained.append(record)
                except Exception as exc:
                    raise HTTPException(503, f"Hindsight retain failed: {exc}")
        return {"extracted": extracted, "memories_recalled": recalled, "memories_retained": retained, "personalization_note": "Output will follow recalled preferences." if recalled else "No prior memory was found; this conversation has now been retained."}
    finally:
        await memory.aclose()


@app.post("/api/export")
def export(payload: ExportRequest):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = payload.format
    name = f"contextforge-{stamp}-{uuid.uuid4().hex[:6]}.{suffix}"
    path = OUTPUTS / name
    personalization = "\n\nMemories recalled and applied:\n" + "\n".join(f"• {m}" for m in payload.memories) if payload.memories else ""
    body = payload.content + personalization
    if suffix in ("txt", "md"):
        path.write_text((f"# {payload.title}\n\n" if suffix == "md" else f"{payload.title}\n{'=' * len(payload.title)}\n\n") + body, encoding="utf-8")
    elif suffix == "docx":
        doc = Document(); doc.add_heading(payload.title, 0)
        for paragraph in body.split("\n\n"): doc.add_paragraph(paragraph)
        doc.save(path)
    else:
        prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[1]); slide.shapes.title.text = payload.title
        slide.placeholders[1].text = body[:1800]
        prs.save(path)
    size = path.stat().st_size
    return {
        "url": f"/downloads/{name}",
        "filename": name,
        "format": suffix,
        "saved_path": str(path.relative_to(ROOT.parent)),
        "size": size,
        "memories_applied": payload.memories,
    }


@app.get("/api/outputs")
def list_outputs():
    """List every export saved on the backend, newest first."""
    files = sorted(
        (p for p in OUTPUTS.iterdir() if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return {
        "outputs": [
            {
                "filename": p.name,
                "url": f"/downloads/{p.name}",
                "format": p.suffix.lstrip("."),
                "size": p.stat().st_size,
                "saved_path": str(p.relative_to(ROOT.parent)),
            }
            for p in files
        ]
    }
