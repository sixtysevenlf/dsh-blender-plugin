#!/bin/bash
# Build: host tsc (src → lib) → runtime tsc (runtime → lib/runtime) → client bundle.
# 任一步失败即退出（set -e）。DSH_CHECKOUT 可选：只用于把依赖链接到 dsh 源码树；
# 没有 checkout 时直接用本包 node_modules 里的 typescript 与依赖（npm ci 后即可构建）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CHECKOUT="${DSH_CHECKOUT:-}"
if [ -z "$CHECKOUT" ]; then
  for candidate in "$HOME/dsh-harness" "$HOME/dsh" "$HOME/.dsh/dsh-harness"; do
    if [ -d "$candidate/packages" ]; then CHECKOUT="$candidate"; break; fi
  done
fi

link_pkg() {
  local target="$CHECKOUT/$2"
  if [ ! -e "$target" ]; then
    echo "build: dependency target missing: $target" >&2
    exit 1
  fi
  node -e "
    const fs = require('fs');
    const path = require('path');
    const link = path.resolve(process.argv[1]);
    const target = path.resolve(process.argv[2]);
    fs.rmSync(link, { recursive: true, force: true });
    fs.mkdirSync(path.dirname(link), { recursive: true });
    fs.symlinkSync(target, link, process.platform === 'win32' ? 'junction' : 'dir');
  " "node_modules/$1" "$target"
}

if [ -n "$CHECKOUT" ] && [ -d "$CHECKOUT/packages" ]; then
  export DSH_CHECKOUT="$CHECKOUT"
  echo "=== Linking build dependencies (checkout: $CHECKOUT) ==="
  mkdir -p node_modules/@deepseek-ai
  node -e "const fs=require('fs');fs.rmSync('node_modules/@standard-schema',{recursive:true,force:true})"
  link_pkg cordis vendor/cordis
  link_pkg cosmokit vendor/cosmokit
  link_pkg schemastery vendor/schemastery
  link_pkg @deepseek-ai/dsh-tools packages/core/tools
  link_pkg @deepseek-ai/dsh-llm packages/llm/llm
  link_pkg @deepseek-ai/dsh-system-prompt packages/core/system-prompt
  link_pkg @types/node node_modules/@types/node
  STD_SCHEMA=$(find "$CHECKOUT/node_modules/.pnpm" -maxdepth 1 -type d -iname '@standard-schema+spec@*' 2>/dev/null | head -1)
  if [ -n "$STD_SCHEMA" ]; then
    node -e "
      const fs = require('fs');
      const path = require('path');
      fs.rmSync('node_modules/@standard-schema', { recursive: true, force: true });
      fs.mkdirSync('node_modules/@standard-schema', { recursive: true });
      fs.symlinkSync(path.resolve(process.argv[1]), path.resolve('node_modules/@standard-schema/spec'), process.platform === 'win32' ? 'junction' : 'dir');
    " "$STD_SCHEMA/node_modules/@standard-schema/spec"
  fi
else
  echo "build: 未找到含 packages/ 的 dsh checkout —— 跳过依赖链接，使用本包 node_modules（npm ci 产物）"
fi

# 顺序：runtime → host → client。src/host 与 src/tools 的边界 import 指向 lib/runtime/*.js，
# host tsc 需要先有 lib/runtime/*.d.ts（生产布局真实相对路径），所以 runtime 先编译。
echo "=== Compiling runtime → lib/runtime ==="
node "$ROOT/scripts/build.mjs" runtime

echo "=== Compiling src → lib (host) ==="
node "$ROOT/scripts/build.mjs" host

# 客户端半边：src/client/index.js（手写 bundle，无打包器）→ lib/client.js
# 由 package.json 的 dsh.client + exports["./client"] 暴露给 DSH 宿主。
# 脚本缺失时优雅跳过（便于只改 host 半边的场景），但**存在就必须成功**。
echo "=== Building client half (src/client → lib/client.js) ==="
if [ -f "$ROOT/scripts/build-client.mjs" ]; then
  node "$ROOT/scripts/build-client.mjs"
else
  echo "build: scripts/build-client.mjs 不存在 —— 跳过客户端半边（lib/client.js 不会更新）" >&2
fi
echo "=== Build complete ==="
