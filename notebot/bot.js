'use strict';

require('dotenv').config();
const {
  Client, GatewayIntentBits, EmbedBuilder,
  REST, Routes, SlashCommandBuilder
} = require('discord.js');
const { joinVoiceChannel, EndBehaviorType } = require('@discordjs/voice');
const prism = require('prism-media');
const { createWriteStream, createReadStream, unlinkSync, existsSync, statSync } = require('fs');
const { pipeline } = require('stream');
const axios = require('axios');
const FormData = require('form-data');

const TRIGGER_USERS = new Set([
  '236708079872376834',   // Mike
  '1074610938684121138'   // Alex
]);

const GUILD_ID        = process.env.DISCORD_GUILD_ID;
const TEXT_CHANNEL_ID = process.env.DISCORD_TEXT_CHANNEL_ID;
const API_BASE        = `http://localhost:${process.env.DASHBOARD_PORT || 8080}`;

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.MessageContent,
  ]
});

// — State —
let voiceConn         = null;
let startTime         = null;
let recordingChanId   = null;
let recordingChanName = '';
let recordingText     = null;
let speakerFiles      = new Map(); // userId → { filename, displayName, rawStream, writeStream }

// ─── Audio helpers ────────────────────────────────────────────────────────────

function subscribeUser(receiver, userId, displayName) {
  if (speakerFiles.has(userId)) return;

  const filename    = `rec_${userId}_${Date.now()}.pcm`;
  const rawStream   = receiver.subscribe(userId, { end: EndBehaviorType.Manual });
  const decoder     = new prism.opus.Decoder({ frameSize: 960, channels: 2, rate: 48000 });
  const writeStream = createWriteStream(filename);

  pipeline(rawStream, decoder, writeStream, err => {
    if (err && err.code !== 'ERR_STREAM_DESTROYED') {
      console.error(`[audio] error for ${displayName}:`, err.message);
    }
  });

  speakerFiles.set(userId, { filename, displayName, rawStream, writeStream });
  console.log(`[rec] subscribed to ${displayName}`);
}

// ─── Recording lifecycle ──────────────────────────────────────────────────────

async function startRecording(voiceChannel, txtChannel, sendMessage = true) {
  if (voiceConn) return;

  recordingText     = txtChannel;
  startTime         = Date.now();
  recordingChanName = voiceChannel.name;
  recordingChanId   = voiceChannel.id;
  speakerFiles      = new Map();

  voiceConn = joinVoiceChannel({
    channelId:       voiceChannel.id,
    guildId:         voiceChannel.guild.id,
    adapterCreator:  voiceChannel.guild.voiceAdapterCreator,
    selfDeaf:        false,
    selfMute:        true,
  });

  const receiver = voiceConn.receiver;

  // Subscribe to everyone already in the channel
  for (const [uid, member] of voiceChannel.members) {
    if (uid === client.user.id) continue;
    subscribeUser(receiver, uid, member.displayName);
  }

  // Subscribe as new people start speaking
  receiver.speaking.on('start', uid => {
    const member = voiceChannel.members.get(uid);
    if (member && uid !== client.user.id) {
      subscribeUser(receiver, uid, member.displayName);
    }
  });

  if (sendMessage) {
    await txtChannel.send(`🎙️ Recording **${voiceChannel.name}** — type \`/leave\` when done.`);
  }
}

async function stopRecording() {
  if (!voiceConn) return;

  const duration  = Math.round((Date.now() - startTime) / 60000);
  const txtChan   = recordingText;
  const chanName  = recordingChanName;

  // Signal end of all raw streams (push null, don't destroy yet)
  for (const [, { rawStream }] of speakerFiles) {
    try { rawStream.push(null); } catch (_) {}
  }

  // Wait for all write streams to finish flushing (up to 8 seconds)
  await Promise.all([...speakerFiles.values()].map(({ writeStream }) =>
    new Promise(resolve => {
      if (writeStream.writableEnded || writeStream.closed) return resolve();
      writeStream.once('finish', resolve);
      writeStream.once('close', resolve);
      setTimeout(resolve, 8000);
    })
  ));

  voiceConn.destroy();
  voiceConn       = null;
  recordingChanId = null;

  await processRecording(duration, chanName, txtChan);
}

// ─── Processing ───────────────────────────────────────────────────────────────

async function processRecording(duration, chanName, txtChan) {
  const statusMsg = await txtChan.send('⏳ Transcribing audio...');

  try {
    const form = new FormData();
    form.append('channel',  chanName);
    form.append('duration', String(duration));

    let hasAudio = false;
    for (const [userId, { filename, displayName }] of speakerFiles) {
      if (existsSync(filename) && statSync(filename).size > 0) {
        console.log(`[process] including ${displayName} — ${statSync(filename).size} bytes`);
        form.append('audio_files',   createReadStream(filename), {
          filename:    `${userId}.pcm`,
          contentType: 'audio/pcm',
        });
        form.append('speaker_names', displayName);
        hasAudio = true;
      } else {
        console.log(`[process] skipping ${displayName} — file missing or empty`);
      }
    }

    if (!hasAudio) {
      await statusMsg.edit('❌ No audio recorded.');
      return;
    }

    const resp = await axios.post(`${API_BASE}/process`, form, {
      headers: form.getHeaders(),
      timeout: 600_000,       // 10 min — allow for long transcription
      maxContentLength: Infinity,
      maxBodyLength:    Infinity,
    });

    const result = resp.data;

    // Clean up PCM files
    for (const [, { filename }] of speakerFiles) {
      if (existsSync(filename)) unlinkSync(filename);
    }

    const embed = buildEmbed(result);
    await statusMsg.delete();
    await txtChan.send({ embeds: [embed] });
    if (result.google_doc_url) {
      await txtChan.send(`📄 **Google Doc:** ${result.google_doc_url}`);
    }

  } catch (err) {
    console.error('[process] error:', err.message);
    await statusMsg.edit(`❌ Processing failed: ${err.message}`);

    // Clean up on failure too
    for (const [, { filename }] of speakerFiles) {
      if (existsSync(filename)) try { unlinkSync(filename); } catch (_) {}
    }
  }
}

