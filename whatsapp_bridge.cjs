const http = require('node:http');
const path = require('node:path');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const actionFromBody = require('./whatsapp_commands.cjs');

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 3021);
const COMMAND_PORT = Number(process.env.WHATSAPP_COMMAND_PORT || 3022);
const RECIPIENT = String(process.env.WHATSAPP_RECIPIENT || '').trim();
const AUTH_DIR = path.resolve(process.env.WHATSAPP_AUTH_DIR || path.join(__dirname, '.whatsapp-auth'));
const SELF_COMMANDS_ONLY = !RECIPIENT;
let ready = false;
let clientInfo = null;

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: AUTH_DIR }),
  puppeteer: {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    ...(process.env.PUPPETEER_EXECUTABLE_PATH
      ? { executablePath: process.env.PUPPETEER_EXECUTABLE_PATH }
      : {}),
  },
});

function recipientJid() {
  if (!RECIPIENT) {
    if (!clientInfo || !clientInfo.wid || !clientInfo.wid._serialized) {
      throw new Error('WhatsApp session is not ready yet');
    }
    return clientInfo.wid._serialized;
  }
  if (RECIPIENT.includes('@')) return RECIPIENT;
  const digits = RECIPIENT.replace(/\D/g, '');
  if (!digits) throw new Error('WHATSAPP_RECIPIENT must be an international phone number');
  return `${digits}@c.us`;
}

function sendJson(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
  });
  response.end(body);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > 25 * 1024 * 1024) {
        reject(new Error('request body too large'));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'));
      } catch (error) {
        reject(error);
      }
    });
    request.on('error', reject);
  });
}

const server = http.createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/health') {
    return sendJson(response, 200, { ok: true, ready, account: clientInfo?.wid?._serialized || null });
  }
  if (request.method !== 'POST' || request.url !== '/send') {
    return sendJson(response, 404, { ok: false, error: 'not found' });
  }
  try {
    const payload = await readJson(request);
    if (!ready) throw new Error('WhatsApp is not connected; scan the QR code first');
    const requestedTarget = String(payload.chat_id || '').trim();
    if (requestedTarget && !/^\d+@(c\.us|s\.whatsapp\.net|lid)$/i.test(requestedTarget)) {
      throw new Error('invalid private WhatsApp chat id');
    }
    const target = requestedTarget || recipientJid();
    if (requestedTarget && requestedTarget === recipientJid()) {
      return sendJson(response, 200, { ok: true, skipped: true });
    }
    if (payload.image_base64) {
      const media = new MessageMedia(
        payload.image_mimetype || 'image/png',
        payload.image_base64,
        payload.image_filename || 'latest_browser.png',
      );
      await client.sendMessage(target, media, { caption: String(payload.text || '') });
    } else {
      const text = String(payload.text || '').trim();
      if (!text) throw new Error('message text is empty');
      await client.sendMessage(target, text);
    }
    return sendJson(response, 200, { ok: true });
  } catch (error) {
    return sendJson(response, 503, { ok: false, error: error.message || String(error) });
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`WhatsApp bridge listening locally on port ${PORT}`);
  console.log(SELF_COMMANDS_ONLY
    ? 'Alerts will be sent to the linked WhatsApp account (self-chat).'
    : `Alerts will be sent to the configured recipient (${RECIPIENT}).`);
});

client.on('qr', (qr) => {
  console.log('\nפתח WhatsApp בטלפון > מכשירים מקושרים > קישור מכשיר, וסרוק את ה-QR הבא:');
  qrcode.generate(qr, { small: true });
});
client.on('authenticated', () => console.log('WhatsApp: המכשיר קושר בהצלחה.'));
client.on('auth_failure', (message) => console.error(`WhatsApp authentication failed: ${message}`));
client.on('ready', () => {
  ready = true;
  clientInfo = client.info;
  console.log('✅ WhatsApp Web מחובר ומוכן.');
});
client.on('disconnected', (reason) => {
  ready = false;
  console.error(`WhatsApp disconnected: ${reason}`);
});

async function sendCommandReply(chatId, command, senderChatId = chatId, phone = '') {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5 * 60 * 1000);
  try {
    const response = await fetch(`http://127.0.0.1:${COMMAND_PORT}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, chat_id: senderChatId, phone }),
      signal: controller.signal,
    });
    const result = await response.json();
    for (const text of (result.messages || [])) {
      if (text) await client.sendMessage(chatId, String(text));
    }
    if (result.image_base64) {
      const image = new MessageMedia(
        'image/png',
        result.image_base64,
        result.image_filename || 'latest_browser.png',
      );
      await client.sendMessage(chatId, image, { caption: '📸 צילום המסך האחרון של הדפדפן' });
    }
    if (!response.ok && !(result.messages || []).length) {
      await client.sendMessage(chatId, 'הפקודה נכשלה מול Apollo Monitor. בדוק שהמוניטור עדיין פועל.');
    }
  } catch (error) {
    await client.sendMessage(chatId, `לא הצלחתי לבצע את הפקודה: ${error.message || error}`);
  } finally {
    clearTimeout(timer);
  }
}

async function senderPhone(message, senderChatId) {
  try {
    const contact = await message.getContact();
    if (contact && contact.number) return String(contact.number);
  } catch (_) { /* use the private chat id below */ }
  const match = String(senderChatId || '').match(/^(\d+)@(c\.us|s\.whatsapp\.net)$/i);
  return match ? match[1] : '';
}

async function handleCommand(message) {
  if (!ready || message.isGroupMsg) return;
  const chatId = message.fromMe ? (message.to || message.from) : message.from;
  const command = actionFromBody(message.body);
  if (!command) return;

  const selfJid = clientInfo?.wid?._serialized;
  const sender = message.fromMe ? (message.to || message.from) : message.from;
  const allowed = message.fromMe
    ? (!SELF_COMMANDS_ONLY || sender === selfJid || message.from === selfJid)
    : (!SELF_COMMANDS_ONLY && sender === recipientJid());
  const subscriptionCommand = command === 'subscribe' || command === 'unsubscribe';
  if (!allowed && !subscriptionCommand) return;

  const phone = await senderPhone(message, chatId);
  await sendCommandReply(chatId, command, chatId, phone);
}

// Incoming private chats and self-chat commands support the Hebrew phrases above.
// Normal messages and messages in groups are ignored.
client.on('message', (message) => { void handleCommand(message); });
client.on('message_create', (message) => {
  if (message.fromMe) void handleCommand(message);
});

client.initialize().catch((error) => {
  console.error('Could not start WhatsApp Web:', error);
  process.exitCode = 1;
});

async function shutdown() {
  ready = false;
  try { await client.destroy(); } catch (_) { /* already stopped */ }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 3000).unref();
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
