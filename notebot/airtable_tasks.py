import httpx, os
from dotenv import load_dotenv

load_dotenv()

def log_tasks(action_items: list, meeting_date: str, meeting_title: str):
    """Log each action item as a separate Airtable record."""
    if not action_items:
        return

    base_id = os.getenv("AIRTABLE_BASE_ID")
    table = os.getenv("AIRTABLE_TABLE_NAME", "Tasks")
    url = f"https://api.airtable.com/v0/{base_id}/{table}"
    headers = {
        "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
        "Content-Type": "application/json"
    }

    records = []
    for item in action_items:
        if ": " in item:
            owner, task = item.split(": ", 1)
        else:
            owner, task = "Unassigned", item

        records.append({
            "fields": {
                "Task": task.strip(),
                "Owner": owner.strip(),
                "Meeting Date": meeting_date,
                "Meeting Title": meeting_title,
                "Status": "To Do"
            }
        })

    # Airtable allows max 10 records per request
    for i in range(0, len(records), 10):
        batch = records[i:i+10]
        with httpx.Client() as client:
            client.post(url, headers=headers, json={"records": batch})
