import os
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive"
]

def get_google_creds():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds

def create_meeting_doc(
    title: str,
    summary: dict,
    transcript: str,
    speakers: list
) -> str:
    """Creates a Google Doc and returns its URL."""
    creds = get_google_creds()
    docs = build("docs", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    doc = docs.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if folder_id:
        file = drive.files().get(fileId=doc_id, fields="parents").execute()
        drive.files().update(
            fileId=doc_id,
            addParents=folder_id,
            removeParents=",".join(file.get("parents", [])),
            fields="id, parents"
        ).execute()

    date_str = datetime.utcnow().strftime("%B %d, %Y")
    speakers_str = ", ".join(speakers) if speakers else "Unknown"

    content_blocks = []
    content_blocks.append(("AMP Titans — Meeting Notes", "TITLE"))
    content_blocks.append((f"{date_str}  |  Attendees: {speakers_str}", "SUBTITLE"))
    content_blocks.append(("", "NORMAL_TEXT"))

    content_blocks.append(("Summary", "HEADING_1"))
    content_blocks.append((summary.get("summary", ""), "NORMAL_TEXT"))
    content_blocks.append(("", "NORMAL_TEXT"))

    content_blocks.append(("Topics Discussed", "HEADING_1"))
    for t in summary.get("topics", []):
        content_blocks.append((f"• {t}", "NORMAL_TEXT"))
    content_blocks.append(("", "NORMAL_TEXT"))

    content_blocks.append(("Decisions Made", "HEADING_1"))
    for d in summary.get("decisions", []):
        content_blocks.append((f"• {d}", "NORMAL_TEXT"))
    content_blocks.append(("", "NORMAL_TEXT"))

    content_blocks.append(("Action Items", "HEADING_1"))
    for a in summary.get("action_items", []):
        content_blocks.append((f"• {a}", "NORMAL_TEXT"))
    content_blocks.append(("", "NORMAL_TEXT"))

    content_blocks.append(("Blockers & Open Questions", "HEADING_1"))
    for b in summary.get("blockers", []):
        content_blocks.append((f"• {b}", "NORMAL_TEXT"))
    content_blocks.append(("", "NORMAL_TEXT"))

    content_blocks.append(("Full Transcript", "HEADING_1"))
    content_blocks.append((transcript, "NORMAL_TEXT"))

    # Insert in reverse so index 1 ordering works correctly
    insert_requests = []
    for text, style in reversed(content_blocks):
        insert_requests.append({
            "insertText": {"location": {"index": 1}, "text": text + "\n"}
        })

    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": insert_requests}
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"
