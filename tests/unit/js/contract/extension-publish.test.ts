import { describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_EXTENSION_ID,
  extensionUploadUrl,
  publishExtension,
  readManifestVersion,
} from '../../../../scripts/publish-extension.mjs'
import { repoPath } from '../../../helpers/docs'

const zipPath = repoPath('extension', 'dsh_blender_bridge.zip')
const manifestPath = repoPath('extension', 'dsh_blender_bridge', 'blender_manifest.toml')
const notes = 'Release 3.1.0 extension package.'

describe('extension publish script', () => {
  it('builds the documented upload URL and reads the manifest version', () => {
    expect(extensionUploadUrl('https://extensions.blender.org/', 'dsh_blender_bridge')).toBe(
      'https://extensions.blender.org/api/v1/extensions/dsh_blender_bridge/versions/upload/',
    )
    expect(DEFAULT_EXTENSION_ID).toBe('dsh_blender_bridge')
    expect(readManifestVersion(manifestPath)).toBe('2.0.0')
  })

  it('dry run does not require a token or call the network', async () => {
    const fetchImpl = vi.fn()
    const log = vi.fn()
    const result = await publishExtension({
      zipPath,
      notes,
      manifestPath,
      dryRun: true,
      fetchImpl,
      log,
    })
    expect(result).toMatchObject({ published: false, dryRun: true, submittedVersion: '2.0.0' })
    expect(fetchImpl).not.toHaveBeenCalled()
    expect(log.mock.calls[0][0]).toContain('DRY RUN')
    expect(log.mock.calls[0][0]).toContain('token=missing')
  })

  it('fails closed when a real publish has no token', async () => {
    await expect(
      publishExtension({ zipPath, notes, manifestPath, fetchImpl: vi.fn() }),
    ).rejects.toThrow(/BLENDER_EXTENSIONS_TOKEN is not set/)
  })

  it('posts multipart version_file and release_notes with a bearer token', async () => {
    let request: { url: string; init: RequestInit } | null = null
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      request = { url, init }
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ version: { version: '2.0.0' }, id: 7 }),
      } as Response
    })
    const result = await publishExtension({
      zipPath,
      notes,
      manifestPath,
      token: 'secret-token',
      fetchImpl,
    })
    expect(result).toMatchObject({
      published: true,
      dryRun: false,
      extensionId: 'dsh_blender_bridge',
      submittedVersion: '2.0.0',
      serverVersion: '2.0.0',
    })
    expect(request).not.toBeNull()
    expect(request!.url).toBe(
      'https://extensions.blender.org/api/v1/extensions/dsh_blender_bridge/versions/upload/',
    )
    expect(request!.init.method).toBe('POST')
    expect((request!.init.headers as Record<string, string>).Authorization).toBe(
      'bearer secret-token',
    )
    const form = request!.init.body as FormData
    expect(form).toBeInstanceOf(FormData)
    expect(form.get('release_notes')).toBe(notes)
    const file = form.get('version_file') as File
    expect(file).toBeInstanceOf(Blob)
    expect(file.name).toBe('dsh_blender_bridge.zip')
    expect(file.size).toBeGreaterThan(100000)
  })

  it('throws on an HTTP rejection instead of reporting a version', async () => {
    const fetchImpl = vi.fn(async () => {
      return { ok: false, status: 403, text: async () => '{"detail":"invalid token"}' } as Response
    })
    await expect(
      publishExtension({ zipPath, notes, manifestPath, token: 'bad', fetchImpl }),
    ).rejects.toThrow(/HTTP 403/)
  })
})
