// addon command 目录表（F1.1 从 catalog.ts 拆出）
export const COMMAND_CATALOG: Record<string, { d: string; gate: string | null }> = {
  ping: { d: '存活探测（不碰 bpy 数据）', gate: null },
  get_scene_info: { d: '场景概览（无参）', gate: null },
  get_world_state_snapshot: { d: '世界状态快照：对象/选中/帧', gate: null },
  get_addon_info: { d: 'addon 版本与协议号', gate: null },
  get_object_info: { d: '单对象详情（参数 name）', gate: null },
  get_viewport_screenshot: { d: '视口离屏截图（max_size, filepath, format）', gate: null },
  // addon v1.10（协议号 11）的常驻命令 —— 让模型查 API 而不是猜
  describe_node_type: {
    d: '查节点类型的全部 socket/属性（bl_idname, property_overrides）—— 建着色器/几何节点前先查，别猜 socket 顺序',
    gate: null,
  },
  bpy_api_lookup: {
    d: '查 bpy API 参考（query，如 "bpy.ops.mesh.primitive_cube_add"）',
    gate: null,
  },
  export_scene: {
    d: '导出场景/选择/名单到 glb|fbx|obj（filepath, format, object_names, selection_only, apply_modifiers）',
    gate: null,
  },
  // v1.8（插件 v2.3）：Blender 原生选中 → DSH / 统一活动状态
  get_activity_state: {
    d: '统一活动状态 human/agent/idle/both + 原生选中摘要（无参；查询自身不算 agent）',
    gate: null,
  },
  get_selection: { d: '当前 Blender 原生选中（名字/类型/活动对象/模式/场景；无参）', gate: null },
  send_selection_to_dsh: {
    d: '把当前选中 POST 给 DSH 后端（N 面板按钮的同一实现；timeout 可选）',
    gate: null,
  },
  get_collections: {
    d: '列场景集合：名字 / 对象数 / 其中选中数（limit 可选；大装配体先选集合）',
    gate: null,
  },
  send_collection_to_dsh: {
    d: '把整个集合 POST 给 DSH（collection 可选，缺省用 N 面板下拉的 dshbb_collection_target）',
    gate: null,
  },
  execute_code: { d: '执行任意 Python（万能通道，预置 K/bpy/math/mathutils）', gate: null },
  drain_human_activity: { d: '取走"人类操作"事件缓冲', gate: null },
  get_telemetry_consent: { d: '读遥测同意开关', gate: null },
  set_telemetry_consent: { d: '写遥测同意开关（参数 consent）', gate: null },
  get_polyhaven_status: { d: 'PolyHaven 集成状态', gate: null },
  get_hyper3d_status: { d: 'Hyper3D Rodin 集成状态', gate: null },
  get_sketchfab_status: { d: 'Sketchfab 集成状态', gate: null },
  get_polypizza_status: { d: 'Poly Pizza 集成状态', gate: null },
  get_hunyuan3d_status: { d: 'Hunyuan3D 集成状态', gate: null },
  get_polyhaven_categories: {
    d: 'PolyHaven 分类（参数 asset_type: hdris/textures/models）',
    gate: 'dshbb_use_polyhaven',
  },
  search_polyhaven_assets: {
    d: 'PolyHaven 搜索（asset_type, categories）',
    gate: 'dshbb_use_polyhaven',
  },
  download_polyhaven_asset: {
    d: '下载并导入 PolyHaven 资产（asset_id, asset_type, resolution）',
    gate: 'dshbb_use_polyhaven',
  },
  set_texture: {
    d: '把已下载贴图应用到对象（object_name, texture_id）',
    gate: 'dshbb_use_polyhaven',
  },
  create_rodin_job: { d: 'Hyper3D Rodin 生成任务（文本/图片）', gate: 'dshbb_use_hyper3d' },
  poll_rodin_job_status: { d: 'Rodin 任务轮询', gate: 'dshbb_use_hyper3d' },
  import_generated_asset: { d: '导入 Rodin 生成结果', gate: 'dshbb_use_hyper3d' },
  search_sketchfab_models: {
    d: 'Sketchfab 搜索（query, categories, count）',
    gate: 'dshbb_use_sketchfab',
  },
  get_sketchfab_model_preview: { d: 'Sketchfab 模型预览图', gate: 'dshbb_use_sketchfab' },
  download_sketchfab_model: { d: '下载并导入 Sketchfab 模型（uid）', gate: 'dshbb_use_sketchfab' },
  search_polypizza_models: { d: 'Poly Pizza 搜索（query）', gate: 'dshbb_use_polypizza' },
  download_polypizza_model: {
    d: '下载并导入 Poly Pizza 模型（model_id）',
    gate: 'dshbb_use_polypizza',
  },
  create_hunyuan_job: { d: 'Hunyuan3D 生成任务', gate: 'dshbb_use_hunyuan3d' },
  poll_hunyuan_job_status: { d: 'Hunyuan3D 任务轮询', gate: 'dshbb_use_hunyuan3d' },
  import_generated_asset_hunyuan: { d: '导入 Hunyuan3D 生成结果', gate: 'dshbb_use_hunyuan3d' },
}

// Wire-only addon commands that are deliberately not model-facing (D6). Declared
// here so the host catalog <-> addon registry parity is checkable in both
// directions; addon/dsh_blender_bridge/command_registry.py COMMAND_NAMES stays
// the only command truth. Consumed by tests/unit/js/contract/command-contract.test.ts
// and tests/integration/python/test_addon_command_matrix.py.
export const COMMAND_CATALOG_OMISSIONS: readonly string[] = ['get_polyhaven_asset_preview']
