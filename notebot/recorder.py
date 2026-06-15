"""
recorder.py — Discord bot + local audio recorder

Monitors voice state via Discord Gateway (no voice connection needed).
When both trigger users join the same channel, records from:
  • default microphone  → Alex's voice
  • WASAPI loopback     → everything playing through your speakers (Mike via Discord)

Bypasses DAVE E2EE entirely — audio is captured after your Discord client
has already decrypted it.
"""
import os, io, wave, time, asyncio
from threading import Event, Thread

import numpy as np
import soundcard as sc
import httpx
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TRIGGER_USERS  = {236708079872376834, 1074610938684121138}   # Mike, Alex
LOCAL_USER_ID  = 1074610938684121138                          # Alex — whoever runs this machine
GUILD_ID       = int(os.getenv("DISCORD_GUILD_ID", 0))
API_BASE       = f"http://localhost:{os.getenv('DASHBOARD_PORT', '8080')}"
SAMPLE_RATE    = 16000   # Whisper's native rate

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, debug_guilds=[GUILD_ID])

# ─── Recording state ──────────────────────────────────────────────────────────

_recording       = False
_stop_event      = Event()
_mic_frames: list      = []
_loopback_frames: list = []
_threads: list         = []
_start_ts        = 0.0
_rec_chan_id     = None
_rec_chan_name   = ""
_rec_txt_channel = None

# ─── Audio capture threads ────────────────────────────────────────────────────

def _capture_mic(frames: list, stop: Event):
    import warnings, ctypes
    warnings.filterwarnings("ignore", message="data discontinuity")
    try:
        ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass
    try:
        mic   = sc.default_microphone()
        chunk = int(SAMPLE_RATE * 0.5)
        with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=chunk) as r:
            while not stop.is_set():
                frames.append(r.record(numframes=chunk).copy())
        print("[mic] recording finished")
    except BaseException as e:
        print(f"[mic] error: {type(e).__name__}: {e}")
    finally:
        try:
            ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass

def _capture_loopback(frames: list, stop: Event):
    """
    Capture remote speakers via a virtual audio device (e.g. VB-Cable).
    Set LOOPBACK_DEVICE in .env to a substring of the device name, e.g.:
        LOOPBACK_DEVICE=CABLE Output
    Route Discord output → CABLE Input, then listen to CABLE Output
    through your headphones via Windows Sound > Recording > Listen tab.
    """
    import warnings, ctypes
    warnings.filterwarnings("ignore", message="data discontinuity")

    device_hint = os.getenv("LOOPBACK_DEVICE", "")
    if not device_hint:
        print("[loopback] LOOPBACK_DEVICE not set — skipping remote audio capture")
        print("[loopback] Install VB-Cable and set LOOPBACK_DEVICE=CABLE Output in .env")
        return

    try:
        ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass

    try:
        loopback = None
        for m in sc.all_microphones(include_loopback=False):
            if device_hint.lower() in m.name.lower():
                loopback = m
                break

        if loopback is None:
            print(f"[loopback] device matching '{device_hint}' not found — skipping")
            print(f"[loopback] available mics: {[m.name for m in sc.all_microphones()]}")
            return

        print(f"[loopback] capturing from: {loopback.name}")
        chunk = int(SAMPLE_RATE * 0.5)
        with loopback.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=chunk) as r:
            while not stop.is_set():
                frames.append(r.record(numframes=chunk).copy())
        print("[loopback] finished")
    except BaseException as e:
        print(f"[loopback] error: {type(e).__name__}: {e}")
    finally:
        try:
            ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass

def _frames_to_wav(frames: list) -> bytes:
    if not frames:
        return b""
    data = np.concatenate(frames, axis=0).flatten()
    pcm  = (data * 32767).clip(-32768, 32767).astype(np.int16)
    buf  = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()

# ─── Recording lifecycle ──────────────────────────────────────────────────────

