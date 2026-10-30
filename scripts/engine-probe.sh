#!/usr/bin/env bash
# v0.9.1（rifle-build《93 反馈》）引擎侧回归探针 —— 不需要 DSH、不需要工具层，直接打后端 HTTP。
#
# 覆盖：
#   B4  env 注入（DSH_ARGS/DSH_OUTDIR/DSH_RUN_ID → 子进程 os.environ + K.args/K.run_id/K.env）
#   A3  scriptFile= 收 .py；file= 传 .py 自动识别；outJson= 落盘；大结果自动落 results/（resultPath）
#   A4  status 分档（finished / script_error）—— Blender 脚本抛异常时退出码仍是 0，别只看 exit
#   B3  args 数组直传（旧版 String([...]) 会变成 "[delta]"）
#   D1  gui_help / gui_frame 参数绑定（旧工具层曾静默丢 args）
#   E1  render_lock 状态只读可用
#   D2  /view 的 x-dsh-view 头带 coverage_estimate / scene_bbox / warning（非 ASCII 需 URL 编码）
#
# 用法：bash tests/engine_probe.sh [port]
set -u
PORT="${1:-9877}"
B="http://127.0.0.1:${PORT}"
HOLD="probe-$$"
RELEASE_HOLD=1
PASS=0; FAIL=0
say() { printf '%-58s %s\n' "$1" "$2"; }
ok()  { PASS=$((PASS+1)); say "$1" "ok"; }
bad() { FAIL=$((FAIL+1)); say "$1" "FAIL  $2"; }

