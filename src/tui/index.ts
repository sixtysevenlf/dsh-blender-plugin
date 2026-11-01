/**
 * dsh-TUI 宿主半边 facade。
 *
 * 具体实现按接缝拆在 ./tui/ 下：http、state、selection、footer、mentions、commands、register/*。
 * 这个文件只保留原来的导入名（原 src/tui-host.ts 已删除），插件入口和测试改从这里导入。
 */
export type { TuiHostOptions, MentionItem } from './types.js'
export {
  subSelectionText,
  selectionMessageText,
  selectionChipTitle,
  selectionTick,
} from './selection.js'
export { footerField } from './footer.js'
export { mentionItems, filterMentionItems, createSceneMentionSource } from './mentions.js'
export { blenderCommand } from './commands.js'
export { registerTuiHost, tuiHostSummary } from './register/index.js'

import { SUBCOMMANDS } from './commands.js'
import { bridge, wired } from './state.js'

export const __tuiHostInternals = { wired: wired, bridge: bridge, SUBCOMMANDS: SUBCOMMANDS }
