/**
 * tests 严格类型检查的边界声明（T5）。
 *
 * runtime/*.mjs 是纯 JS 运行时（由 tests 直接 import），lib/*.js 是构建产物；
 * 两者在测试里按黑盒使用。这里把它们声明为 any，让 tsconfig.test.json 能对
 * tests 下的 TS 文件做严格检查（语法、导入、类型错误），而不要求给整个 JS 运行时补类型。
 *
 * 注意：只匹配带显式 .js / .mjs 扩展名的说明符；npm 包仍走各自 package.json 的类型入口。
 */
declare module '*.mjs'
declare module '*.js'
