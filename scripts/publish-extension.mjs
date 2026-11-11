#!/usr/bin/env node
/**
 * Upload the built Blender extension zip to extensions.blender.org.
 *
 * The official endpoint is documented at
 * https://developer.blender.org/docs/features/extensions/ci_cd/ :
 * POST /api/v1/extensions/<id>/versions/upload/ with a bearer token and a
 * multipart body carrying version_file + release_notes.
 *
 * Dry-run never needs a token and never calls the network. A real publish
 * fails closed when BLENDER_EXTENSIONS_TOKEN is missing, the API rejects the
 * upload, or the server records a version other than the one submitted; it
 * never reports a version that the server did not accept. Both modes report the
 * zip sha256 so a release log identifies the exact artifact.
 */
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { basename } from 'node:path'
import { pathToFileURL } from 'node:url'

export const DEFAULT_API_URL = 'https://extensions.blender.org'
export const DEFAULT_EXTENSION_ID = 'dsh_blender_bridge'
export const DEFAULT_MANIFEST = 'extension/dsh_blender_bridge/blender_manifest.toml'

/** Build the documented upload URL without a double slash. */
export function extensionUploadUrl(apiUrl, extensionId) {
  return (
    apiUrl.replace(/\/+$/, '') +
    '/api/v1/extensions/' +
    encodeURIComponent(extensionId) +
    '/versions/upload/'
  )
}

/** Read the extension version from the generated manifest. */
export function readManifestVersion(manifestPath = DEFAULT_MANIFEST) {
  const text = readFileSync(manifestPath, 'utf8')
  const match = text.match(/^version\s*=\s*"([^"]+)"/m)
  if (!match) throw new Error('blender_manifest.toml has no top-level version: ' + manifestPath)
  return match[1]
}

function responseVersion(body) {
  if (!body || typeof body !== 'object') return null
  const candidate = body.version
  if (typeof candidate === 'string') return candidate
  if (candidate && typeof candidate === 'object' && typeof candidate.version === 'string') {
    return candidate.version
  }
  return null
}

/**
 * Publish one extension version. Returns a result instead of printing unless a
 * caller supplies a logger; a rejected upload throws.
 */
export async function publishExtension(options) {
  const {
    zipPath,
    notes,
    token,
    extensionId = DEFAULT_EXTENSION_ID,
    version,
    apiUrl = DEFAULT_API_URL,
    manifestPath = DEFAULT_MANIFEST,
    dryRun = false,
    fetchImpl = globalThis.fetch,
    log = () => {},
  } = options
  if (!zipPath || !existsSync(zipPath))
    throw new Error('extension zip not found: ' + String(zipPath))
  if (typeof notes !== 'string' || notes.trim() === '')
    throw new Error('release notes are required')
  const manifestVersion = readManifestVersion(manifestPath)
  const submittedVersion = version || manifestVersion
  if (version && version !== manifestVersion) {
    throw new Error(
      '--version ' + version + ' disagrees with blender_manifest version ' + manifestVersion,
    )
  }
  const zipBytes = readFileSync(zipPath)
  const zipSha256 = createHash('sha256').update(zipBytes).digest('hex')
  const url = extensionUploadUrl(apiUrl, extensionId)
  const bytes = zipBytes.length
  if (dryRun) {
    log(
      'DRY RUN extension=' +
        extensionId +
        ' version=' +
        submittedVersion +
        ' bytes=' +
        String(bytes) +
        ' sha256=' +
        zipSha256 +
        ' url=' +
        url +
        ' token=' +
        (token ? 'present' : 'missing'),
    )
    return {
      published: false,
      dryRun: true,
      extensionId,
      submittedVersion,
      manifestVersion,
      zipSha256,
      url,
    }
  }
  if (!token) {
    throw new Error(
      'BLENDER_EXTENSIONS_TOKEN is not set; add the repository secret or submit the first version manually at ' +
        'https://extensions.blender.org/',
    )
  }
  const form = new FormData()
  form.append('version_file', new Blob([zipBytes], { type: 'application/zip' }), basename(zipPath))
  form.append('release_notes', notes)
  const response = await fetchImpl(url, {
    method: 'POST',
    headers: { Authorization: 'bearer ' + token },
    body: form,
  })
  const text = await response.text()
  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    throw new Error(
      'extension upload rejected (HTTP ' + String(response.status) + '): ' + text.slice(0, 500),
    )
  }
  const serverVersion = responseVersion(body)
  if (serverVersion && serverVersion !== submittedVersion) {
    throw new Error(
      'extensions.blender.org recorded version ' +
        serverVersion +
        ', expected ' +
        submittedVersion +
        '; do not report this upload as published',
    )
  }
  log(
    'PUBLISHED extension=' +
      extensionId +
      ' version=' +
      submittedVersion +
      ' sha256=' +
      zipSha256 +
      (serverVersion ? ' serverVersion=' + serverVersion : ''),
  )
  return {
    published: true,
    dryRun: false,
    extensionId,
    submittedVersion,
    manifestVersion,
    serverVersion,
    zipSha256,
    url,
    response: body,
  }
}

function parseArgs(argv) {
  const parsed = { notesFiles: [], dryRun: false }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--dry-run') parsed.dryRun = true
    else if (arg === '--zip') parsed.zipPath = argv[++i]
    else if (arg === '--notes-file') parsed.notesFiles.push(argv[++i])
    else if (arg === '--notes') parsed.notes = argv[++i]
    else if (arg === '--extension-id') parsed.extensionId = argv[++i]
    else if (arg === '--version') parsed.version = argv[++i]
    else if (arg === '--api-url') parsed.apiUrl = argv[++i]
    else if (arg === '--manifest') parsed.manifestPath = argv[++i]
    else throw new Error('unknown argument: ' + arg)
  }
  return parsed
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const notes = args.notes ?? args.notesFiles.map((file) => readFileSync(file, 'utf8')).join('\n')
  const result = await publishExtension({
    zipPath: args.zipPath,
    notes,
    token: process.env.BLENDER_EXTENSIONS_TOKEN,
    extensionId: args.extensionId,
    version: args.version,
    apiUrl: args.apiUrl,
    manifestPath: args.manifestPath,
    dryRun: args.dryRun,
    log: console.log,
  })
  if (result.dryRun) console.log('extension publish dry run complete')
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error('extension publish failed: ' + error.message)
    process.exit(1)
  })
}
