import discord
from datetime import datetime

def build_embed(
    summary: dict,
    speakers: list,
    duration: int,
    doc_url: str,
    meeting_id: int
) -> discord.Embed:

    embed = discord.Embed(
        title="📋 Meeting Notes",
        description=summary.get("summary", "No summary available."),
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )

    if summary.get("topics"):
        embed.add_field(
            name="🗂️ Topics",
            value="\n".join(f"• {t}" for t in summary["topics"]),
            inline=False
        )

    if summary.get("decisions"):
        embed.add_field(
            name="✅ Decisions",
            value="\n".join(f"• {d}" for d in summary["decisions"]),
            inline=False
        )

    if summary.get("action_items"):
        embed.add_field(
            name="📌 Action Items",
            value="\n".join(f"• {a}" for a in summary["action_items"]),
            inline=False
        )

    if summary.get("blockers"):
        embed.add_field(
            name="🚧 Blockers",
            value="\n".join(f"• {b}" for b in summary["blockers"]),
            inline=False
        )

    links = []
    if doc_url:
        links.append(f"[📄 Google Doc]({doc_url})")
    links.append(f"[📊 Dashboard](http://localhost:8080/meeting/{meeting_id})")

    embed.add_field(name="🔗 Links", value="  |  ".join(links), inline=False)

    embed.set_footer(
        text=f"AMP Titans NoteBot • {', '.join(speakers)} • {duration} min"
    )
    return embed
