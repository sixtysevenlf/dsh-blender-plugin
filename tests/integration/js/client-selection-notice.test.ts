import fs from 'node:fs'
import vm from 'node:vm'
import { beforeAll, describe, expect, it } from 'vitest'
import { pkgPath } from '../../helpers/pkg'

// v2.3 Blender native selection -> DSH draft, Web half: what the bridge is allowed to notify.
// DSH renders notify("info") as a permanent banner in the input bar (no clear path, survives send),
// so a success notice there becomes a sticky "已接收 Blender 选中" strip. Success must be silent
// (the draft text is the feedback) and failures must go through notify("error") (a closable toast).

function loadClientSelftest(): any {
  const code = fs.readFileSync(pkgPath('src', 'client', 'index.js'), 'utf8')
  let entry: any = null
  const sandbox: any = {
    window: {
      __ModuleLoader__: {
        load(definition: any) {
          entry = definition
        },
      },
    },
    console,
  }
  vm.runInNewContext(code, sandbox)
  expect(entry && entry.id).toBe('dsh-blender-plugin')
  const reactStub = new Proxy({}, { get: () => () => null })
  return entry.factory((id: string) => {
    if (id === 'react') return reactStub
    throw new Error('unexpected require ' + id)
  }).__selftest
}

const PAYLOAD = { selection: { kind: 'objects', count: 1, names: ['Cube'], active: 'Cube' } }

let selftest: any

beforeAll(() => {
  selftest = loadClientSelftest()
})

describe('selection draft insertion stays silent on success', () => {
  it('never notifies when inputActions.insertText accepts the text', async () => {
    const notices: any[] = []
    const inserted: string[] = []
    const out = await selftest.applySelectionToDraft(
      {
        sessionId: 'session-1',
        inputActions: {
          captureInsertion: () => ({ token: 1 }),
          insertText: (text: string) => {
            inserted.push(text)
            return true
          },
        },
        fallbackInput: { notify: (...args: any[]) => notices.push(args) },
      },
      PAYLOAD,
    )
    expect(out).toMatchObject({ ok: true, how: 'actions' })
    expect(inserted[0]).toContain('Blender 选中：Cube')
    expect(notices).toEqual([])
  })

  it('never notifies when the empty-draft fallback channel writes', async () => {
    const notices: any[] = []
    const drafts: string[] = []
    const out = await selftest.applySelectionToDraft(
      {
        sessionId: 'session-1',
        fallbackInput: {
          state: { getSnapshot: () => ({ draft: '' }) },
          setDraft: (text: string) => drafts.push(text),
          notify: (...args: any[]) => notices.push(args),
        },
      },
      PAYLOAD,
    )
    expect(out).toMatchObject({ ok: true, how: 'fallback' })
    expect(drafts[0]).toContain('Blender 选中：Cube')
    expect(notices).toEqual([])
  })

  it('reports failure as a reason and leaves the notice to the caller', async () => {
    const notices: any[] = []
    const out = await selftest.applySelectionToDraft(
      {
        sessionId: 'session-1',
        inputActions: {
          captureInsertion: () => ({ token: 1 }),
          insertText: () => false,
        },
        fallbackInput: { notify: (...args: any[]) => notices.push(args) },
      },
      PAYLOAD,
    )
    expect(out.ok).toBe(false)
    expect(out.reason).toContain('文本插入被拒')
    expect(notices).toEqual([])
  })
})

describe('failure notices use the closable error toast', () => {
  it('notifies with level "error" and the given text', () => {
    const notices: any[] = []
    selftest.notifyError(
      { fallbackInput: { notify: (...args: any[]) => notices.push(args) } },
      'Blender 选中未写入草稿：bridge_error',
    )
    expect(notices).toEqual([['error', 'Blender 选中未写入草稿：bridge_error']])
  })

  it('is a no-op when the host input has no notify()', () => {
    expect(() => selftest.notifyError({}, 'x')).not.toThrow()
    expect(() => selftest.notifyError(null, 'x')).not.toThrow()
    expect(() =>
      selftest.notifyError(
        {
          fallbackInput: {
            notify: () => {
              throw new Error('host rejected')
            },
          },
        },
        'x',
      ),
    ).not.toThrow()
  })
})

describe('the sticky info banner cannot come back', () => {
  const source = fs.readFileSync(pkgPath('src', 'client', 'index.js'), 'utf8')

  it('no longer carries the success notice text', () => {
    expect(source).not.toContain('已加入草稿，未发送')
  })

  it('never calls notify("info", …)', () => {
    expect(source).not.toMatch(/notify\(\s*['"]info['"]/)
  })
})
