#!/usr/bin/env node
/**
 * Build addon/dsh_blender_bridge.zip from the add-on package.
 *
 * The zip has a single package root (dsh_blender_bridge/) so Blender's
 * "Install from Disk" treats it as one add-on. Entries are stored (no
 * compression) and use a fixed DOS timestamp so identical trees produce
 * identical bytes. No __pycache__ / .pyc files are included.
 *
 * Usage:
 *   node scripts/build-addon.mjs [--check]
 *
 * --check only verifies the existing zip manifest and embedded versions.
 */

import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PACKAGE_DIR = join(ROOT, "addon", "dsh_blender_bridge");
const ZIP_PATH = join(ROOT, "addon", "dsh_blender_bridge.zip");
const PACKAGE_ROOT = "dsh_blender_bridge";
const CHECK_ONLY = process.argv.includes("--check");

function fail(message) {
  console.error("build-addon: " + message);
  process.exit(1);
}

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (name === "__pycache__") continue;
      out.push(...walk(path));
    } else if (stat.isFile() && name.endsWith(".py")) {
      out.push(path);
    }
  }
  return out;
}

function zipName(path) {
  return PACKAGE_ROOT + "/" + relative(PACKAGE_DIR, path).split(sep).join("/");
}

function readVersions() {
  const init = readFileSync(join(PACKAGE_DIR, "__init__.py"), "utf8");
  const protocol = readFileSync(join(PACKAGE_DIR, "protocol.py"), "utf8");
  const blInfoVersion = init.match(/["']version["']\s*:\s*\((\d+),\s*(\d+)\)/);
  const addonVersion = protocol.match(/ADDON_VERSION\s*=\s*\((\d+),\s*(\d+)\)/);
  const protocolVersion = protocol.match(/ADDON_PROTOCOL_VERSION\s*=\s*(\d+)/);
  if (!blInfoVersion || !addonVersion || !protocolVersion) {
    fail("could not read bl_info/addon/protocol versions from the package");
  }
  const result = {
    bl_info_version: [Number(blInfoVersion[1]), Number(blInfoVersion[2])],
    addon_version: [Number(addonVersion[1]), Number(addonVersion[2])],
    protocol_version: Number(protocolVersion[1]),
  };
  if (JSON.stringify(result.bl_info_version) !== JSON.stringify(result.addon_version)) {
    fail("bl_info.version and protocol.ADDON_VERSION disagree");
  }
  if (result.protocol_version !== 11) {
    fail("protocol version must stay 11 (got " + result.protocol_version + ")");
  }
  return result;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function buildZip(entries) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  const dosTime = 0;
  const dosDate = 33; // 1980-01-01

  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const data = entry.data;
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(dosTime, 10);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, name, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(dosTime, 12);
    central.writeUInt16LE(dosDate, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, name);

    offset += local.length + name.length + data.length;
  }

  const centralBuffer = Buffer.concat(centralParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralBuffer.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, centralBuffer, end]);
}

function readZipManifest(buffer) {
  if (buffer.readUInt32LE(0) !== 0x04034b50) fail("zip has no local file header");
  let offset = 0;
  const names = [];
  while (buffer.readUInt32LE(offset) === 0x04034b50) {
    const flags = buffer.readUInt16LE(offset + 6);
    const size = buffer.readUInt32LE(offset + 18);
    const nameLength = buffer.readUInt16LE(offset + 26);
    const extraLength = buffer.readUInt16LE(offset + 28);
    const name = buffer.subarray(offset + 30, offset + 30 + nameLength).toString("utf8");
    names.push({ name, size, flags });
    offset += 30 + nameLength + extraLength + size;
  }
  return names;
}

function main() {
  if (!existsSync(join(PACKAGE_DIR, "__init__.py"))) fail("package __init__.py is missing");
  const versions = readVersions();
  if (CHECK_ONLY) {
    if (!existsSync(ZIP_PATH)) fail("zip does not exist");
    const buffer = readFileSync(ZIP_PATH);
    const entries = readZipManifest(buffer);
    if (!entries.some((entry) => entry.name === PACKAGE_ROOT + "/__init__.py")) {
      fail("zip does not contain the package __init__.py at its root");
    }
    for (const entry of entries) {
      if (!entry.name.startsWith(PACKAGE_ROOT + "/")) fail("zip entry outside package root: " + entry.name);
      if (entry.name.includes("__pycache__") || entry.name.endsWith(".pyc")) {
        fail("zip contains a generated cache file: " + entry.name);
      }
    }
    console.log(JSON.stringify({
      ok: true,
      check: true,
      zip: relative(ROOT, ZIP_PATH).split(sep).join("/"),
      package_root: PACKAGE_ROOT,
      entries: entries.length,
      versions,
      sha256: createHash("sha256").update(buffer).digest("hex"),
    }, null, 2));
    return;
  }

  const files = walk(PACKAGE_DIR);
  if (files.length === 0) fail("package contains no Python files");
  if (!files.some((path) => zipName(path) === PACKAGE_ROOT + "/__init__.py")) {
    fail("package root __init__.py is missing");
  }
  const entries = files.map((path) => ({ name: zipName(path), data: readFileSync(path) }));
  const zip = buildZip(entries);
  writeFileSync(ZIP_PATH, zip);

  const sha256 = createHash("sha256").update(zip).digest("hex");
  console.log(JSON.stringify({
    ok: true,
    zip: relative(ROOT, ZIP_PATH).split(sep).join("/"),
    package_root: PACKAGE_ROOT,
    entries: entries.length,
    bytes: zip.length,
    sha256,
    versions,
  }, null, 2));
}

main();
