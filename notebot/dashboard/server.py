import uvicorn, os, json, asyncio
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv
from datetime import datetime
from typing import List

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_all_meetings, get_meeting, save_meeting
from transcriber import transcribe_audio
from summarizer import summarize_transcript
from google_docs import create_meeting_doc
from airtable_tasks import log_tasks

load_dotenv()

_here = os.path.dirname(os.path.abspath(__file__))
jinja_env = Environment(loader=FileSystemLoader(os.path.join(_here, "templates")))

app = FastAPI(title="AMP Titans NoteBot Dashboard")
app.mount("/static", StaticFiles(directory=os.path.join(_here, "static")), name="static")

def parse_json_field(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value or []

def render(template_name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(jinja_env.get_template(template_name).render(**ctx))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    meetings = get_all_meetings()
    for m in meetings:
        m["topics"] = parse_json_field(m["topics"])
        m["action_items"] = parse_json_field(m["action_items"])
        m["speakers"] = parse_json_field(m["speakers"])
    return render("index.html",
        meetings=meetings,
        title=os.getenv("DASHBOARD_TITLE", "AMP Titans Meeting Notes")
    )

@app.get("/meeting/{meeting_id}", response_class=HTMLResponse)
async def meeting_detail(request: Request, meeting_id: int):
    meeting = get_meeting(meeting_id)
    if not meeting:
        return HTMLResponse("Meeting not found", status_code=404)
    for field in ["topics", "decisions", "action_items", "blockers", "speakers"]:
        meeting[field] = parse_json_field(meeting[field])
    return render("meeting.html",
        meeting=meeting,
        title=os.getenv("DASHBOARD_TITLE", "AMP Titans Meeting Notes")
    )

@app.post("/process")
async def process_recording(
    channel: str = Form(...),
    duration: str = Form(...),
    audio_files: List[UploadFile] = File(...),
    speaker_names: List[str] = Form(...),
):
    duration_min = int(duration)
    meeting_date = datetime.utcnow().strftime("%Y-%m-%d")
    meeting_title = f"AMP Titans Meeting — {meeting_date}"

    # Transcribe each speaker
    full_transcript_lines = []
    for audio_file, display_name in zip(audio_files, speaker_names):
        try:
            ogg_bytes = await audio_file.read()
            if not ogg_bytes:
                continue
            text = await asyncio.to_thread(transcribe_audio, ogg_bytes)
            if text.strip():
                full_transcript_lines.append(f"{display_name}: {text.strip()}")
        except Exception as e:
            print(f"[transcribe] error for {display_name}: {e}")

    if not full_transcript_lines:
        return JSONResponse({"error": "No audio transcribed"}, status_code=422)

    transcript = "\n".join(full_transcript_lines)

    # Summarize
    try:
        summary = await summarize_transcript(transcript)
    except Exception as e:
        return JSONResponse({"error": f"Summarization failed: {e}"}, status_code=500)

    # Google Doc
    doc_url = ""
    try:
        doc_url = await asyncio.to_thread(
            create_meeting_doc, meeting_title, summary, transcript, speaker_names
        )
    except Exception as e:
        print(f"[google_docs] error: {e}")

    # Airtable
    try:
        await asyncio.to_thread(
            log_tasks, summary.get("action_items", []), meeting_date, meeting_title
        )
    except Exception as e:
        print(f"[airtable] error: {e}")

    # SQLite
    meeting_id = save_meeting({
        "date": meeting_date,
        "channel": channel,
        "duration_minutes": duration_min,
        "speakers": speaker_names,
        "summary": summary.get("summary", ""),
        "topics": summary.get("topics", []),
        "decisions": summary.get("decisions", []),
        "action_items": summary.get("action_items", []),
        "blockers": summary.get("blockers", []),
        "transcript": transcript,
        "google_doc_url": doc_url,
    })

    return JSONResponse({
        "meeting_id": meeting_id,
        "summary": summary.get("summary", ""),
        "topics": summary.get("topics", []),
        "decisions": summary.get("decisions", []),
        "action_items": summary.get("action_items", []),
        "blockers": summary.get("blockers", []),
        "speakers": speaker_names,
        "duration": duration_min,
        "google_doc_url": doc_url,
    })

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=port, reload=False)