def _start_local_recording(chan_name: str):
    global _recording, _mic_frames, _loopback_frames, _threads, _start_ts, _rec_chan_name
    if _recording:
        return
    _recording   = True
    _rec_chan_name = chan_name
    _start_ts    = time.time()
    _stop_event.clear()
    _mic_frames      = []
    _loopback_frames = []

    t1 = Thread(target=_capture_mic,      args=(_mic_frames,      _stop_event), daemon=True)
    t2 = Thread(target=_capture_loopback, args=(_loopback_frames, _stop_event), daemon=True)
    t1.start(); t2.start()
    _threads[:] = [t1, t2]
    print(f"[rec] started — mic + WASAPI loopback for {chan_name!r}")

def _stop_and_encode():
    """Blocking: join threads and encode WAV. Run via asyncio.to_thread."""
    global _recording, _threads
    _stop_event.set()
    for t in _threads:
        t.join(timeout=8)
    _threads.clear()
    _recording = False
    mic_wav      = _frames_to_wav(_mic_frames)
    loopback_wav = _frames_to_wav(_loopback_frames)
    duration     = max(1, int((time.time() - _start_ts) / 60))
    print(f"[rec] finished — mic {len(mic_wav)//1024}KB  loopback {len(loopback_wav)//1024}KB  {duration}min")
    return mic_wav, loopback_wav, duration, _rec_chan_name

# ─── Processing + Discord posting ─────────────────────────────────────────────

async def _get_display_name(guild: discord.Guild, user_id: int) -> str:
    try:
        m = guild.get_member(user_id) or await guild.fetch_member(user_id)
        return m.display_name
    except Exception:
        return "Unknown"

async def process_and_post(txt_channel: discord.TextChannel):
    if not _recording:
        return

    guild      = txt_channel.guild
    status_msg = await txt_channel.send("⏳ Transcribing audio...")

    mic_wav, loopback_wav, duration, chan_name = await asyncio.to_thread(_stop_and_encode)

    other_id   = next(uid for uid in TRIGGER_USERS if uid != LOCAL_USER_ID)
    alex_name  = await _get_display_name(guild, LOCAL_USER_ID)
    mike_name  = await _get_display_name(guild, other_id)

    files, names = [], []
    if mic_wav:
        files.append(("audio_files", (f"{LOCAL_USER_ID}.wav", mic_wav, "audio/wav")))
        names.append(alex_name)
    if loopback_wav:
        files.append(("audio_files", (f"{other_id}.wav", loopback_wav, "audio/wav")))
        names.append(mike_name)

    if not files:
        await status_msg.edit(content="❌ No audio recorded.")
        return

    form = [("channel", chan_name), ("duration", str(duration))] + \
           [("speaker_names", n) for n in names]

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(f"{API_BASE}/process", data=form, files=files)
            resp.raise_for_status()
            result = resp.json()

        from formatter import build_embed
        embed = build_embed(
            result, names, duration,
            result.get("google_doc_url", ""), result.get("meeting_id")
        )
        await status_msg.delete()
        await txt_channel.send(embed=embed)
        if result.get("google_doc_url"):
            await txt_channel.send(f"📄 **Google Doc:** {result['google_doc_url']}")

    except Exception as e:
        print(f"[process] error: {e}")
        await status_msg.edit(content=f"❌ Processing failed: {e}")

# ─── Voice state monitoring ───────────────────────────────────────────────────

def _trigger_users_in_channel(guild: discord.Guild, channel_id: int) -> set:
    return {
        uid for uid in TRIGGER_USERS
        if (m := guild.get_member(uid)) and m.voice and m.voice.channel and m.voice.channel.id == channel_id
    }

def _find_shared_channel(guild: discord.Guild):
    """Return channel if all trigger users are in the same voice channel."""
    positions = {}
    for uid in TRIGGER_USERS:
        m = guild.get_member(uid)
        if m and m.voice and m.voice.channel:
            positions[uid] = m.voice.channel
    if len(positions) == len(TRIGGER_USERS):
        channels = list(positions.values())
        if all(c.id == channels[0].id for c in channels):
            return channels[0]
    return None

