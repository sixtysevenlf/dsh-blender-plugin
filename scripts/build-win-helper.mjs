#!/usr/bin/env node
// Build native/win-hidden-desktop/helper.cpp with MSVC from the Visual Studio
// Build Tools install. The binary is a build artifact: it is written under
// native/win-hidden-desktop/out/ (gitignored) and packaged by the release
// pipeline; it is never committed.
//
// Usage: npm run build:win-helper
//        node scripts/build-win-helper.mjs [--out <dir>]

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const source = join(root, 'native', 'win-hidden-desktop', 'helper.cpp')
const defaultOut = join(root, 'native', 'win-hidden-desktop', 'out')

function fail(message) {
  console.error('[build-win-helper] ' + message)
  process.exit(1)
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options })
  if (result.error) {
    fail(command + ' failed: ' + result.error.message)
  }
  return result
}

const args = process.argv.slice(2)
let outDir = defaultOut
for (let index = 0; index < args.length; index += 1) {
  if (args[index] === '--out' && args[index + 1]) {
    outDir = resolve(args[index + 1])
    index += 1
  } else {
    fail('unknown argument: ' + args[index])
  }
}

if (process.platform !== 'win32') {
  fail('the hidden-desktop helper requires Windows and MSVC')
}
if (!existsSync(source)) {
  fail('missing source file: ' + source)
}

const programFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)'
const vswhere = join(programFilesX86, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
if (!existsSync(vswhere)) {
  fail('vswhere.exe not found; install Visual Studio 2022 Build Tools with the MSVC x64 toolchain')
}

const vswhereResult = run(vswhere, [
  '-latest',
  '-products',
  '*',
  '-requires',
  'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
  '-property',
  'installationPath',
])
const vsPath = (vswhereResult.stdout || '')
  .split(/\r?\n/)
  .map((line) => line.trim())
  .find(Boolean)
if (!vsPath) {
  fail('no Visual Studio instance with Microsoft.VisualStudio.Component.VC.Tools.x86.x64')
}
const vcvars = join(vsPath, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat')
if (!existsSync(vcvars)) {
  fail('missing ' + vcvars)
}

const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const version = pkg.version
let commit = process.env.DSH_HELPER_COMMIT || ''
if (!commit) {
  const git = run('git', ['rev-parse', '--short', 'HEAD'], { cwd: root })
  if (git.status === 0) {
    commit = (git.stdout || '').trim()
  }
}
if (!commit) {
  commit = 'unknown'
}

mkdirSync(outDir, { recursive: true })
const objDir = join(outDir, 'obj')
mkdirSync(objDir, { recursive: true })
const exe = join(outDir, 'dsh-blender-hidden-desktop.exe')
writeFileSync(
  join(outDir, 'build-info.h'),
  [
    '#pragma once',
    '#define DSH_HELPER_VERSION "' + version + '"',
    '#define DSH_HELPER_COMMIT "' + commit + '"',
    '#define DSH_HELPER_PROTOCOL 1',
    '',
  ].join('\r\n'),
)

const bat = join(outDir, 'build.bat')
writeFileSync(
  bat,
  [
    '@echo off',
    'setlocal',
    'call "' + vcvars + '" >nul',
    'if errorlevel 1 exit /b 1',
    'cd /d "' + objDir + '"',
    'cl /nologo /std:c++17 /EHsc /O2 /MT /W4 /I"' +
      outDir +
      '" /DUNICODE /D_UNICODE /Fe:"' +
      exe +
      '" "' +
      source +
      '" user32.lib advapi32.lib',
    'exit /b %errorlevel%',
    '',
  ].join('\r\n'),
)

const build = run('cmd.exe', ['/d', '/s', '/c', bat], {
  cwd: root,
  timeout: 300000,
  maxBuffer: 16 * 1024 * 1024,
})
if (build.status !== 0) {
  if (build.stdout) process.stderr.write(build.stdout)
  if (build.stderr) process.stderr.write(build.stderr)
  fail('MSVC build failed with exit code ' + build.status)
}
if (!existsSync(exe)) {
  fail('the build did not produce ' + exe)
}

function parseJsonLine(result, label) {
  const line = (result.stdout || '')
    .split(/\r?\n/)
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith('{'))
  if (!line) {
    fail(label + ' printed no JSON line')
  }
  try {
    return JSON.parse(line)
  } catch (error) {
    fail(label + ' printed invalid JSON: ' + error.message)
  }
}

const versionInfo = parseJsonLine(run(exe, ['--version'], { timeout: 30000 }), '--version')
if (versionInfo.version !== version || versionInfo.commit !== commit) {
  fail(
    '--version mismatch: expected ' +
      version +
      '/' +
      commit +
      ', got ' +
      JSON.stringify(versionInfo),
  )
}
const selfTest = parseJsonLine(run(exe, ['--self-test'], { timeout: 60000 }), '--self-test')
if (selfTest.ok !== true) {
  fail('--self-test failed: ' + JSON.stringify(selfTest))
}

const sha256 = createHash('sha256').update(readFileSync(exe)).digest('hex')
const size = statSync(exe).size
writeFileSync(
  join(outDir, 'build.json'),
  JSON.stringify(
    {
      name: 'dsh-blender-hidden-desktop',
      version,
      commit,
      protocol: 1,
      sha256,
      size,
      sourceSha256: createHash('sha256').update(readFileSync(source)).digest('hex'),
      builtAt: new Date().toISOString(),
    },
    null,
    2,
  ) + '\n',
)

console.log('[build-win-helper] OK')
console.log('  exe:     ' + exe)
console.log('  sha256:  ' + sha256)
console.log('  version: ' + version + ' (' + commit + ')')
