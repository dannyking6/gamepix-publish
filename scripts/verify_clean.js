#!/usr/bin/env node
'use strict';
/**
 * verify_clean.js — assert a build is fully cleaned of its origin.
 *
 * Usage:
 *   node scripts/verify_clean.js <build-dir> <platform-name> <original-game-name> [extra term...]
 *
 * Exits 0 when NONE of the given terms (case-insensitive) appear in any
 * shipped file's contents or file names. Exits 1 (and prints offenders)
 * otherwise. A non-zero exit must block submission.
 *
 * Default platform list is always checked in addition to the provided
 * platform-name, so a build is verified against every known portal/engine
 * name regardless of the argument.
 */
const fs = require('fs');
const path = require('path');

const DEFAULT_PLATFORMS = [
  'poki', 'softgames', 'gamesnacks', 'construct', 'scirra',
  'phaser', 'pixi', 'pixijs', 'unity', 'godot', 'famobi',
  'crazygames', 'gamemonetize', 'playgama',
];

const buildDir = process.argv[2];
if (!buildDir) {
  console.error('Usage: node verify_clean.js <build-dir> <platform-name> <original-game-name> [extra...]');
  process.exit(2);
}
const userTerms = process.argv.slice(3).filter(Boolean);
const terms = Array.from(new Set([...DEFAULT_PLATFORMS, ...userTerms]))
  .map(t => t.toLowerCase().trim())
  .filter(Boolean);

if (!fs.existsSync(buildDir) || !fs.statSync(buildDir).isDirectory()) {
  console.error('Build dir not found: ' + buildDir);
  process.exit(2);
}

const SKIP_DIRS = new Set(['.git', 'node_modules', '.DS_Store']);
const SKIP_EXT = new Set(['.png','.jpg','.jpeg','.webp','.gif','.ico','.woff','.woff2','.ttf','.otf','.wasm','.pck','.mp3','.ogg','.wav','.webm','.m4a','.mp4','.zip','.gpx']);

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const offenders = [];
for (const file of walk(buildDir)) {
  const rel = path.relative(buildDir, file);
  // check file name itself
  const base = path.basename(file).toLowerCase();
  for (const t of terms) {
    if (base.includes(t)) {
      offenders.push({ file: rel, kind: 'filename', term: t });
    }
  }
  // check contents (skip heavy binaries)
  const ext = path.extname(file).toLowerCase();
  if (SKIP_EXT.has(ext)) continue;
  let buf;
  try { buf = fs.readFileSync(file); } catch (e) { continue; }
  const text = buf.toString('utf8').toLowerCase();
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    for (const t of terms) {
      if (lines[i].includes(t)) {
        offenders.push({ file: rel, kind: 'content', line: i + 1, term: t, snippet: lines[i].trim().slice(0, 120) });
      }
    }
  }
}

if (offenders.length === 0) {
  console.log('CLEAN: 0 in-game hits for terms: ' + terms.join(', '));
  process.exit(0);
}

console.error('NOT CLEAN: ' + offenders.length + ' hit(s):');
for (const o of offenders.slice(0, 50)) {
  if (o.kind === 'filename') {
    console.error('  [filename] ' + o.file + '  (term: ' + o.term + ')');
  } else {
    console.error('  [content] ' + o.file + ':' + o.line + '  (term: ' + o.term + ')  ' + o.snippet);
  }
}
if (offenders.length > 50) console.error('  ... and ' + (offenders.length - 50) + ' more');
process.exit(1);
