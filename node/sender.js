/**
 * WhatsApp Sender — Baileys backend
 *
 * Protocolo com o Python (via stdin/stdout):
 *   stdin  ← JSON lines: { id, nome, numero, mensagem }
 *   stdout → JSON lines: { status: 'ready' }
 *                        { id, status: 'sent' }
 *                        { id, status: 'failed', error: '...' }
 *   stderr → logs e QR code (exibidos direto no terminal)
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys';
import { createRequire } from 'module';
import readline from 'readline';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import pino from 'pino';

const require = createRequire(import.meta.url);
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AUTH_DIR   = process.env.DATA_DIR
  ? path.join(process.env.DATA_DIR, 'auth_info')
  : path.join(__dirname, 'auth_info');
const CONFIG_PATH = path.join(__dirname, '..', 'config.json');

// ── Configuração ────────────────────────────────────────────────────────────

let config = { delay_min_seconds: 15, delay_max_seconds: 45 };
try {
  config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
} catch {
  // usa defaults
}

// ── Utilitários ─────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function randomDelay() {
  const min = (config.delay_min_seconds ?? 15) * 1000;
  const max = (config.delay_max_seconds ?? 45) * 1000;
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

/** Envia resposta estruturada para o Python via stdout. */
function reply(data) {
  process.stdout.write(JSON.stringify(data) + '\n');
}

/** Log simples para stderr (não interfere no protocolo stdout). */
function log(msg) {
  process.stderr.write(`[sender.js] ${msg}\n`);
}

/**
 * Simula tempo de digitação proporcional ao tamanho da mensagem.
 * Retorna ms entre 3s e 8s com pequeno jitter.
 */
function typingDelay(text) {
  const base = Math.min(8000, Math.max(3000, text.length * 55));
  const jitter = Math.floor(Math.random() * 1000) - 500;
  return base + jitter;
}

/**
 * Adiciona um caractere unicode invisível aleatório no final da mensagem.
 * Torna o hash de cada mensagem único sem alterar o texto visível.
 */
const ZWS_CHARS = ['\u200B', '\u200C', '\u200D', '\uFEFF'];
function addInvisibleVariation(text) {
  return text + ZWS_CHARS[Math.floor(Math.random() * ZWS_CHARS.length)];
}

// ── Processamento de contatos via stdin ──────────────────────────────────────

function startProcessing() {
  const rl = readline.createInterface({ input: process.stdin, terminal: false });

  rl.on('line', async (raw) => {
    const line = raw.trim();
    if (!line) return;

    rl.pause();

    let contact;
    try {
      contact = JSON.parse(line);
    } catch {
      log(`JSON inválido recebido: ${line}`);
      rl.resume();
      return;
    }

    try {
      const jid = contact.numero.replace('+', '') + '@s.whatsapp.net';

      // Verifica se o número está cadastrado no WhatsApp antes de enviar
      const [check] = await currentSock.onWhatsApp(jid);
      if (!check?.exists) {
        reply({ id: contact.id, status: 'failed', error: 'Número não está no WhatsApp' });
        await sleep(2000);
        rl.resume();
        return;
      }

      // Usa o JID canônico retornado (resolve o dígito 9 brasileiro automaticamente)
      const canonicalJid = check.jid;
      const variedText   = addInvisibleVariation(contact.mensagem);

      // Simula digitação humana antes de enviar
      await currentSock.sendPresenceUpdate('composing', canonicalJid);
      await sleep(typingDelay(contact.mensagem));
      await currentSock.sendPresenceUpdate('paused', canonicalJid);

      await currentSock.sendMessage(canonicalJid, { text: variedText });
      reply({ id: contact.id, status: 'sent' });

      // Marca como offline após o envio
      await currentSock.sendPresenceUpdate('unavailable');
      await sleep(randomDelay());
    } catch (err) {
      reply({ id: contact.id, status: 'failed', error: err.message ?? String(err) });
    }

    rl.resume();
  });

  rl.on('close', () => {
    process.exit(0);
  });
}

let currentSock = null;
let processingStarted = false;

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const logger = pino({ level: 'fatal' });

  const sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger,
    browser: ['Chrome (Linux)', 'Chrome', '120.0.0.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
  });

  currentSock = sock;
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      process.stderr.write('\n');
      process.stderr.write('╔══════════════════════════════════════════╗\n');
      process.stderr.write('║  ESCANEIE O QR COM SEU WHATSAPP           ║\n');
      process.stderr.write('║  WhatsApp → ⋮ → Dispositivos conectados   ║\n');
      process.stderr.write('╚══════════════════════════════════════════╝\n\n');
      qrcode.generate(qr, { small: true }, (qrStr) => {
        process.stderr.write(qrStr + '\n');
      });
      // Envia a string bruta do QR para o Python converter em PNG base64
      reply({ status: 'qr', qr: qr });
    }

    if (connection === 'open') {
      currentSock = sock;
      if (!processingStarted) {
        processingStarted = true;
        log('Conectado ao WhatsApp!');
        reply({ status: 'ready' });
        startProcessing();
      } else {
        log('Reconectado ao WhatsApp!');
      }
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        log('Sessão encerrada. Delete node/auth_info e execute novamente.');
        process.exit(1);
      } else {
        // Reconecta automaticamente (ex: código 515 = restart required)
        log(`Conexão encerrada (código ${code}). Reconectando em 3s...`);
        await sleep(3000);
        connect();
      }
    }
  });
}

async function main() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  await connect();
}

main().catch((err) => {
  log(`FATAL: ${err.message}`);
  process.exit(1);
});
