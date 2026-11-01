export interface WiredState {
  chips: boolean
  toast: boolean
  status: boolean
  mentions: boolean
  commands: boolean
  commandTree: boolean
}

export interface BridgeState {
  lastSeq: number
  lastOk: null | boolean
  lastReason: null | string
  lastAt: null | number
}

/** doctor 用的接线现场：哪些接缝挂上了、选中桥最近一次结果 */
export const wired: WiredState = {
  chips: false,
  toast: false,
  status: false,
  mentions: false,
  commands: false,
  commandTree: false,
}

export const bridge: BridgeState = {
  lastSeq: 0,
  lastOk: null,
  lastReason: null,
  lastAt: null,
}
