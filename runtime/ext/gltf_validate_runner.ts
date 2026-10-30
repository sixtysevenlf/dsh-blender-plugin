import { createRequire } from 'node:module';
import fs from 'node:fs';
import { errorMessage } from "../core/json.js";
const require = createRequire(import.meta.url);
const file = process.argv[2];
if (!file) { console.log(JSON.stringify({ ok: false, error: 'usage: node gltf_validate_runner.mjs <file.glb|gltf>' })); process.exit(0); }
try {
  const validator = require('./node_modules/gltf-validator');
  const bytes = new Uint8Array(fs.readFileSync(file));
  validator.validateBytes(bytes, { maxIssues: 200 }).then((rep: unknown) => {
    console.log('RAW ' + JSON.stringify(rep));
  }).catch((e: unknown) => {
    console.log('RAW ' + JSON.stringify({ ok: false, error: errorMessage(e).slice(0, 300) }));
  });
} catch (e) {
  console.log('RAW ' + JSON.stringify({ ok: false, error: 'validator 加载失败: ' + errorMessage(e).slice(0, 200) }));
}