last_json() { python3 -c '
import json,sys
raw=sys.stdin.read()
lines=[l for l in raw.strip().split(chr(10)) if l.strip() and "heartbeat" not in l]
d=json.loads(lines[-1])
print(json.dumps(d, ensure_ascii=False))
'; }
jget() { python3 -c 'import json,sys; d=json.loads(sys.argv[1]); cur=d
for k in sys.argv[2].split("."):
    cur = (cur or {}).get(k) if isinstance(cur, dict) else None
print("" if cur is None else (json.dumps(cur, ensure_ascii=False) if isinstance(cur,(dict,list)) else cur))' "$1" "$2"; }

TMP=".tmp-probe-$$"; mkdir -p "$TMP"
cat > "$TMP/probe_script.py" <<'PY'
import json, os
print("HEADLESS " + json.dumps({"args": K.args, "run_id": K.run_id,
                                 "env_has": sorted([k for k in os.environ if k.startswith("DSH_")]),
                                 "big": "x" * 5000}))
PY

# ① scriptFile + env + args 数组 + outJson
R=$(curl -s -m 90 -X POST "$B/headless" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys
print(json.dumps({"engine":"none","force":True,"holder":sys.argv[1],"timeoutMs":90000,
  "scriptFile":sys.argv[2],"args":["delta","echo"],"env":{"DSH_PROBE":"1"},
  "outJson":sys.argv[3]}))' "$HOLD" "$PWD/$TMP/probe_script.py" 'D:\DSH\blender\tmp\engine_probe_out.json')" | last_json)
J=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(json.dumps(d["result"], ensure_ascii=False))' "$R")
[[ "$(jget "$J" status)" == "finished" ]] && ok "① status=finished" || bad "① status" "$(jget "$J" status)"
[[ "$(jget "$J" scriptFile.bytes)" != "" ]] && ok "② scriptFile 被接受（标了字节数）" || bad "② scriptFile" "$(jget "$J" scriptFile)"
[[ "$(jget "$J" outJson)" == *engine_probe_out.json ]] && ok "③ outJson 落盘路径已回报" || bad "③ outJson" "$(jget "$J" outJson)"
[[ "$(jget "$J" resultPath)" == *engine_probe_out.json ]] && ok "④ resultPath 指向 outJson" || bad "④ resultPath" "$(jget "$J" resultPath)"
ARGS=$(jget "$J" result.args)
[[ "$ARGS" == '["delta", "echo"]' ]] && ok "⑤ args 数组直传（$ARGS）" || bad "⑤ args" "$ARGS"
[[ "$(jget "$J" result.run_id)" != "" ]] && ok "⑥ K.run_id 可见" || bad "⑥ K.run_id" ""
ENVH=$(jget "$J" result.env_has)
[[ "$ENVH" == *DSH_ARGS* ]] && ok "⑦ 子进程 os.environ 里能看到 DSH_*（WSL 跨界已打通）" || bad "⑦ env" "$ENVH"
CK=$(jget "$J" childEnvKeys)
[[ "$CK" == *DSH_PROBE* ]] && ok "⑧ 自定义 env 也注入了" || bad "⑧ env 自定义" "$CK"

# ② file= 传 .py → 自动识别
R2=$(curl -s -m 90 -X POST "$B/headless" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys
print(json.dumps({"engine":"none","force":True,"holder":sys.argv[1],"timeoutMs":90000,"file":sys.argv[2]}))' "$HOLD" "$PWD/$TMP/probe_script.py")" | last_json)
[[ "$(jget "$R2" result.scriptFile.auto_from_file)" == "True" ]] && ok "⑨ file= 传 .py 自动当脚本（不再 File format is not supported）" || bad "⑨ file=.py" "$(jget "$R2" result.scriptFile)"

# ③ 脚本抛异常 → status=script_error（退出码仍是 0）
R3=$(curl -s -m 90 -X POST "$B/headless" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys; print(json.dumps({"engine":"none","force":True,"holder":sys.argv[1],"timeoutMs":60000,"script":"raise ValueError(\"boom\")"}))' "$HOLD")" | last_json)
[[ "$(jget "$R3" result.status)" == "script_error" ]] && ok "⑩ 脚本异常 → status=script_error（注意 exitCode 仍是 0）" || bad "⑩ status" "$(jget "$R3" result.status)"
[[ "$(jget "$R3" result.failure_hint)" != "" ]] && ok "⑪ failure_hint 给出可执行提示" || bad "⑪ hint" ""

# ④ GUI 原语参数绑定（旧工具层静默丢 args 的回归）
R4=$(curl -s -m 60 -X POST "$B/plan" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys; print(json.dumps({"op":"gui_shading","args":{"mode":"WIREFRAME"},"force":True,"holder":sys.argv[1]}))' "$HOLD")" | last_json)
[[ "$(jget "$R4" result.shading)" == "WIREFRAME" ]] && ok "⑫ gui_shading 参数绑定（kwargs 路由）" || bad "⑫ gui_shading" "$(jget "$R4" result)"
curl -s -m 60 -X POST "$B/plan" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys; print(json.dumps({"op":"gui_shading","args":{"mode":"SOLID"},"force":True,"holder":sys.argv[1]}))' "$HOLD")" >/dev/null   # 还原视口着色
R5=$(curl -s -m 60 -X POST "$B/plan" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys; print(json.dumps({"op":"gui_help","force":True,"holder":sys.argv[1]}))' "$HOLD")" | last_json)
[[ "$(jget "$R5" result.version)" != "" ]] && ok "⑬ gui_help 可用（GUI 原语已注册）" || bad "⑬ gui_help" ""

# ⑤ 渲染锁状态（E1）
R6=$(curl -s -m 60 -X POST "$B/plan" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys; print(json.dumps({"op":"render_lock","args":{"action":"status"},"force":True,"holder":sys.argv[1]}))' "$HOLD")" | last_json)
[[ "$(jget "$R6" result.ok)" == "True" ]] && ok "⑭ render_lock status 可用（E1）" || bad "⑭ render_lock" "$(jget "$R6" result)"

# ⑥ /view 头里的自诊断字段（D2；头值 URL 编码）
R7=$(curl -s -m 60 -D "$TMP/hdr.txt" -o /dev/null -X POST "$B/view" -H 'content-type: application/json' \
  -d '{"from":[10,10,10],"look_at":[20,20,20],"width":160,"height":90,"mode":"viewport"}')
HDR=$(grep -i '^x-dsh-view:' "$TMP/hdr.txt" | head -1 | sed 's/^[Xx]-[Dd]sh-[Vv]iew: *//' | tr -d '\r')
DEC=$(python3 -c 'import sys,urllib.parse; h=sys.argv[1]
print(urllib.parse.unquote(h) if h.startswith("%7B") else h)' "$HDR")
[[ "$DEC" == *coverage_estimate* ]] && ok "⑮ /view 头带 coverage_estimate（D2 自诊断）" || bad "⑮ view 头" "${DEC:0:80}"
[[ "$DEC" == *frame_looks_empty* || "$DEC" == *'"warning": null'* ]] && ok "⑯ 瞄空时给 frame_looks_empty（或明确 null）" || bad "⑯ warning" "${DEC:0:120}"

# 归还写通道租约（探针用 force 抢的，别留给别人的会话）
curl -s -m 20 -X POST "$B/release" -H 'content-type: application/json' -d "$(python3 -c '
import json,sys; print(json.dumps({"holder":sys.argv[1]}))' "$HOLD")" >/dev/null 2>&1 || true
rm -rf "$TMP"
echo "----"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]]
