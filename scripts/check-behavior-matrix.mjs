#!/usr/bin/env node
/**
 * T14 behavior coverage matrix checker.
 *
 * Parses tests/BEHAVIOR_MATRIX.md and compares it with the live source of truth:
 *   - model tools: every .ts under src (vTool registrations; the schema-diagnostic
 *     duplicate is not a model-facing tool and is skipped)
 *   - addon commands: runtime/catalog.mjs COMMAND_CATALOG, each backed by
 *     the addon package (all .py under addon/dsh_blender_bridge/)
 *   - plan ops: runtime/catalog.mjs PLAN_CATALOG (one row per op entry)
 *   - HTTP routes: CONTRACT_ROUTES from runtime/http/routes/index.mjs
 *   - fixed protocol/lifecycle/config/install/security/version rows
 *
 * Exit 1 on any gap. --release also requires planned=0. --report <path>
 * writes a deterministic JSON report.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..');
const MATRIX = path.join(ROOT, 'tests', 'BEHAVIOR_MATRIX.md');
const COLUMNS = [
  'id',
  'surface',
  'behavior',
  'contract source',
  'unit',
  'integration',
  'e2e',
  'status',
  'exception/owner',
];
const STATUSES = new Set(['covered', 'planned', 'exempt']);
const PLACEHOLDER = new Set(['', '—', '-', 'n/a', 'N/A']);

const FIXED = {
  protocol: [
    'PROTO-HANDSHAKE',
    'PROTO-VERSION-MISMATCH',
    'PROTO-FRAME-BOUNDARY',
    'PROTO-PARTIAL-FRAME',
    'PROTO-DISCONNECT',
    'PROTO-TIMEOUT',
    'PROTO-ERROR-ENVELOPE',
    'PROTO-PAUSE',
  ],
  lifecycle: [
    'LIFECYCLE-START',
    'LIFECYCLE-STOP',
    'LIFECYCLE-RESTART',
    'LIFECYCLE-WATCHDOG',
    'LIFECYCLE-PORT-CONFLICT',
    'LIFECYCLE-CRASH',
    'LIFECYCLE-WORKER-TTL',
    'LIFECYCLE-UNINSTALL',
  ],
  config: [
    'CONFIG-DEFAULTS',
    'CONFIG-ENV',
    'CONFIG-FILE',
    'CONFIG-INVALID',
    'CONFIG-MISSING-DSH-SERVICES',
  ],
  install: [
    'INSTALL-NPM-PACK',
    'INSTALL-TARBALL',
    'INSTALL-ADDON-ZIP',
    'INSTALL-OLD-ADDON-REMOVAL',
    'INSTALL-RESTART-DSH',
    'INSTALL-DOCTOR',
  ],
  security: [
    'SEC-FILE-GUARD',
    'SEC-ZIP-SLIP',
    'SEC-SYMLINK',
    'SEC-INJECTION',
    'SEC-LEASE-BYPASS',
  ],
  environment: [
    'ENV-BLENDER-42',
    'ENV-BLENDER-5X',
    'ENV-DSH-CURRENT',
    'ENV-DSH-PREVIOUS',
  ],
};

const failures = [];
const fail = (message) => failures.push(message);
const rel = (file) => path.relative(ROOT, file).replaceAll(path.sep, '/');

function readTree(relative, extension) {
  const root = path.join(ROOT, relative);
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(extension)) out.push(full);
    }
  };
  if (existsSync(root)) walk(root);
  out.sort();
  return out;
}

function splitRow(line) {
  let text = line.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|')) text = text.slice(0, -1);
  return text.split('|').map((cell) => cell.trim());
}

function loadMatrix(matrixPath = MATRIX) {
  if (!existsSync(matrixPath)) {
    fail('missing matrix: ' + rel(matrixPath));
    return [];
  }
  const lines = readFileSync(matrixPath, 'utf8').replace(/\r\n/g, '\n').split('\n');
  const headerIndex = lines.findIndex((line) => line.trim().startsWith('| id |'));
  if (headerIndex < 0) {
    fail('matrix has no "| id |" table header');
    return [];
  }
  const header = splitRow(lines[headerIndex]);
  if (header.length !== COLUMNS.length || header.some((cell, index) => cell !== COLUMNS[index])) {
    fail('matrix header must be exactly: ' + COLUMNS.join(' | '));
    return [];
  }
  const rows = [];
  for (let index = headerIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() === '') {
      if (rows.length > 0) break;
      continue;
    }
    if (!line.trim().startsWith('|')) {
      if (rows.length > 0) break;
      continue;
    }
    if (/^\|[\s:|-]+\|$/.test(line.trim())) continue;
    const cells = splitRow(line);
    if (cells.length !== COLUMNS.length) {
      fail('row ' + (index + 1) + ' has ' + cells.length + ' cells, expected ' + COLUMNS.length);
      continue;
    }
    const row = Object.fromEntries(COLUMNS.map((column, columnIndex) => [column, cells[columnIndex]]));
    row.line = index + 1;
    rows.push(row);
  }
  return rows;
}

async function loadCatalogs() {
  const catalog = await import(pathToFileURL(path.join(ROOT, 'runtime', 'catalog.mjs')).href);
  const toolNames = [];
  const walkTools = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walkTools(full);
      else if (entry.name.endsWith('.ts')) {
        const chunks = readFileSync(full, 'utf8').split('vTool({').slice(1);
        for (const chunk of chunks) {
          const match = chunk.match(/name:\s*'([^']+)'/);
          if (!match) continue;
          if (chunk.slice(0, 3000).includes('仅诊断')) continue;
          toolNames.push(match[1]);
        }
      }
    }
  };
  walkTools(path.join(ROOT, 'src'));
  toolNames.sort();

  const commandCatalog = catalog.COMMAND_CATALOG || {};
  const planCatalog = catalog.PLAN_CATALOG || [];
  const ops = [];
  const families = new Set();
  for (const family of planCatalog) {
    families.add(family.f);
    for (const op of family.ops || []) ops.push({ family: family.f, op });
  }

  const routesModule = await import(pathToFileURL(path.join(ROOT, 'runtime', 'http', 'routes', 'index.mjs')).href);
  const routes = Array.isArray(routesModule.CONTRACT_ROUTES) ? routesModule.CONTRACT_ROUTES : [];
  if (routes.length === 0) fail('cannot find CONTRACT_ROUTES in runtime/http/routes/index.mjs');

  const addonSource = readTree('addon/dsh_blender_bridge', '.py').map((full) => readFileSync(full, 'utf8')).join('\n');
  const protocolMatch = addonSource.match(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/);
  const protocol = protocolMatch ? Number(protocolMatch[1]) : null;
  const handshake = addonSource.slice(
    addonSource.indexOf('def get_addon_info'),
    addonSource.indexOf('def get_scene_info'),
  );
  const capabilityMatch = handshake.match(/"capabilities":\s*sorted\(\[([\s\S]*?)\]\)/);
  const capabilityCount = capabilityMatch
    ? (capabilityMatch[1].match(/"[^"]+"/g) || []).length
    : 0;

  return { toolNames, commandCatalog, planCatalog, ops, families, routes, addonSource, protocol, capabilityCount };
}

function expectedIds(catalogs) {
  const ids = [];
  for (const name of catalogs.toolNames) ids.push('TOOL-' + name);
  for (const name of Object.keys(catalogs.commandCatalog)) ids.push('CMD-' + name);
  for (const entry of catalogs.ops) ids.push('OP-' + entry.family + '-' + entry.op);
  for (const route of catalogs.routes) ids.push('ROUTE-' + route.replace(/^\//, '').replaceAll('/', '-'));
  for (const list of Object.values(FIXED)) ids.push(...list);
  return ids;
}

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + 'T00:00:00Z');
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function checkTestRef(reference, row) {
  if (PLACEHOLDER.has(reference)) return false;
  const separator = reference.indexOf('::');
  const file = separator >= 0 ? reference.slice(0, separator) : reference;
  const title = separator >= 0 ? reference.slice(separator + 2) : '';
  if (!/\.(?:ts|mjs)$/.test(file)) {
    fail(row.id + ': test reference is not a .ts/.mjs file: ' + reference);
    return false;
  }
  const absolute = path.resolve(ROOT, file);
  if (!absolute.startsWith(ROOT + path.sep)) {
    fail(row.id + ': test reference escapes the package: ' + reference);
    return false;
  }
  if (!existsSync(absolute)) {
    fail(row.id + ': missing test file ' + file);
    return false;
  }
  if (title) {
    const text = readFileSync(absolute, 'utf8').replace(/\r\n/g, '\n');
    if (!text.includes(title)) {
      fail(row.id + ': ' + file + ' has no test titled ' + JSON.stringify(title));
      return false;
    }
  }
  return true;
}

function checkExemption(row) {
  const value = row['exception/owner'];
  const prefix = 'EXEMPT-' + row.id + ';';
  if (!value.startsWith(prefix)) {
    fail(row.id + ': exempt row must start with ' + JSON.stringify(prefix));
    return;
  }
  const rest = value.slice(prefix.length);
  const fields = {};
  for (const part of rest.split(';')) {
    const separator = part.indexOf('=');
    if (separator < 0) continue;
    fields[part.slice(0, separator).trim()] = part.slice(separator + 1).trim();
  }
  for (const field of ['issue', 'owner', 'reason']) {
    if (!fields[field]) fail(row.id + ': exempt row is missing ' + field + '=');
  }
  if (!fields.review || !validDate(fields.review)) {
    fail(row.id + ': exempt row needs review=YYYY-MM-DD');
  }
}

function checkAddonCommands(catalogs) {
  for (const name of Object.keys(catalogs.commandCatalog)) {
    const pattern = new RegExp('["\\\']' + name.replace(/[.*+?^\${}()|[\]\\]/g, '\\$&') + '["\\\']\\s*:');
    if (!pattern.test(catalogs.addonSource)) {
      fail('COMMAND_CATALOG command ' + name + ' has no handler in addon/dsh_blender_bridge/');
    }
  }
  if (!/cmd_type\s*==\s*["']ping["']/.test(catalogs.addonSource)) {
    fail('addon ping special-case is missing');
  }
  if (catalogs.protocol !== 11) {
    fail('addon protocol version is ' + String(catalogs.protocol) + ', expected 11');
  }
  if (catalogs.capabilityCount !== 16) {
    fail('addon handshake advertises ' + catalogs.capabilityCount + ' capabilities, expected 16');
  }
}

function parseArgs(argv) {
  const options = { release: false, report: null, matrix: MATRIX };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--release') options.release = true;
    else if (arg === '--report' || arg === '--matrix') {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) {
        console.error('check-behavior-matrix: ' + arg + ' needs a path');
        process.exit(2);
      }
      if (arg === '--report') options.report = value;
      else options.matrix = path.resolve(value);
      index += 1;
    } else {
      console.error('check-behavior-matrix: unknown argument ' + arg);
      process.exit(2);
    }
  }
  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const catalogs = await loadCatalogs();
  const rows = loadMatrix(options.matrix);
  const expected = expectedIds(catalogs);
  const expectedSet = new Set(expected);
  const seen = new Set();
  const counts = { covered: 0, planned: 0, exempt: 0 };

  if (expected.length !== expectedSet.size) {
    const duplicates = expected.filter((id, index) => expected.indexOf(id) !== index);
    fail('source of truth produces duplicate ids: ' + [...new Set(duplicates)].join(', '));
  }

  for (const row of rows) {
    if (seen.has(row.id)) fail(row.id + ': duplicate matrix id');
    seen.add(row.id);
    if (!expectedSet.has(row.id)) fail(row.id + ': row has no source-of-truth counterpart');
    if (!STATUSES.has(row.status)) {
      fail(row.id + ': invalid status ' + JSON.stringify(row.status));
      continue;
    }
    counts[row.status] += 1;
    if (row.status === 'covered') {
      const e2eRef = /\.(?:ts|mjs)(?:::.*)?$/.test(row.e2e.trim()) ? [row.e2e] : [];
      const refs = [row.unit, row.integration, ...e2eRef]
        .flatMap((cell) => cell.split(';')).map((value) => value.trim());
      const real = refs.filter((value) => !PLACEHOLDER.has(value));
      if (real.length === 0) {
        fail(row.id + ': covered row has no unit/integration/e2e test reference');
      }
      let valid = false;
      for (const reference of real) {
        if (checkTestRef(reference, row)) valid = true;
      }
      if (real.length > 0 && !valid) fail(row.id + ': covered row has no valid test reference');
      if (!PLACEHOLDER.has(row['exception/owner'])) {
        fail(row.id + ': covered row must not carry an exemption');
      }
    } else if (row.status === 'exempt') {
      checkExemption(row);
    } else if (row['exception/owner'] !== '' && row['exception/owner'] !== '—') {
      fail(row.id + ': planned row should use — in exception/owner');
    }
  }

  for (const id of expectedSet) {
    if (!seen.has(id)) fail(id + ': source-of-truth item has no matrix row');
  }

  if (options.release && counts.planned > 0) {
    fail('release gate: ' + counts.planned + ' planned row(s) remain');
  }
  if (counts.exempt > 0) {
    for (const row of rows.filter((item) => item.status === 'exempt')) {
      if (!row['exception/owner'].startsWith('EXEMPT-' + row.id + ';')) {
        // detailed failure already recorded by checkExemption
      }
    }
  }
  checkAddonCommands(catalogs);

  const report = {
    ok: failures.length === 0,
    release: options.release,
    matrix: rel(options.matrix),
    sources: {
      tools: 'src/index.ts',
      commands: 'runtime/catalog.mjs + addon/dsh_blender_bridge/',
      ops: 'runtime/catalog.mjs',
      routes: 'runtime/server.mjs',
    },
    counts: {
      tools: catalogs.toolNames.length,
      commands: Object.keys(catalogs.commandCatalog).length,
      families: catalogs.families.size,
      ops: catalogs.ops.length,
      routes: catalogs.routes.length,
      protocol: FIXED.protocol.length,
      lifecycle: FIXED.lifecycle.length,
      config: FIXED.config.length,
      install: FIXED.install.length,
      security: FIXED.security.length,
      environment: FIXED.environment.length,
      total: rows.length,
      expectedTotal: expectedSet.size,
      covered: counts.covered,
      planned: counts.planned,
      exempt: counts.exempt,
    },
    addon: {
      protocol: catalogs.protocol,
      capabilities: catalogs.capabilityCount,
    },
    failures,
  };

  if (options.report) {
    const target = path.resolve(ROOT, options.report);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, JSON.stringify(report, null, 2) + '\n');
  }

  const summary = 'behavior matrix: ' + rows.length + ' rows (' + counts.covered + ' covered, ' +
    counts.planned + ' planned, ' + counts.exempt + ' exempt) · source ' +
    catalogs.toolNames.length + ' tools / ' + Object.keys(catalogs.commandCatalog).length + ' commands / ' +
    catalogs.families.size + ' families / ' + catalogs.ops.length + ' ops / ' + catalogs.routes.length + ' routes';
  if (failures.length === 0) {
    console.log('OK ' + summary);
    process.exit(0);
  }
  console.error('FAIL ' + summary);
  for (const message of failures) console.error('- ' + message);
  process.exit(1);
}

main().catch((error) => {
  console.error('check-behavior-matrix: ' + (error && error.stack ? error.stack : String(error)));
  process.exit(1);
});
