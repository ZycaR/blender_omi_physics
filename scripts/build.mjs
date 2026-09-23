#!/usr/bin/env node
/**
 * build.mjs - bundle the src/ modules into the distributed single-file
 * Blender addon, plus the Blender extension .zip package.
 *
 * ZERO npm dependencies (Node >= 18). Run from the repo root:
 *
 *   node scripts/build.mjs               dist/omi_...py + dist/<id>-<ver>.zip
 *   node scripts/build.mjs --no-zip      only the single-file bundle
 *   node scripts/build.mjs --verify      also run scripts/verify_bundle.py (python3)
 *   node scripts/build.mjs --out FILE    custom bundle output path
 *
 * HOW IT WORKS
 * ------------
 *  1. Reads the modules in the fixed ORDER below.
 *  2. Splits each module into a leading import block and a body.
 *     - Project-internal imports ("from omi_* import ...") are DROPPED:
 *       the modules are concatenated, so all symbols share one namespace
 *       in the bundle.
 *     - External imports (bpy, math, ...) are merged and de-duplicated
 *       into a single header block (plain imports sorted; "from X import"
 *       statements merged per module, names kept in first-seen order).
 *     - Only UNINDENTED (module-level) import lines are touched; the lazy
 *       runtime imports inside functions/classes are left alone.
 *  3. Patches bl_info in the OUTPUT from blender_manifest.toml
 *     (version / author / doc_url) so the manifest stays the single source
 *     of truth. Prints a warning when src/omi_meta.py is stale.
 *  4. Emits: meta section (bl_info + docstring), merged imports, then each
 *     module body behind a navigation banner.
 *  5. Extension package: dist/<id>-<version>.zip containing
 *     blender_manifest.toml + __init__.py (the bundle with bl_info
 *     stripped - extensions are identified by their manifest).
 */

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateRawSync } from 'node:zlib';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'src');
const DIST = join(ROOT, 'dist');

/** Concatenation order - meta first, composition root last. */
const ORDER = [
  'omi_meta.py',
  'omi_core.py',
  'omi_ui.py',
  'omi_gltf_ext.py',
  'omi_register.py',
];
const INTERNAL_MODULE = /^omi_/;          // stripped in the bundle
const BUNDLE_NAME = 'omi_physics_body_gltf_extension.py';

const args = new Set(process.argv.slice(2));
const WANT_ZIP = !args.has('--no-zip');
const WANT_VERIFY = args.has('--verify');
const outPath = (() => {
  const i = process.argv.indexOf('--out');
  return i !== -1 ? resolve(process.argv[i + 1]) : join(DIST, BUNDLE_NAME);
})();

function fail(msg) {
  console.error(`[build] ERROR: ${msg}`);
  process.exit(1);
}

// --------------------------------------------------------------- parsing

/** Count unbalanced parentheses on a line (import blocks only - no strings). */
function parenDelta(line) {
  let d = 0;
  for (const ch of line) {
    if (ch === '(') d += 1;
    if (ch === ')') d -= 1;
  }
  return d;
}

/** Parse one import statement string into structured records. */
function parseImportStmt(stmt, file) {
  const single = stmt
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .join(' ')
    .replace(/\s*#.*$/, ''); // trailing comments on import lines
  let m = single.match(/^from\s+([\w.]+)\s+import\s+\((.*)\)\s*$/);
  if (!m) m = single.match(/^from\s+([\w.]+)\s+import\s+(.+)\s*$/);
  if (m) {
    const names = m[2].split(',').map((s) => s.trim()).filter(Boolean);
    return [{ kind: 'from', module: m[1], names }];
  }
  m = single.match(/^import\s+(.+)\s*$/);
  if (m) {
    return m[1]
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map((mod) => ({ kind: 'plain', module: mod }));
  }
  console.warn(`[build] WARNING: unparseable import in ${file}: ${stmt.split('\n')[0]}`);
  return [];
}

/**
 * Split a module into { imports, body }.
 * Only module-level (unindented) import statements are extracted;
 * indented imports (lazy runtime imports) stay in the body.
 */
function parseModule(file, source) {
  const lines = source.split('\n');
  const imports = [];
  const body = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^(import|from)\s/.test(line)) {
      const stmt = [line];
      let depth = parenDelta(line);
      i += 1;
      while (depth > 0 && i < lines.length) {
        stmt.push(lines[i]);
        depth += parenDelta(lines[i]);
        i += 1;
      }
      imports.push(...parseImportStmt(stmt.join('\n'), file));
    } else {
      body.push(line);
      i += 1;
    }
  }
  while (body.length && body[0].trim() === '') body.shift();
  while (body.length && body[body.length - 1].trim() === '') body.pop();
  return { file, imports, body: body.join('\n') };
}

