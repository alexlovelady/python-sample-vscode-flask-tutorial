import discord
from discord.ext import commands
import asyncio, os, time
from datetime import datetime
from dotenv import load_dotenv

from transcriber import transcribe_audio
from summarizer import summarize_transcript
from formatter import build_embed
from google_docs import create_meeting_doc
from airtable_tasks import log_tasks
from db import init_db, save_meeting

load_dotenv()
init_db()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

connections = {}     # guild_id → voice_client
start_times = {}     # guild_id → unix timestamp
channel_names = {}   # guild_id → voice channel name

@bot.event
async def on_ready():
    await bot.sync_commands()
    print(f"✅ NoteBot online as {bot.user}")
    print(f"📊 Dashboard: http://localhost:{os.getenv('DASHBOARD_PORT', 8080)}")

@bot.slash_command(name="join", description="Join your voice channel and start recording")
async def join(ctx):
    if not ctx.author.voice:
        return await ctx.respond("❌ You need to be in a voice channel first.", ephemeral=True)

    channel = ctx.author.voice.channel
    vc = await channel.connect()
    connections[ctx.guild.id] = vc
    start_times[ctx.guild.id] = time.time()
    channel_names[ctx.guild.id] = channel.name

    vc.start_recording(
        discord.sinks.WaveSink(),
        recording_finished,
        ctx.channel,
        ctx.guild.id
    )

    await ctx.respond(
        f"🎙️ Recording **{channel.name}** — type `/leave` when the meeting ends."
    )

async def recording_finished(sink, text_channel, guild_id):
    duration = int((time.time() - start_times.pop(guild_id, time.time())) / 60)
    voice_channel = channel_names.pop(guild_id, "Unknown")

    status_msg = await text_channel.send("⏳ Transcribing audio...")

    full_transcript_lines = []
    speaker_names = []

    for user_id, audio in sink.audio_data.items():
        try:
            user = await bot.fetch_user(user_id)
            speaker_names.append(user.display_name)
            audio_bytes = audio.file.read()
            text = await asyncio.to_thread(transcribe_audio, audio_bytes)
            if text.strip():
                full_transcript_lines.append(f"{user.display_name}: {text.strip()}")
        except Exception as e:
            print(f"Transcription error for user {user_id}: {e}")

    if not full_transcript_lines:
        await status_msg.edit(content="❌ No audio detected — nothing to summarize.")
        return

    transcript = "\n".join(full_transcript_lines)
    await status_msg.edit(content="🧠 Summarizing with Nemotron...")

    try:
        summary = await summarize_transcript(transcript)
    except Exception as e:
        await status_msg.edit(content=f"❌ Summarization failed: {e}")
        return

    meeting_date = datetime.utcnow().strftime("%Y-%m-%d")
    meeting_title = f"AMP Titans Meeting — {meeting_date}"

    # 1. Google Doc
    doc_url = ""
    try:
        await status_msg.edit(content="📄 Creating Google Doc...")
        doc_url = await asyncio.to_thread(
            create_meeting_doc, meeting_title, summary, transcript, speaker_names
        )
    except Exception as e:
        print(f"Google Docs error: {e}")

    # 2. Airtable tasks
    try:
        await status_msg.edit(content="📌 Logging tasks to Airtable...")
        await asyncio.to_thread(
            log_tasks, summary.get("action_items", []), meeting_date, meeting_title
        )
    except Exception as e:
        print(f"Airtable error: {e}")

    # 3. Save to SQLite
    meeting_id = save_meeting({
        "date": meeting_date,
        "channel": voice_channel,
        "duration_minutes": duration,
        "speakers": speaker_names,
        "summary": summary.get("summary", ""),
        "topics": summary.get("topics", []),
        "decisions": summary.get("decisions", []),
        "action_items": summary.get("action_items", []),
        "blockers": summary.get("blockers", []),
        "transcript": transcript,
        "google_doc_url": doc_url
    })

    # 4. Discord embed
    embed = build_embed(summary, speaker_names, duration, doc_url, meeting_id)
    await status_msg.delete()
    await text_channel.send(embed=embed)

@bot.slash_command(name="leave", description="Stop recording and generate meeting notes")
async def leave(ctx):
    if ctx.guild.id not in connections:
        return await ctx.respond("❌ I'm not recording right now.", ephemeral=True)

    vc = connections.pop(ctx.guild.id)
    vc.stop_recording()
    await vc.disconnect()
    await ctx.respond("✅ Stopped. Processing notes now...")

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
