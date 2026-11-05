// 轨迹 JSONL（拆分入口）
import path from 'node:path'
import { winToWsl } from '../../config.js'
import { WIN_TMP } from '../../paths.js'
import type { TrajWriteDeps } from './record.js'
import { createTrajectoryWrapper } from './read.js'

export function createTrajectory(_E: unknown) {
  // deps: none
  const TRAJ_DIR = path.join(winToWsl(WIN_TMP), 'trajectory')
  const TRAJ_OFF = process.env.DSH_TRAJ === '0' || process.env.DSH_TRAJ === 'false'
  const TRAJ_FULL = process.env.DSH_TRAJ_FULL === '1'
  const TRAJ_MAX_BYTES = Number(process.env.DSH_TRAJ_MAX_BYTES || 16000000)
  const deps: TrajWriteDeps = { TRAJ_OFF, TRAJ_DIR, TRAJ_FULL, TRAJ_MAX_BYTES }
  return {
    withTrajectory: createTrajectoryWrapper(deps),
    TRAJ_DIR,
    TRAJ_OFF,
    TRAJ_FULL,
  }
}
