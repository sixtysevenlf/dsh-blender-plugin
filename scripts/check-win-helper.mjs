#!/usr/bin/env node
// Verify the packaged Windows hidden-desktop helper (task 5.3).
//
// The helper is a build artifact: Windows CI builds it from
// tools/win-hidden-desktop/helper.cpp and the release pipeline stages it under
// vendor/win-hidden-desktop/ before npm pack. This check proves that the
// packaged binary was built from the current source commit and that the npm
// tarball really contains it.
//
// Usage:
//   node scripts/check-win-helper.mjs [--optional] [--packed] [--dir <path>]
//
// --optional: skip with exit 0 when no packaged helper exists (PR jobs on
//             non-Windows runners); release jobs run without it.
// --packed:   also run npm pack --dry-run --json and require the helper files.

import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)
const optional = args.includes('--optional')
const packedCheck = args.includes('--packed')
const dirIndex = args.indexOf('--dir')
const resourceDir = resolve(
  dirIndex >= 0 ? args[dirIndex + 1] : join(root, 'vendor', 'win-hidden-desktop'),
)
const exeName = 'dsh-blender-hidden-desktop.exe'

function fail(message) {
  console.error('[check-win-helper] ' + message)
  process.exit(1)
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    fail('invalid JSON at ' + path + ': ' + error.message)
  }
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

if (!existsSync(join(resourceDir, exeName)) || !existsSync(join(resourceDir, 'build.json'))) {
  if (optional) {
    console.log('[check-win-helper] SKIP: no packaged helper at ' + resourceDir)
    process.exit(0)
  }
  fail('missing packaged helper: expected ' + exeName + ' and build.json in ' + resourceDir)
}

const info = readJson(join(resourceDir, 'build.json'))
const pkg = readJson(join(root, 'package.json'))
if (info.name !== 'dsh-blender-hidden-desktop') {
  fail('unexpected build.json name: ' + String(info.name))
}
if (info.version !== pkg.version) {
  fail('helper version ' + String(info.version) + ' != package.json ' + pkg.version)
}

const head = spawnSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: root, encoding: 'utf8' })
if (head.status !== 0) {
  fail('git rev-parse --short HEAD failed: ' + (head.stderr || '').trim())
}
const commit = (head.stdout || '').trim()
if (info.commit !== commit) {
  fail('helper commit ' + String(info.commit) + ' != HEAD ' + commit + ' (rebuild the helper)')
}

const sourcePath = join(root, 'tools', 'win-hidden-desktop', 'helper.cpp')
if (!existsSync(sourcePath)) {
  fail('missing helper source: ' + sourcePath)
}
const source = readFileSync(sourcePath, 'utf8')
if (info.sourceSha256 !== sha256(sourcePath)) {
  fail('helper sourceSha256 mismatch: the packaged binary is not built from the current helper.cpp')
}
const protocolMatch = source.match(/#define DSH_HELPER_PROTOCOL\s+(\d+)/)
if (!protocolMatch) {
  fail('helper.cpp has no DSH_HELPER_PROTOCOL default')
}
if (Number(protocolMatch[1]) !== info.protocol) {
  fail(
    'helper protocol ' +
      protocolMatch[1] +
      ' (source) != ' +
      String(info.protocol) +
      ' (build.json)',
  )
}

const exePath = join(resourceDir, exeName)
const exeSha = sha256(exePath)
if (info.sha256 !== exeSha) {
  fail('helper sha256 mismatch: build.json ' + String(info.sha256) + ' != binary ' + exeSha)
}

console.log(
  '[check-win-helper] OK ' +
    info.version +
    ' ' +
    info.commit +
    ' protocol ' +
    info.protocol +
    ' sha256 ' +
    exeSha,
)

if (packedCheck) {
  const pack =
    process.platform === 'win32'
      ? spawnSync(
          process.env.ComSpec || 'cmd.exe',
          ['/d', '/s', '/c', 'npm pack --dry-run --json --ignore-scripts'],
          {
            cwd: root,
            encoding: 'utf8',
            timeout: 180000,
          },
        )
      : spawnSync('npm', ['pack', '--dry-run', '--json', '--ignore-scripts'], {
          cwd: root,
          encoding: 'utf8',
          timeout: 180000,
        })
  if (pack.status !== 0) {
    fail('npm pack --dry-run failed: ' + ((pack.stderr || '') + (pack.stdout || '')).slice(-2000))
  }
  let entries = null
  try {
    entries = JSON.parse(pack.stdout)
  } catch (error) {
    fail('npm pack --dry-run printed invalid JSON: ' + error.message)
  }
  const files = ((entries && entries[0] && entries[0].files) || []).map((file) =>
    String(file.path).replace(/\\/g, '/'),
  )
  for (const required of [
    'vendor/win-hidden-desktop/' + exeName,
    'vendor/win-hidden-desktop/build.json',
  ]) {
    if (!files.includes(required)) {
      fail('the npm tarball does not include ' + required)
    }
  }
  console.log('[check-win-helper] OK the npm tarball includes the helper resource')
}
