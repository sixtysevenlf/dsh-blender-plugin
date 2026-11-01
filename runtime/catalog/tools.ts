// plan 工具参数细节文本（F1.1 从 catalog.ts 拆出；文本依赖 planOpNames 现算 op 数）
import { PLAN_CATALOG, planOpNames } from './data.js'

export const TOOL_DETAIL: Record<string, string> = {
  rt_headless:
    'blender_rt_headless —— 无头 Blender 备用路径（独立进程，不动 GUI 场景；默认仍先走 GUI）\n\n【路径语义（现场踩过，先读这条）】headless 的 cwd 是 \\\\wsl.localhost\\<distro>\\home\\<user>\\DSH；**绝对 WSL 路径必须带开头 / **（漏了会被当相对路径接到 cwd 后面，症状是 ...\\DSH\\home\\<user>\\DSH\\测试\\...；当前版本会自动纠正并写后端 stderr）。script_file 是**内联**执行（拷进 D:\\DSH\\blender\\tmp\\dsh_headless_*.py），当前版本自动注入 DSH_SCRIPT_FILE + __file__（指回原脚本）+ 脚本目录进 sys.path —— 多文件工程不再需要手铺 sys.path。\n\n【结果契约】脚本里 print("HEADLESS " + json.dumps(obj, separators=(",",":")))；必须是**单行 JSON**。\n  解析失败不顶掉输出：记在 resultParseError，stdoutTail/logs 保留原文；>4KB 的结果自动落盘并给 resultPath。\n【超时语义】timeout_ms 只决定服务端子进程跑多久；客户端等待窗口默认 100 s（DSH_HEADLESS_WAIT_MS 可调），\n  到点回 {kind:"promoted", jobId:"run-…"} —— 子进程照跑，用 blender_rt_job(op="wait"|"collect", id=…) 收结果。\n  ⇒ 预期 >100 s 的活**直接 as_job=true**（可配 wait_s 先等几秒），别让外层 deadline 决定结果去向。\n【引擎】默认注入 EEVEE + 光追前导（回执回 engine/gpu）；engine="cycles" 走 OptiX、engine="keep" 保持现状、\n  engine="none" 完全跳过前导与 GPU 探测（纯 numpy/图像类任务省 1–1.5 s）。gpu 只对 cycles 路径有意义。\n【路径】outdir/out_json/file/script_file 都收 WSL 路径（/home/… 内部映射成 \\wsl.localhost\\<distro>\\…），\n  回执给 Windows + WSL 两种真实路径；脚本把 POSIX 路径交给 Windows API 会被体检出来（pathWarnings）。\n【可观测】三处 spawn 注入 PYTHONUNBUFFERED=1（日志运行期就有增量）；脚本里 dsh_stage("building") 打心跳，\n  blender_rt_job(op="status") 回 stage/idleMs/lines/logBytes/pidAlive。\n【多视角一体化】shots=[{name, from, look_at, lens, res, samples}] 一次出 N 张（内置 harness：自动三点光 + 渲染锁 +\n  逐张 md5 + 实测设备）；回执 res.shots 每行带 coverage_estimate，主体占画面 <5% 会给 subject_too_small。\n【回执字段】result / resultJson / status / gpu / logs / lastException / traceback / artifacts / shots / inputFile。\n【脚本内可用】K（持久内核，与 rt_do 同一套）/ K.args / K.win_path / K.wsl_path / K.blend_path / K.out_dir / K.env；\n  预载模块用 preload="audit,qc,qc_render" → K.dsh_audit_api / K.dsh_qc_api …\n【别做】长任务别连发 op=status 轮询（用 job op=wait）；GUI 会话里别连发 render.render()。\n【硬退出（F-14）】done 后默认 os._exit(0) 跳过 Blender 退出拆解（规避 5.2.3 退出期 AV：glTF 导出等场景产物完整、done 已打，却被 exitCode 顶成失败）；hard_exit=false 回旧行为（atexit / bpy.app.handlers / 延迟存档照常执行）。job 路径同语义（as_job=true 走本工具即可传；直连 /job 用 opts.hardExit）。\\n【常见坑】没传 outdir 时产物落在默认工作目录；file= 传了 .py 会被自动当脚本（并给提示）；\n  factory_startup=false 会加载用户 startup（其中本插件会尝试占 9876 端口，通常无害但有报错噪音）。',
  rt_job:
    'blender_rt_job —— 作业层（长活后台化 + 按 runId/jobId 回收）\n\nop=start: 与 headless 同形参（script / script_file / file / outdir / args / env / engine / preload /\n  factory_startup / bootstrap / workdir / out_json / timeout_ms 默认 1 h、上限 24 h）。\nop=wait: 阻塞到完成或超时（默认 120 s / 上限 600 s）—— 一次拿结构化结果，**别连发 status**。\nop=status/collect: stage/stageAt/stageAgeMs/lastOutputAt/idleMs/lines/logBytes/pidAlive；collect 可 tail=N。\nop=kill: 对未知 id 不抛错（回"已结束/不存在"）。op=list: 列作业。\nrun 与 job 同一 id 空间：headless 的 runId（run-…）也能用 status/collect/wait/kill 收。\n日志落 <outdir>/jobs/<id>/（stdout.log 边跑边写）；后端重启后台账仍在磁盘。',
  rt_worker:
    'blender_rt_worker —— 热无头会话（反复迭代免冷启动：省 0.9–1.2 s 启动 + 最多 ~16 s EEVEE 着色器编译）\n\nstart: name（默认 default）/ engine / gpu / preload（注入 K.dsh_*_api）/ warmup（预热 Workbench）；\nexec: name / code / timeout_ms / purge_prefix；see: name / spec（离屏 Workbench 帧）；watch: name / spec（逐帧 hash + ≤6 帧图）；\nreset / status / stop / restart / list。\n串行：一次只处理一个请求，长代码会占住 worker；无窗口（依赖 GUI 上下文的 bpy.ops 可能失败）。\n语义：同一实例共享场景与 K（复用 = 放弃进程隔离），脏了用 restart 换新会话；空闲 TTL 默认 15 min（DSH_WORKER_TTL_MS 可调）。\n改了用户模块必须 purge_prefix（否则 import 命中旧代码）。print("HEADLESS {json}") 仍是结果契约。',
  rt_see:
    'blender_rt_see —— 取一帧视口（约 55–160 ms；比 CLI/MCP 快 20 倍）\n\nmax_size（默认 560，420 更快 / 900 更清晰）；full=true + area=N 走整窗口截图（看 Blender UI 用）；\nfrom/look_at（+ lens / ortho / ortho_scale / view_size / shading / overlays / view_mode）走自定义视角：\n  自建矩阵离屏绘制，不建相机、不改 scene.camera、不动用户视口（约 100 ms）。\ndiagnostics=true 强制跑三项诊断（coverage / scene_bbox / objects_in_frame）；默认只在近空帧自动补跑。\n同画面重复出图默认不重复附图（省视觉 token），要重发传 force=true；回执带 hash 可对拍。',
  rt_loop: `blender_rt_loop —— Blender 侧内环搜索（bpy.app.timers，160 tick/s；迭代/时间双上限 + op=stop 急停）

【什么时候用（看任务形状，不看次数门槛）】对齐 / 拟合 / 反推尺寸 / 参数扫描 —— 判据能写成一个数（轮廓 IoU、包围盒尺寸误差、剖面差，或你自写的 score），候选几十~几千组。
  反面：只试 ≤5 组 → 用 blender_rt_do 自己循环；审美判断 → 别用；无头（blender -b）timers 不触发 → 直接在脚本里写有界 for 循环。

【spec】{setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every, patience}
  setup 只跑一次；step/measure 共享命名空间 ns（预置 bpy / K / math / random / np / i / frac / penalize / anneal / record）；
  measure 必须给 ns["score"]，参数写 ns["params"]，可选 ns["metrics"] / ns["violations"]；
  iterations / budget_ms 是安全阀（至少给一个）；patience>0 = 连续 N 次 measure 没刷新 best 就早停（stop_reason="plateau"）。

【现成目标函数 K.dsh_measure】silhouette_iou(view_spec, ref_path, ref_box) → 1-IoU（会渲染一张再比对；固定对齐，防缩放刷分）；
  aabb_err(obj_name, target_size) → 三轴绝对误差和（不出图，最便宜）；profile_err(view_spec, ref_path, ref_box, bins=24) → 平均剖面差 px。

【可照抄骨架】blender_rt_loop(op="start", spec={
    "setup":   "import bpy\\nob = bpy.data.objects['GEO-x']\\ndef apply(p):\\n    ob.location.y = p['dy_mm']/1000.0\\n    bpy.context.view_layer.update()\\n",
    "step":    "ns['params'] = {'dy_mm': random.uniform(-30, 30)}",
    "measure": "apply(ns['params'])\\nns['score'] = K.dsh_measure['silhouette_iou']({'from':[0,-6,1.0],'look_at':[0,0,0.5]}, 'D:/ref/side.png', None)",
    "iterations": 400, "budget_ms": 60000, "top_k": 10, "patience": 60})
  → op=status（tps / best / 尾迹）· op=board（候选表：哪些参数、各自 score）· op=export（best → 可复现脚本；setup 里定义了 apply(p) 才会自动重放）· op=stop（下一个 tick 退出）。

【纪律】① 内环只优化你写的目标函数 —— 收敛后换另一条通路复核（qc_render_views / audit_measure）+ rt_see 看一眼，别把 board 的 best 当验收；
  ② 单视轮廓 IoU 会被"填满轮廓"刷高 → 必须再配间隙 / 尺寸 / 特征线证据；
  ③ 跑完场景停在**最后一次迭代**的参数上，不是 best —— 要 best 就用 op=export 的 apply(BEST) 贴回去；
  ④ step / measure 里改完 transform 必须 bpy.context.view_layer.update()，否则量到的是上一轮几何。`,
  viewport:
    'blender_viewport —— 通道运维 + 写租约 + 工具参数细节\n\nstatus: 后端健康/视口区域/计数/版本；doctor: 真跑一次 bpy 往返的三级体检（未连 / 主线程忙 / addon 线程卡死 + 修法）；\nwho / lease / release: 写租约（holder / ttl_ms / force；被别人持有时写路由 409，只读 op 豁免）；\nstart / stop / restart: 后端进程与 15 s 看护；\nlaunch: 一键拉起 GUI Blender 并自动 Connect addon（wait_ms 默认 90000 / file / exe / addon_module / addon_file / dry_run）；\n  唯一可信判据是"addon 端口开了"，不靠进程活着；幂等（已在监听就回 already=true）。\nhelp: args={tool:"rt_headless"} 取该工具的完整参数细节；args 省略 → 返回有细节的工具索引。',
  rt_plan:
    'blender_rt_plan —— 判定 / 验收 / 导出 / 造型的唯一入口（' +
    PLAN_CATALOG.length +
    ' family / ' +
    planOpNames().length +
    ' op；数字由 PLAN_CATALOG 现算，不再手写以免过期）\n\n' +
    '不确定用哪个 op → op="catalog"（本地直出：什么时候用 / 别用 / 最小骨架）。\n' +
    '单族展开：op="catalog", args={family:"audit"}；算子细节：op="audit_help" / "sculpt_help" / "qc_help" …\n' +
    '工具参数细节（重工具长尾）：op="catalog", args={tool:"rt_plan"}。\n' +
    '常用：audit_scene|audit_mesh|audit_gate|audit_interference · qc_render_views|qc_compare ·\n' +
    '  sculpt_scan→setup→apply · fix_repair|fix_decimate · uv_smart_project|uv_unwrap|uv_pack ·\n' +
    '  print_report · sweep_analyze→build · deliver_export|verify · motion_joints|measure|export_urdf ·\n' +
    '  generator_save|run（save 用 **code=**；run 的生成器 PARAMS 走嵌套 **args=**，不会被摊平）·\n' +
    '  gui_frame|shading|open · render_lock|render_status · montage ·\n' +
    '  契约层 status|register_*|check_*|destructive_guard|evidence|ledger|report|verify|flip|advance。\n' +
    '【目录自证】目录回执带 provenance：loadedHash=本进程加载 engine.mjs 时的**内容指纹**（sha256:12）、\n' +
    '  currentHash=现场再读磁盘的指纹、verdict=current|stale|unknown。**不看 mtime**（touch 会假 stale、同秒重写会假 fresh）。\n' +
    '  stale=true → 磁盘已改、本进程还是旧一代：重启后端再问一次；强对拍用 op="catalog", args={verify:true}\n' +
    '  （另起干净 node 进程 import 磁盘 engine.mjs，用它的 family/op 数与 catalogHash 当裁判 —— 实测 ~50 ms）。\n' +
    '写 op 过写租约；只读 op（catalog / audit_* / print_* / qc_* / sculpt_scan / uv_stats / sweep_analyze …）豁免；\n' +
    '每次顶层调用另写一行轨迹 JSONL（DSH_TRAJ=0 关，DSH_TRAJ_FULL=1 记更多参数）。',
}
