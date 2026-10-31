/**
 * dsh-TUI 宿主半边 facade。
 *
 * 具体实现按接缝拆在 ./tui/ 下：http、state、selection、footer、mentions、commands、register。
 * 这个文件只保留原来的导入名，插件入口和测试无需改 import。
 */
export type { TuiHostOptions, MentionItem } from './tui/types.js'
export {
  subSelectionText,
  selectionMessageText,
  selectionChipTitle,
  selectionTick,
} from './tui/selection.js'
export { footerField } from './tui/footer.js'
export { mentionItems, filterMentionItems, createSceneMentionSource } from './tui/mentions.js'
export { blenderCommand } from './tui/commands.js'
export { registerTuiHost, tuiHostSummary } from './tui/register.js'

import { SUBCOMMANDS } from './tui/commands.js'
import { bridge, wired } from './tui/state.js'

export const __tuiHostInternals = { wired: wired, bridge: bridge, SUBCOMMANDS: SUBCOMMANDS }
