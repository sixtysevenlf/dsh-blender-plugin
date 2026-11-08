import { asRecord } from '../contracts/dynamic.js'

/** '/host/state' 响应 → 页脚一格（文本 / tone / 悬停说明）。纯函数，导出给自检。 */
export function footerField(
  state: unknown,
  holder: string,
  port: number,
): { text: string; tone: string; detail: string } {
  const st = asRecord(state)
  if (st.ok !== true) {
    return {
      text: 'Blender ○',
      tone: 'dim',
      detail:
        '后端未运行（127.0.0.1:' +
        String(port) +
        '）· 让模型跑 blender_viewport(op="start")，或 /blender doctor',
    }
  }
  const ch = asRecord(st.channel)
  const lease = asRecord(st.lease)
  const jobs = Number(asRecord(st.jobs).running || 0)
  const jobsTail = jobs > 0 ? ' · 作业 ' + String(jobs) : ''
  const jobsDetail = jobs > 0 ? ' · ' + String(jobs) + ' 个 headless 作业在跑（/blender jobs）' : ''
  if (ch.aiPaused) {
    return {
      text: 'Blender ⏸ AI 暂停',
      tone: 'warning',
      detail: 'Blender 里暂停了 AI 操作：N 面板「活动状态」点「继续 AI 操作」' + jobsDetail,
    }
  }
  const render = asRecord(st.render)
  if (render.busy) {
    const s = Math.round(Number(render.ageMs || 0) / 1000)
    return {
      text: 'Blender ● 渲染中' + jobsTail,
      tone: 'warning',
      detail: 'Blender 正在渲染（' + String(s) + ' s）· 主线程命令会秒回 busy' + jobsDetail,
    }
  }
  if (ch.diagnosis === 'main-thread-busy') {
    return {
      text: 'Blender ● 忙' + jobsTail,
      tone: 'warning',
      detail: 'Blender 主线程被渲染或模态操作占住 · 等它空下来，或改走无头' + jobsDetail,
    }
  }
  if (
    ch.lastOkAgeMs == null ||
    ch.diagnosis === 'blender-unreachable' ||
    ch.diagnosis === 'addon-thread-stuck'
  ) {
    return {
      text: 'Blender ○ 未连接' + jobsTail,
      tone: 'dim',
      detail:
        'GUI Blender 还没连上（addon 没 Connect 或 Blender 没起）· Blender 里按 N → Connect；无头路线不受影响' +
        jobsDetail,
    }
  }
  if (lease.active && lease.holder && lease.holder !== holder) {
    return {
      text: 'Blender ● 他人租约' + jobsTail,
      tone: 'warning',
      detail:
        '写租约在别的会话（' + String(lease.holder) + '）· 写操作会 409；只读不受影响' + jobsDetail,
    }
  }
  const mine = lease.active && lease.holder === holder ? ' · 本会话持有写租约' : ''
  return {
    text: 'Blender ●' + jobsTail,
    tone: 'success',
    detail: 'GUI 通道已连上' + mine + jobsDetail,
  }
}
