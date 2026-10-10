import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } from '@whiskeysockets/baileys';
import pino from 'pino';
import QRCode from 'qrcode';
import qrcodeTerminal from 'qrcode-terminal';
import path from 'path';
import fs from 'fs';
import { Boom } from '@hapi/boom';

const sessionDir = process.env.HERMES_SESSION_DIR || 'C:\\Users\\MOHD ASHFAQ\\AppData\\Local\\hermes\\whatsapp\\session';
fs.mkdirSync(sessionDir, { recursive: true });

const htmlPath = 'd:\\New folder (3)\\ai automation\\whatsapp_qr.html';
const logger = pino({ level: 'silent' });

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(sessionDir);
  let version;
  try {
    const v = await fetchLatestBaileysVersion();
    version = v.version;
  } catch (e) {}

  const sock = makeWASocket({
    ...(version ? { version } : {}),
    auth: state,
    logger,
    printQRInTerminal: false,
    browser: ['Hermes Agent', 'Chrome', '120.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\n================ QR CODE READY ================');
      qrcodeTerminal.generate(qr, { small: true });
      console.log('================================================\n');

      const dataUrl = await QRCode.toDataURL(qr, { width: 340, margin: 2 });
      const htmlContent = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Scan WhatsApp QR Code</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #0b141a; color: #e9edef; }
    .card { background: #111b21; padding: 32px; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); text-align: center; max-width: 420px; border: 1px solid #222e35; }
    h2 { margin-top: 0; color: #25D366; font-size: 22px; font-weight: 600; }
    p { font-size: 14px; color: #8696a0; line-height: 1.5; margin-bottom: 20px; }
    img { border-radius: 12px; background: white; padding: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
    .steps { text-align: left; font-size: 13px; color: #aebac1; margin-top: 20px; background: #1f2c34; padding: 16px; border-radius: 8px; }
    .steps ol { padding-left: 20px; margin: 0; }
    .steps li { margin-bottom: 6px; }
    .status { margin-top: 14px; font-size: 12px; color: #25D366; font-weight: 500; }
  </style>
</head>
<body>
  <div class="card">
    <h2>⚡ Connect WhatsApp to Hermes</h2>
    <p>Scan this QR code with WhatsApp on your phone to link your account to Hermes Agent.</p>
    <img src="${dataUrl}" alt="WhatsApp QR Code" />
    <div class="steps">
      <ol>
        <li>Open <b>WhatsApp</b> on your phone</li>
        <li>Tap <b>Settings</b> (or three dots ⋮) → <b>Linked Devices</b></li>
        <li>Tap <b>Link a Device</b></li>
        <li>Scan the QR code above</li>
      </ol>
    </div>
    <div class="status">● Waiting for scan...</div>
  </div>
</body>
</html>`;
      fs.writeFileSync(htmlPath, htmlContent, 'utf-8');
      console.log(`[QR Saved to HTML]: ${htmlPath}`);
    }

    if (connection === 'close') {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      if (reason === DisconnectReason.loggedOut) {
        console.log('❌ Logged out. Run again to re-authenticate.');
        process.exit(1);
      } else {
        console.log(`⚠️ Connection closed (reason: ${reason}), reconnecting in 2s...`);
        setTimeout(() => start(), 2000);
      }
    } else if (connection === 'open') {
      console.log('\n✅ WHATSAPP CONNECTED AND PAIRED SUCCESSFULLY!');
      console.log('Linked JID:', sock.user?.id || 'Connected');
      setTimeout(() => {
        process.exit(0);
      }, 2000);
    }
  });
}

start();
