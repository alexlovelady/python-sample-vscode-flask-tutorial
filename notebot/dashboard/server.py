import uvicorn, os, json
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_all_meetings, get_meeting

load_dotenv()

app = FastAPI(title="AMP Titans NoteBot Dashboard")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

def parse_json_field(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value or []

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    meetings = get_all_meetings()
    for m in meetings:
        m["topics"] = parse_json_field(m["topics"])
        m["action_items"] = parse_json_field(m["action_items"])
        m["speakers"] = parse_json_field(m["speakers"])
    return templates.TemplateResponse("index.html", {
        "request": request,
        "meetings": meetings,
        "title": os.getenv("DASHBOARD_TITLE", "AMP Titans Meeting Notes")
    })

@app.get("/meeting/{meeting_id}", response_class=HTMLResponse)
async def meeting_detail(request: Request, meeting_id: int):
    meeting = get_meeting(meeting_id)
    if not meeting:
        return HTMLResponse("Meeting not found", status_code=404)
    for field in ["topics", "decisions", "action_items", "blockers", "speakers"]:
        meeting[field] = parse_json_field(meeting[field])
    return templates.TemplateResponse("meeting.html", {
        "request": request,
        "meeting": meeting,
        "title": os.getenv("DASHBOARD_TITLE", "AMP Titans Meeting Notes")
    })

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=port, reload=False)