function buildEmbed(r) {
  const embed = new EmbedBuilder()
    .setTitle('📋 Meeting Notes')
    .setDescription(r.summary || 'No summary available.')
    .setColor(0x5865F2)
    .setTimestamp();

  if (r.topics?.length)
    embed.addFields({ name: '🗂️ Topics',       value: r.topics.map(t => `• ${t}`).join('\n'),       inline: false });
  if (r.decisions?.length)
    embed.addFields({ name: '✅ Decisions',     value: r.decisions.map(d => `• ${d}`).join('\n'),    inline: false });
  if (r.action_items?.length)
    embed.addFields({ name: '📌 Action Items',  value: r.action_items.map(a => `• ${a}`).join('\n'), inline: false });
  if (r.blockers?.length)
    embed.addFields({ name: '🚧 Blockers',      value: r.blockers.map(b => `• ${b}`).join('\n'),     inline: false });

  const links = [];
  if (r.google_doc_url) links.push(`[📄 Google Doc](${r.google_doc_url})`);
  links.push(`[📊 Dashboard](http://localhost:8080/meeting/${r.meeting_id})`);
  embed.addFields({ name: '🔗 Links', value: links.join('  |  '), inline: false });
  embed.setFooter({ text: `AMP Titans NoteBot • ${(r.speakers || []).join(', ')} • ${r.duration} min` });

  return embed;
}

// ─── Auto-trigger ─────────────────────────────────────────────────────────────

client.on('voiceStateUpdate', async (oldState, newState) => {
  const memberId = newState.id || oldState.id;
  if (memberId === client.user?.id) return;

  const guild = newState.guild;

  // Someone joined a channel — check if both trigger users are now present
  if (newState.channelId && newState.channelId !== oldState.channelId) {
    const channel   = newState.channel;
    const memberIds = new Set([...channel.members.keys()]);

    const allPresent = [...TRIGGER_USERS].every(id => memberIds.has(id));
    if (allPresent && !voiceConn) {
      const txtChan = TEXT_CHANNEL_ID
        ? guild.channels.cache.get(TEXT_CHANNEL_ID)
        : guild.systemChannel;
      if (txtChan) await startRecording(channel, txtChan, true);
    }
  }

  // Someone left the recording channel — stop when both trigger users are gone
  if (oldState.channelId && oldState.channelId === recordingChanId) {
    const oldChan    = oldState.channel;
    const remaining  = new Set([...(oldChan?.members?.keys() || [])]);
    const anyPresent = [...TRIGGER_USERS].some(id => remaining.has(id));
    if (!anyPresent && voiceConn) await stopRecording();
  }
});

// ─── Slash commands ───────────────────────────────────────────────────────────

client.on('interactionCreate', async interaction => {
  if (!interaction.isChatInputCommand()) return;

  if (interaction.commandName === 'join') {
    if (!interaction.member.voice.channel) {
      return interaction.reply({ content: '❌ You need to be in a voice channel first.', ephemeral: true });
    }
    await interaction.deferReply();
    await startRecording(interaction.member.voice.channel, interaction.channel, false);
    await interaction.editReply(`🎙️ Recording **${recordingChanName}** — type \`/leave\` when done.`);
  }

  if (interaction.commandName === 'leave') {
    if (!voiceConn) {
      return interaction.reply({ content: '❌ Not recording right now.', ephemeral: true });
    }
    await interaction.deferReply();
    await interaction.editReply('✅ Stopped. Processing notes...');
    await stopRecording();
  }
});

// ─── Startup ──────────────────────────────────────────────────────────────────

client.once('ready', async () => {
  console.log(`✅ NoteBot online as ${client.user.tag}`);
  console.log(`📊 Dashboard: ${API_BASE}`);

  const commands = [
    new SlashCommandBuilder().setName('join').setDescription('Join your voice channel and start recording'),
    new SlashCommandBuilder().setName('leave').setDescription('Stop recording and generate meeting notes'),
  ].map(c => c.toJSON());

  const rest = new REST({ version: '10' }).setToken(process.env.DISCORD_BOT_TOKEN);
  try {
    await rest.put(Routes.applicationGuildCommands(client.user.id, GUILD_ID), { body: commands });
    console.log('✅ Slash commands registered');
  } catch (err) {
    console.error('Failed to register commands:', err.message);
  }
});

client.login(process.env.DISCORD_BOT_TOKEN).catch(err => {
  console.error('Login failed:', err.message);
  process.exit(1);
});
