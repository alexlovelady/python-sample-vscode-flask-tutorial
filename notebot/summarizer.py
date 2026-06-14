import httpx, os, json
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a professional meeting notes assistant for AMP Titans LLC.
Analyze the transcript and return ONLY a valid JSON object with these exact keys:
- "summary": string — 3-5 sentence overview
- "topics": array of strings — main topics discussed
- "decisions": array of strings — decisions made
- "action_items": array of strings — each formatted as "Owner: Task description"
- "blockers": array of strings — blockers or open questions

Return only the JSON object. No markdown fences, no preamble, no explanation."""

async def summarize_transcript(transcript: str) -> dict:
    url = f"{os.getenv('VLLM_BASE_URL')}/chat/completions"
    payload = {
        "model": os.getenv("VLLM_MODEL"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"}
        ],
        "temperature": 0.2,
        "max_tokens": 2000
    }

    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()

    content = r.json()["choices"][0]["message"]["content"]
    content = content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(content)
