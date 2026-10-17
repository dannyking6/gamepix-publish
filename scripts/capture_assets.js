#!/usr/bin/env node
/**
 * capture_assets.js — REAL-GAMEPLAY asset generator (replaces AI generation).
 *
 * QA rejected games whose AI/description-derived assets "did not correspond to
 * the game tested". This script makes that impossible by construction: it
 * launches the ACTUAL build being submitted, makes sure real GAMEPLAY is on
 * screen (auto-plays through most start menus — never ships a menu/loading
 * frame), captures a burst of gameplay frames, then calls capture_postprocess.py
 * which picks the most action-rich frames and crops them into:
 *   icon_256.png        256x256, <=1MB, full-bleed SQUARE corners (no rounded)
 *   cover_1360x850.png  1360x850, <=1.5MB, full-bleed
 * and writes ASSETS_SOURCE=assets_gameplay_capture next to them.
 *
 * Usage:
 *   xvfb-run -a node scripts/capture_assets.js --zip build.zip --out-dir ./assets
 * Options:
 *   --zip PATH        game build zip (index.html at root)   [or --dir PATH]
 *   --dir PATH        already-unzipped build directory
 *   --out-dir PATH    where icon/cover are written (default ./assets)
 *   --width  1280     browser viewport width
 *   --height 720      browser viewport height
 *   --settle 6        seconds of active play to skip BEFORE capturing (menus die here)
 *   --frames 24       number of gameplay frames captured (default 24)
 *   --interval 900    ms between frames (default 900)
 *   --title "My Game" game title rendered on the assets (must match game title)
 *
 * Requirements:
 *   npm i playwright && npx playwright install chromium     (run under xvfb on Linux)
 *   pip install pillow                                       (for the post-process)
 */
'use strict';
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const http = require('http');

const log = (...a) => console.log('[CAPTURE]', ...a);

function parseArgs() {
  const a = {};
  for (let i = 2; i < process.argv.length; i++) {
    if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i + 1];
  }
  return a;
}
const args = parseArgs();

// ---------- static server for the unpacked build ----------
const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.svg': 'image/svg+xml', '.webp': 'image/webp',
  '.webm': 'video/webm', '.mp4': 'video/mp4', '.mp3': 'audio/mpeg',
  '.ogg': 'audio/ogg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
  '.json': 'application/json', '.wasm': 'application/wasm',
  '.ttf': 'font/ttf', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.xml': 'text/xml', '.ico': 'image/x-icon',
};
function serve(dir, port) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let p;
      try { p = decodeURIComponent(req.url.split('?')[0]); } catch (e) { p = '/'; }
      if (p.endsWith('/')) p += 'index.html';
      const fp = path.join(dir, p);
      fs.readFile(fp, (err, data) => {
        if (err) { res.statusCode = 404; res.end('not found'); return; }
        res.setHeader('Content-Type', MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream');
        res.end(data);
      });
    });
    srv.listen(port, '127.0.0.1', () => resolve(srv));
  });
}

// ---------- unpack zip (fallback to python3 zipfile if unzip is missing) ----------
function unpack(zip, dst) {
  fs.mkdirSync(dst, { recursive: true });
  try {
    execFileSync('unzip', ['-o', '-q', zip, '-d', dst], { stdio: 'inherit' });
    return dst;
  } catch (e) {
    log('unzip unavailable or failed — trying python3 zipfile');
    execFileSync('python3', [
      '-c',
      'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])',
      zip,
      dst,
    ]);
    return dst;
  }
}

// basic path-safety for zip entries (zip-slip)
function sanitizeUnpacked(dst) {
  const root = path.resolve(dst);
  const walk = (d) => {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      if (!path.resolve(p).startsWith(root)) { fs.rmSync(p, { recursive: true, force: true }); continue; }
      if (fs.statSync(p).isDirectory()) walk(p);
    }
  };
  walk(root);
}

// ---------- auto-start: get out of menus into real gameplay ----------
const START_TEXTS = [
  /^play$/i, /^start$/i, /^start game$/i, /^play now$/i, /^let'?s (go|play)/i,
  /^go$/i, /^begin$/i, /^continue$/i, /^ok$/i, /^start$/i, /tap to (play|start)/i,
  /click (or tap )?to (play|start)/i, /^tourner$/i, /^jouer$/i, /^jugar$/i, /^gioca$/i,
  /^english$/i, /^français$/i, /^español$/i, /^italiano$/i, /^deutsch$/i, // language menus
];

