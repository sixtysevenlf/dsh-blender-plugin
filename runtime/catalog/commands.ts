// addon command 目录表（25 条：21 条只读 + 4 条内部传输；ping 仅 wire）
//
// access=read: blender_inspect 可执行、可出现在 help 里。
// access=internal: 只作为 blender_py / blender_see / host 动作的传输通道，
// 不出现在 blender_inspect help、不作为模型可见命令。
export interface CommandCatalogEntry {
  d: string
  gate: string | null
  access: 'read' | 'internal'
}

export const COMMAND_CATALOG: Record<string, CommandCatalogEntry> = {
  get_scene_info: { d: '场景概览（无参）', gate: null, access: 'read' },
  get_world_state_snapshot: { d: '世界状态快照：对象/选中/帧', gate: null, access: 'read' },
  get_addon_info: { d: 'addon 版本与协议号', gate: null, access: 'read' },
  get_object_info: { d: '单对象详情（参数 name）', gate: null, access: 'read' },
  get_viewport_screenshot: {
    d: '视口离屏截图（blender_see 的内部传输）',
    gate: null,
    access: 'internal',
  },
  execute_code: {
    d: '执行任意 Python（blender_py 的内部传输，预置 K/bpy/math/mathutils）',
    gate: null,
    access: 'internal',
  },
  // 让模型查 API 而不是猜
  describe_node_type: {
    d: '查节点类型的全部 socket/属性（bl_idname, property_overrides）—— 建着色器/几何节点前先查，别猜 socket 顺序',
    gate: null,
    access: 'read',
  },
  bpy_api_lookup: {
    d: '查 bpy API 参考（query，如 "bpy.ops.mesh.primitive_cube_add"）',
    gate: null,
    access: 'read',
  },
  // Blender 原生选中 → DSH / 统一活动状态
  get_activity_state: {
    d: '统一活动状态 human/agent/idle/both + 原生选中摘要（无参；查询自身不算 agent）',
    gate: null,
    access: 'read',
  },
  get_selection: {
    d: '当前 Blender 原生选中（名字/类型/活动对象/模式/场景；无参）',
    gate: null,
    access: 'read',
  },
  send_selection_to_dsh: {
    d: '把当前选中 POST 给 DSH 后端（N 面板按钮的同一实现；host 内部动作）',
    gate: null,
    access: 'internal',
  },
  get_collections: {
    d: '列场景集合：名字 / 对象数 / 其中选中数（limit 可选；大装配体先选集合）',
    gate: null,
    access: 'read',
  },
  send_collection_to_dsh: {
    d: '把整个集合 POST 给 DSH（host 内部动作；collection 可选，缺省用 N 面板下拉的 dshbb_collection_target）',
    gate: null,
    access: 'internal',
  },
  get_polyhaven_status: { d: 'PolyHaven 集成状态', gate: null, access: 'read' },
  get_hyper3d_status: { d: 'Hyper3D Rodin 集成状态', gate: null, access: 'read' },
  get_sketchfab_status: { d: 'Sketchfab 集成状态', gate: null, access: 'read' },
  get_polypizza_status: { d: 'Poly Pizza 集成状态', gate: null, access: 'read' },
  get_hunyuan3d_status: { d: 'Hunyuan3D 集成状态', gate: null, access: 'read' },
  get_polyhaven_categories: {
    d: 'PolyHaven 分类（参数 asset_type: hdris/textures/models）',
    gate: 'dshbb_use_polyhaven',
    access: 'read',
  },
  search_polyhaven_assets: {
    d: 'PolyHaven 搜索（asset_type, categories）',
    gate: 'dshbb_use_polyhaven',
    access: 'read',
  },
  poll_rodin_job_status: {
    d: 'Rodin 任务轮询',
    gate: 'dshbb_use_hyper3d',
    access: 'read',
  },
  search_sketchfab_models: {
    d: 'Sketchfab 搜索（query, categories, count）',
    gate: 'dshbb_use_sketchfab',
    access: 'read',
  },
  get_sketchfab_model_preview: {
    d: 'Sketchfab 模型预览图',
    gate: 'dshbb_use_sketchfab',
    access: 'read',
  },
  search_polypizza_models: {
    d: 'Poly Pizza 搜索（query）',
    gate: 'dshbb_use_polypizza',
    access: 'read',
  },
  poll_hunyuan_job_status: {
    d: 'Hunyuan3D 任务轮询',
    gate: 'dshbb_use_hunyuan3d',
    access: 'read',
  },
}

// Wire-only addon commands that are deliberately not model-facing. Declared here
// so the host catalog <-> addon registry parity is checkable in both directions;
// extension/dsh_blender_bridge/command_registry.py COMMAND_NAMES stays the only
// command truth. Consumed by tests/unit/js/contract/command-contract.test.ts and
// tests/integration/python/test_addon_command_matrix.py.
export const COMMAND_CATALOG_OMISSIONS: readonly string[] = []
