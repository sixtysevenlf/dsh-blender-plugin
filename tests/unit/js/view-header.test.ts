import { describe, expect, it } from 'vitest'
import { viewHeader } from '../../../runtime/http/routes/view.js'

function decode(header: string): Record<string, unknown> {
  return JSON.parse(decodeURIComponent(header)) as Record<string, unknown>
}

describe('view route metadata header', () => {
  it('projects the camera pose, projection and dimensions for presence', () => {
    const view = [
      [1, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ]
    const projection = [
      [2, 0, 0, 0],
      [0, 2, 0, 0],
      [0, 0, -1, -1],
      [0, 0, -1, 0],
    ]
    const out = decode(
      viewHeader({
        mode: 'camera',
        width: 512,
        height: 512,
        matrix: { view, proj: projection },
        spec: {
          mode: 'camera',
          from: [4, -4, 3],
          look_at: [0, 0, 0],
          lens: 50,
          ortho: false,
          ortho_scale: 8,
          sensor: 36,
        },
        camera: 'Camera',
        frame: 1,
      }),
    )
    expect(out.viewpoint).toBe('camera')
    expect(out.view_matrix).toEqual(view)
    expect(out.proj_matrix).toEqual(projection)
    expect(out.width).toBe(512)
    expect(out.height).toBe(512)
    expect(out.from).toEqual([4, -4, 3])
    expect(out.look_at).toEqual([0, 0, 0])
    expect(out.lens).toBe(50)
    expect(out.ortho).toBe(false)
    expect(out.ortho_scale).toBe(8)
    expect(out.sensor).toBe(36)
    expect(out.camera).toBe('Camera')
    expect(out.frame).toBe(1)
  })

  it('labels a custom capture as custom while camera keeps its mode', () => {
    const custom = decode(
      viewHeader({
        mode: 'current',
        matrix: {},
        spec: { mode: 'viewport', from: [1, 2, 3], look_at: [0, 0, 0] },
      }),
    )
    expect(custom.viewpoint).toBe('custom')
    const camera = decode(viewHeader({ mode: 'camera', matrix: {}, spec: { mode: 'camera' } }))
    expect(camera.viewpoint).toBe('camera')
  })

  it('keeps the encoded header bounded', () => {
    const long = 'x'.repeat(9000)
    const header = viewHeader({ mode: long, warning: long })
    expect(header.length).toBeLessThanOrEqual(6000 * 3)
    expect(() => decode(header)).not.toThrow()
  })
})