/** Merge external imports; drop intra-project (omi_*) imports. */
function mergeImports(modules) {
  const plain = new Map(); // module name -> nothing (set semantics)
  const from = new Map(); // module name -> ordered unique names
  for (const mod of modules) {
    for (const imp of mod.imports) {
      if (INTERNAL_MODULE.test(imp.module)) continue; // dropped
      if (imp.kind === 'plain') {
        if (!plain.has(imp.module)) plain.set(imp.module, true);
      } else {
        if (!from.has(imp.module)) from.set(imp.module, []);
        const names = from.get(imp.module);
        for (const n of imp.names) if (!names.includes(n)) names.push(n);
      }
    }
  }
  const out = [];
  for (const mod of [...plain.keys()].sort()) out.push(`import ${mod}`);
  for (const mod of [...from.keys()].sort()) {
    const names = from.get(mod);
    if (names.length > 3) {
      out.push(`from ${mod} import (\n${names.map((n) => `    ${n},`).join('\n')}\n)`);
    } else {
      out.push(`from ${mod} import ${names.join(', ')}`);
    }
  }
  return out.join('\n');
}

// ------------------------------------------------------------------ meta

/** Extremely small TOML reader for the flat string keys we need. */
function parseManifestToml(text) {
  const get = (key) => {
    const m = text.match(new RegExp(`^\\s*${key}\\s*=\\s*"([^"]+)"`, 'm'));
    return m ? m[1] : null;
  };
  return {
    version: get('version'),
    maintainer: get('maintainer'),
    website: get('website'),
    id: get('id'),
  };
}

/** Sync bl_info fields in the OUTPUT from the manifest; report changes. */
function patchBlInfo(bundle, manifest) {
  const notes = [];
  let out = bundle;
  const verTuple = manifest.version.split('.').map((s) => parseInt(s, 10)).join(', ');

  let m = out.match(/("version"\s*:\s*)\(([^)]*)\)/);
  if (m && m[2] !== verTuple) {
    notes.push(`version: (${m[2]}) -> (${verTuple})  [blender_manifest.toml]`);
    out = out.replace(m[0], `${m[1]}(${verTuple})`);
  }
  m = out.match(/("author"\s*:\s*)"([^"]*)"/);
  if (m && manifest.maintainer && m[2] !== manifest.maintainer) {
    notes.push(`author: "${m[2]}" -> "${manifest.maintainer}"  [maintainer]`);
    out = out.replace(m[0], `${m[1]}"${manifest.maintainer}"`);
  }
  m = out.match(/("doc_url"\s*:\s*)"([^"]*)"/);
  if (m && manifest.website && m[2] !== manifest.website) {
    notes.push(`doc_url: "${m[2]}" -> "${manifest.website}"  [website]`);
    out = out.replace(m[0], `${m[1]}"${manifest.website}"`);
  }
  return { out, notes };
}