@bot.event
async def on_error(event_method, *args, **kwargs):
    import traceback
    print(f"[error] unhandled exception in {event_method}:")
    traceback.print_exc()

@bot.event
async def on_ready():
    try:
        await bot.sync_commands()
    except Exception as e:
        print(f"[warn] sync_commands failed: {e}")
    print(f"✅ NoteBot online as {bot.user}")
    print(f"📊 Dashboard: {API_BASE}")
    print(f"🎙️  Recording: local mic + WASAPI loopback (bypasses DAVE)")

@bot.event
async def on_voice_state_update(member, before, after):
    global _rec_chan_id, _rec_txt_channel

    try:
        if member.id == bot.user.id:
            return
        if member.id not in TRIGGER_USERS:
            return

        guild = member.guild
        txt_id = os.getenv("DISCORD_TEXT_CHANNEL_ID")
        txt_channel = guild.get_channel(int(txt_id)) if txt_id else guild.system_channel
        print(f"[vsu] {member.display_name} moved | before={getattr(before.channel,'name',None)} after={getattr(after.channel,'name',None)} | recording={_recording}")

        # Someone joined — check if all trigger users are now in the same channel
        if not _recording:
            shared = _find_shared_channel(guild)
            print(f"[vsu] shared channel: {getattr(shared,'name',None)}, txt_channel: {txt_channel}")
            if shared and txt_channel:
                _rec_chan_id     = shared.id
                _rec_txt_channel = txt_channel
                _start_local_recording(shared.name)
                await txt_channel.send(
                    f"🎙️ Recording **{shared.name}** — type `/leave` when done."
                )

        # Someone left recording channel — stop if no trigger users remain
        elif _rec_chan_id and before.channel and before.channel.id == _rec_chan_id:
            if not _trigger_users_in_channel(guild, _rec_chan_id):
                _rec_chan_id = None
                txt = _rec_txt_channel
                _rec_txt_channel = None
                if txt:
                    await txt.send("✅ Stopped. Processing notes...")
                    asyncio.create_task(process_and_post(txt))

    except Exception as e:
        import traceback
        print(f"[error] on_voice_state_update: {e}")
        traceback.print_exc()

# ─── Slash commands ───────────────────────────────────────────────────────────

@bot.event
async def on_application_command_error(ctx, error):
    import traceback
    print(f"[error] slash command error: {type(error).__name__}: {error}")
    traceback.print_exc()

@bot.slash_command(name="join", description="Start recording this voice channel")
async def cmd_join(ctx):
    global _rec_chan_id, _rec_txt_channel
    try:
        if not ctx.author.voice:
            return await ctx.respond("❌ You need to be in a voice channel first.", ephemeral=True)
        await ctx.defer()
        print("[join] deferred")
        channel          = ctx.author.voice.channel
        _rec_chan_id     = channel.id
        _rec_txt_channel = ctx.channel
        _start_local_recording(channel.name)
        print("[join] sending followup...")
        await ctx.followup.send(f"🎙️ Recording **{channel.name}** — type `/leave` when done.")
        print("[join] followup sent")
    except BaseException as e:
        import traceback
        print(f"[error] cmd_join: {type(e).__name__}: {e}")
        traceback.print_exc()

@bot.slash_command(name="leave", description="Stop recording and generate notes")
async def cmd_leave(ctx):
    try:
        if not _recording:
            return await ctx.respond("❌ Not recording right now.", ephemeral=True)
        await ctx.defer()
        await ctx.followup.send("✅ Stopped. Processing notes...")
        asyncio.create_task(process_and_post(ctx.channel))
    except BaseException as e:
        import traceback
        print(f"[error] cmd_leave: {type(e).__name__}: {e}")
        traceback.print_exc()

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
