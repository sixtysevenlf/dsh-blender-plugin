// 模型工具参数细节文本（3.1：恰好 6 个工具）
import { PLAN_CATALOG, planOpNames } from './data.js'

const LF = String.fromCharCode(10)

export const TOOL_DETAIL: Record<string, string> = {
  session:
    'blender_session —— GUI 会话 / 归属 / 写权限 / 事务快照' +
    LF +
    '' +
    LF +
    '动作：list | status | doctor | attach | claim | detach | release | grant | revoke | grants |' +
    LF +
    '  pause | resume | mark_read | presence | heartbeat | start | stop | txn | help' +
    LF +
    'attach：用户已打开 Blender 后在 N 面板点 Connect；多实例必须给 instance_id（status 里能看到）。' +
    LF +
    'start/stop 只作用 virtual-gui；第一次 start 返回 consent_required，等用户授权后由宿主带一次性 token 重试。' +
    LF +
    '写到别的 agent 的场景区域需要 grant scope：exclusive | collection:名 | prefix:名 | object:名；' +
    LF +
    'pause/resume 只有人类能操作（agent 调用返回 human_action_required）；mark_read 清 human_modified。' +
    LF +
    'txn 子面：op=snapshot|restore|list|prune|mark|revert|marks|drop|help；restore/mark/revert 属写操作，' +
    LF +
    '  需要 exclusive grant 且人类未暂停。Gateway 进程生命周期归宿主，模型面不含 launch/lease 运维。',
  op:
    'blender_op —— catalog 算子（plan 主体）与搜索/精简/配方子面' +
    LF +
    '' +
    LF +
    'action=plan（默认）：op=<catalog 算子> args={...}；不确定先 op="catalog"（本地直出，不需要会话）。' +
    LF +
    '  执行型 op 需要 ready 的 GUI 会话；catalog / 只读 op / args.dry_run=true 不需要会话，也不会启动 Blender。' +
    LF +
    '  单族展开：op="catalog" args={family:"audit"}；算子细节：op="audit_help" 等。' +
    LF +
    'action=loop：op=start|status|stop|board|export|help|bench；target=gui（默认）|session（内部无头 worker）。' +
    LF +
    'action=opt：op=analyze|join|help（对象精简/合并）。action=preset：op=list|get|apply|save|delete|export|import|help。' +
    LF +
    '本目录 ' +
    String(PLAN_CATALOG.length) +
    ' family / ' +
    String(planOpNames().length) +
    ' op（数字现算，不手写）。' +
    LF +
    '写类 op 需要对应 write scope 的 grant；写操作在人类暂停时被拒绝。',
  py:
    'blender_py —— 在 GUI 会话里执行 agent 写的 Python（/act 通道）' +
    LF +
    '' +
    LF +
    'code 或 file 二选一；固定 exclusive（不能申请更窄 scope）；没有会话直接拒绝，不会启动 headless。' +
    LF +
    '脚本可以重跑；超过 ~1s 的重活改用 blender_run(kind=headless)。占用中返回 py_busy；' +
    LF +
    'wait_ms 等待其它 py 释放（0–5000ms，默认 0）。' +
    LF +
    'see=true（默认）跑完抓当前视口一帧；同画面默认不重复附图（force=true 重发）。',
  see:
    'blender_see —— 只服务 GUI 的视点捕获' +
    LF +
    '' +
    LF +
    'viewpoint=viewport（默认，当前 GUI 视口）| camera（当前帧相机）| custom（必须 from+look_at）。' +
    LF +
    'mode=frame（默认单帧）| sequence（固定机位序列帧，只 viewport/custom；seconds 0.2–20、fps 0.5–8、帧数 ≤32）。' +
    LF +
    '固定档位 aspect=1:1（544/768/1024/1280）、4:3（640/960/1280）、16:9（1024/1152/1280），默认 16:9+1024；' +
    LF +
    '  只给 aspect 取该比例最小档；非法 max_size 直接 invalid_params（不夹取）；view_size 已删除。' +
    LF +
    'camera 只 frame：用当前帧 evaluated 相机（object matrix/约束/驱动器/lens/sensor/shift/ortho），不改 scene.camera、' +
    LF +
    '  帧、选择、用户视口；输出渲染比例；PANO 不支持（用 blender_run render）、DOF/运动模糊只 preview_only。' +
    LF +
    'full=true 仅 frame+viewport：整窗口/区域截图，固定 1024 长边。同画面默认不重复附图；回执带 hash 与覆盖估计。',
  run:
    'blender_run —— 唯一作业入口（有界队列：pending 64 / GPU 1 / CPU 2）' +
    LF +
    '' +
    LF +
    'action=submit 立即返回 jobId（没有同步模式）：kind=render 声明式最终渲染（相机/帧区间/分辨率/采样/引擎/输出；' +
    LF +
    '  不接受 script；frames 给区间自动 -a，支持相机动画）；kind=headless 用 fresh blender -b 跑脚本' +
    LF +
    '  （script / script_file / file / outdir / timeout_ms / engine / preload / factory_startup / bootstrap / workdir / args / env）。' +
    LF +
    'action=status|wait|collect|cancel id=<jobId>；wait 默认 120s、上限 600s，超时不取消作业；collect 取日志与产物；' +
    LF +
    '  cancel 幂等并清进程树。action=list [state/kind/owner]；action=perf 看队列容量与槽位。' +
    LF +
    'action=tune：旧 Cycles 渲染分析 op=status|analyze|apply|revert|help（/perf 通道）。' +
    LF +
    '队列满返回 QUEUE_FULL；Gateway 重启后 running 标 failed(gateway_restart)，不会自动重放。',
  inspect:
    'blender_inspect —— 只读命令目录与执行' +
    LF +
    '' +
    LF +
    'command 省略=help：只列可执行的只读命令（name(args) / enabled / pause_safe / description）。' +
    LF +
    'command=<name> args={...}：执行一条只读 addon 命令（如 get_scene_info / get_selection / describe_node_type）。' +
    LF +
    '内部传输命令（execute_code / get_viewport_screenshot / send_*）不列出也不可执行；' +
    LF +
    '  执行 Python 用 blender_py，截图/看视口用 blender_see，作业用 blender_run。' +
    LF +
    '已知写命令（export_scene / set_texture / download_* / create_*_job …）返回 wrong_tool 并指向正确工具；' +
    LF +
    '未知命令返回 unknown_command + did_you_mean。',
}