/** Remove bl_info (and its comment) - for the Blender extension package. */
function stripBlInfo(text) {
  return text
    .replace(/^# bl_info serves as the addon manifest that Blender reads on install\.\n/m, '')
    .replace(/^bl_info\s*=\s*\{[\s\S]*?^\}\n/m, '')
    .replace(/\n{4,}/g, '\n\n\n');
}

// ------------------------------------------------------------------- zip

let CRC_TABLE;
function crc32(buf) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (const b of buf) crc = CRC_TABLE[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date) {
  const time = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
  const d = ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { time, date: d };
}

/** Minimal but spec-correct ZIP writer (DEFLATE, UTF-8 names). */
function makeZip(entries) {
  const { time, date } = dosDateTime(new Date());
  const local = [];
  const central = [];
  let offset = 0;
  for (const entry of entries) {
    const nameBuf = Buffer.from(entry.name, 'utf8');
    const crc = crc32(entry.data);
    const comp = deflateRawSync(entry.data);

    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); // local file header signature
    lh.writeUInt16LE(20, 4); // version needed
    lh.writeUInt16LE(0x0800, 6); // flags: UTF-8 filename
    lh.writeUInt16LE(8, 8); // method: deflate
    lh.writeUInt16LE(time, 10);
    lh.writeUInt16LE(date, 12);
    lh.writeUInt32LE(crc, 14);
    lh.writeUInt32LE(comp.length, 18);
    lh.writeUInt32LE(entry.data.length, 22);
    lh.writeUInt16LE(nameBuf.length, 26);
    lh.writeUInt16LE(0, 28); // extra len
    local.push(lh, nameBuf, comp);

    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); // central dir signature
    ch.writeUInt16LE(20, 4); // version made by
    ch.writeUInt16LE(20, 6); // version needed
    ch.writeUInt16LE(0x0800, 8); // flags
    ch.writeUInt16LE(8, 10); // method
    ch.writeUInt16LE(time, 12);
    ch.writeUInt16LE(date, 14);
    ch.writeUInt32LE(crc, 16);
    ch.writeUInt32LE(comp.length, 20);
    ch.writeUInt32LE(entry.data.length, 24);
    ch.writeUInt16LE(nameBuf.length, 28);
    ch.writeUInt16LE(0, 30); // extra len
    ch.writeUInt16LE(0, 32); // comment len
    ch.writeUInt16LE(0, 34); // disk start
    ch.writeUInt16LE(0, 36); // internal attrs
    ch.writeUInt32LE(0, 38); // external attrs
    ch.writeUInt32LE(offset, 42); // local header offset
    central.push(ch, nameBuf);

    offset += lh.length + nameBuf.length + comp.length;
  }
  const centralDir = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(0, 4); // disk number
  eocd.writeUInt16LE(0, 6); // disk with central dir
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(centralDir.length, 12);
  eocd.writeUInt32LE(offset, 16);
  eocd.writeUInt16LE(0, 20); // comment len
  return Buffer.concat([...local, centralDir, eocd]);
}

// ------------------------------------------------------------------ build

const banner = (file) =>
  ['# '.padEnd(76, '='), `# [bundled from src/${file}]`, '# '.padEnd(76, '=')].join('\n');

const modules = ORDER.map((file) => {
  const path = join(SRC, file);
  if (!existsSync(path)) fail(`missing src module: ${path}`);
  return parseModule(file, readFileSync(path, 'utf8'));
});

const manifestPath = join(ROOT, 'blender_manifest.toml');
if (!existsSync(manifestPath)) fail(`missing blender_manifest.toml: ${manifestPath}`);
const manifest = parseManifestToml(readFileSync(manifestPath, 'utf8'));
if (!manifest.version) fail('blender_manifest.toml: could not read "version"');
if (!manifest.id) fail('blender_manifest.toml: could not read "id"');

// meta section (bl_info + docstring) + merged imports + section bodies
const metaBody = modules[0].body;
const importBlock = mergeImports(modules);
const sections = modules
  .slice(1)
  .map((mod) => `${banner(mod.file)}\n${mod.body}`)
  .join('\n\n\n');

let bundle = [metaBody, importBlock, sections].join('\n\n\n') + '\n';
const { out, notes } = patchBlInfo(bundle, manifest);
bundle = out;
for (const n of notes) console.warn(`[build] bl_info synced: ${n}`);
if (notes.length) {
  console.warn('[build] -> update src/omi_meta.py to match blender_manifest.toml');
}

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, bundle, 'utf8');
console.log(`[build] bundle   : ${outPath} (${Buffer.byteLength(bundle)} bytes)`);

if (WANT_ZIP) {
  const extInit = stripBlInfo(bundle);
  const zipPath = join(DIST, `${manifest.id}-${manifest.version}.zip`);
  const zip = makeZip([
    { name: 'blender_manifest.toml', data: readFileSync(manifestPath) },
    { name: '__init__.py', data: Buffer.from(extInit, 'utf8') },
  ]);
  writeFileSync(zipPath, zip);
  console.log(`[build] extension: ${zipPath} (${zip.length} bytes)`);
}

if (WANT_VERIFY) {
  const verifyScript = join(ROOT, 'scripts', 'verify_bundle.py');
  if (!existsSync(verifyScript)) {
    console.warn(`[build] --verify skipped: ${verifyScript} not found`);
  } else {
    try {
      execFileSync('python3', [verifyScript, outPath], { stdio: 'inherit' });
      console.log('[build] verify: OK');
    } catch (err) {
      console.error('[build] verify: FAILED');
      process.exitCode = 1;
    }
  }
}
