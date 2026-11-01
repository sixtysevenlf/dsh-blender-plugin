import type { Json } from './http.js'

export type { Json }

export interface TuiHostOptions {
  port: number
  /** 本插件的写租约 holder（页脚区分「本会话持有 / 别的会话持有」） */
  holder: string
}

export interface MentionItem {
  label: string
  detail: string
  insertText: string
}

export interface ChipService {
  attachContext(input: { key: string; title: string; content: string }): unknown
}

export interface ToastService {
  show(text: string, opts: { color: string }): void
}

export interface StatusService {
  setField(key: string, value: string, opts: Record<string, unknown>): unknown
}

export interface MentionsService {
  register(def: {
    id: string
    title: string
    complete: (query: string, signal: AbortSignal) => Promise<MentionItem[]>
  }): unknown
}

export interface CommandInvocation {
  rawInput?: string
  signal?: AbortSignal
}

export interface CommandsService {
  register(def: {
    name: string
    description: string
    input: { hint: string }
    handler: (inv: CommandInvocation) => Promise<{ kind: string; text: string }>
  }): unknown
}

export interface CommandTreeService {
  register(def: {
    root: string
    children: (path: readonly string[]) => readonly { name: string; description: string }[]
  }): unknown
}

export interface TuiServices {
  tuiToast?: ToastService
  tuiContextChips?: ChipService
  tuiStatus?: StatusService
  tuiMentions?: MentionsService
  commands?: CommandsService
  tuiCommandTrees?: CommandTreeService
  effect(fn: () => void | (() => void), label: string): void
}

export interface TuiContext {
  inject(names: string[], fn: (c: TuiServices) => void): void
}

export interface SelectionState {
  lastSeq: number
  fails: number
}
