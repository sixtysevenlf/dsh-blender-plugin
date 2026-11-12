import { afterEach, describe, expect, it } from 'vitest'
import path from 'node:path'
import { defaultQueueFile, migrateLegacyQueueState } from '../../../runtime/core/queue/store.js'
import { getHome } from '../../../runtime/home.js'

const OLD = process.env.DSH_QUEUE_STATE

afterEach(() => {
  if (OLD === undefined) delete process.env.DSH_QUEUE_STATE
  else process.env.DSH_QUEUE_STATE = OLD
})

describe('queue state root (v3.2)', () => {
  it('defaults under home/queue and honours DSH_QUEUE_STATE', () => {
    delete process.env.DSH_QUEUE_STATE
    const file = defaultQueueFile()
    expect(file).toBe(path.join(getHome(), 'queue', 'queue.json'))

    process.env.DSH_QUEUE_STATE = path.join('C:', 'tmp', 'queue.json')
    expect(defaultQueueFile()).toBe(process.env.DSH_QUEUE_STATE)
  })

  it('never overwrites an existing home queue during migration', () => {
    delete process.env.DSH_QUEUE_STATE
    const target = defaultQueueFile()
    // The home queue may or may not exist yet; migration must be a no-op then.
    expect(migrateLegacyQueueState(target)).toEqual([])
  })
})