async function autostart(page) {
  for (let round = 0; round < 5; round++) {
    let clicked = 0;
    // 1) real clicks on start-like text (trusted piercing clicks)
    for (const txt of START_TEXTS) {
      try {
        const el = page.locator(`text="${txt.source.replace(/^\/|\/i$/g, '').replace(/\\[a-z]/gi, '')}"`).first() // placeholder; replaced below
          ; void el;
      } catch (e) {}
    }
    // (text= selector uses case-insensitive contains; do it directly:)
    for (const re of START_TEXTS) {
      try {
        const loc = page.locator('button, a, div[role="button"], span, p, ion-button')
          .filter({ hasText: re }).first();
        if (await loc.isVisible({ timeout: 150 }) && await loc.count()) {
          await loc.click({ timeout: 1500, force: true });
          clicked++;
          await page.waitForTimeout(900);
          break; // one action per round; let the game settle
        }
      } catch (e) {}
    }
    // 2) generic "click anywhere to start" overlays: click the canvas/center
    try {
      const canvas = page.locator('canvas, video, body').first();
      if (await canvas.isVisible({ timeout: 100 })) {
        await canvas.click({ position: { x: 10, y: 10 }, force: true, timeout: 1500 }).catch(() => {});
      }
    } catch (e) {}
    await page.keyboard.press('Enter').catch(() => {});
    await page.keyboard.press('Space').catch(() => {});
    await page.waitForTimeout(1200);
    log(`autostart round ${round + 1}: clicks=${clicked}, url=${page.url()}`);
  }
}

(async () => {
  const outDir = args['out-dir'] || './assets';
  const width = parseInt(args.width || '1280', 10);
  const height = parseInt(args.height || '720', 10);
  const settleS = parseFloat(args.settle || '6');
  const frames = parseInt(args.frames || '24', 10);
  const interval = parseInt(args.interval || '900', 10);

  let gameDir = args.dir;
  let tmpUnzip = null;
  if (!gameDir && args.zip) gameDir = tmpUnzip = unpack(args.zip, path.join(path.dirname(path.resolve(args.zip)), '.capture_unpacked'));
  if (!gameDir) throw new Error('provide --zip or --dir');
  sanitizeUnpacked(gameDir);
  if (!fs.existsSync(path.join(gameDir, 'index.html'))) throw new Error('index.html not at ' + gameDir + ' root');

  fs.mkdirSync(outDir, { recursive: true });
  const framesDir = path.join(outDir, '.frames');
  fs.rmSync(framesDir, { recursive: true, force: true });
  fs.mkdirSync(framesDir, { recursive: true });

  const port = 18000 + Math.floor(Math.random() * 2000);
  const srv = await serve(path.resolve(gameDir), port);
  const url = `http://127.0.0.1:${port}/index.html`;
  log('serving', url);

  const browser = await chromium.launch({ headless: false, args: ['--disable-blink-features=AutomationControlled'] });
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(5000); // loading screens

  await autostart(page);

  // ---- settle: keep playing, then skip (settle) seconds so menus are gone ----
  const t0 = Date.now();
  const settleEnd = t0 + settleS * 1000;
  log(`getting deeper into gameplay for ${settleS}s (menus end up behind us)...`);
  while (Date.now() < settleEnd) {
    try {
      const canvas = page.locator('canvas, video, body').first();
      if (await canvas.isVisible({ timeout: 80 })) {
        await canvas.click({ position: { x: width - 20, y: height - 20 }, force: true, timeout: 800 }).catch(() => {});
      }
    } catch (e) {}
    await page.keyboard.press('ArrowRight').catch(() => {});
    await page.waitForTimeout(700);
  }

  // ---- capture burst of gameplay frames ----
  const clip = await (() => {
    // prefer the largest visible canvas — usually the real game viewport
    return page.evaluate(() => {
      let best = null;
      for (const c of document.querySelectorAll('canvas, video')) {
        const r = c.getBoundingClientRect();
        if (!best || (r.width * r.height) > (best.w * best.h)) best = { x: r.x, y: r.y, width: r.width, height: r.height }; // keep css pixels
      }
      return best ? { x: Math.max(0, best.x), y: Math.max(0, best.y), width: Math.max(1, best.width), height: Math.max(1, best.height) } : null;
    });
  })().catch(() => null);
  log('capture clip:', clip || 'full page');

  for (let i = 0; i < frames; i++) {
    const fp = path.join(framesDir, `frame_${String(i).padStart(3, '0')}.png`);
    await page.screenshot({ path: fp, ...(clip && clip.width > 8 && clip.height > 8 ? { clip } : {}) });
    await page.waitForTimeout(interval);
    if (i % 5 === 4) log(`captured ${i + 1}/${frames}`);
    // keep gameplay alive: tiny movement so idle screens don't dominate
    if (i % 6 === 5) { await page.keyboard.press('ArrowLeft').catch(() => {}); await page.keyboard.press('Space').catch(() => {}); }
  }
  await browser.close();
  srv.close();

  // ---- post-process: pick best gameplay frames, crop, enforce QA rules ----
  const pp = path.join(__dirname, 'capture_postprocess.py');
  log('post-processing frames with PIL...');
  const ppArgs = [
    pp, framesDir,
    '--out-dir', outDir,
    '--icon-size', '256',
    '--cover-size', '1360x850',
    '--max-icon-bytes', String(1 * 1024 * 1024),
    '--max-cover-bytes', String(1.5 * 1024 * 1024),
  ];
  if (args.title) ppArgs.push('--title', args.title);
  execFileSync('python3', ppArgs, { stdio: 'inherit' });
  log('✅ assets written to', outDir);
  if (tmpUnzip) { fs.rmSync(tmpUnzip, { recursive: true, force: true }); }
})().catch((e) => { console.error('[CAPTURE][ERR]', e); process.exit(1); });
