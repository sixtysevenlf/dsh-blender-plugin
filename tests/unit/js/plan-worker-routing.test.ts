import { describe, expect, it } from 'vitest'
import { planHeadlessTarget } from '../../../src/plan/headless-target.js'

/**
 * Worker (`session=...`) plan routing must agree with the GUI route table.
 *
 * Regression: the host worker map matched `qc_` before anything else, so
 * `qc_render_views` under a worker session called `K.dsh_qc_api["dispatch"]("render_views")`.
 * The qc API's render_views forwarder depends on K.dsh_qc_render_api being
 * preloaded (or the legacy flat exec fallback); the worker only preloads
 * qc,imgtools, so the call fails even though the GUI path works.
 */
describe('plan worker routing: qc_render_*', () => {
  it('routes qc_render_views to dsh_qc_render_api with the render_views sub-op', () => {
    const target = planHeadlessTarget('qc_render_views')
    expect(target).toEqual({
      api: 'dsh_qc_render_api',
      sub: 'render_views',
      preload: 'qc,qc_render',
    })
  })

  it('keeps the preload able to satisfy the qc_render module', () => {
    const target = planHeadlessTarget('qc_render_views')
    expect(String(target && target.preload)).toContain('qc_render')
  })

  it('routes every qc_render_* op in the catalog to the dedicated API module', () => {
    for (const op of ['qc_render_views', 'qc_render_status', 'qc_render_shot']) {
      const target = planHeadlessTarget(op)
      expect(target, op).toMatchObject({ api: 'dsh_qc_render_api' })
      expect(target && target.sub, op).toBe(op.slice(3))
    }
  })

  it('keeps plain qc_* ops on dsh_qc_api', () => {
    expect(planHeadlessTarget('qc_compare')).toMatchObject({
      api: 'dsh_qc_api',
      sub: 'compare',
      preload: 'qc,imgtools',
    })
    expect(planHeadlessTarget('qc_visual_report')).toMatchObject({
      api: 'dsh_visualreport_api',
      sub: 'report',
    })
  })
})
