/**
 * Host-side copy of the catalog's read-only op list.
 *
 * The Gateway /plan route skips the lease gate for READ_ONLY_OPS and for the
 * host-local vision ops (runtime/catalog/data.ts: PLAN_READ_ONLY_OPS ∪
 * PLAN_HOST_LOCAL_OPS), so blender_op must make the same decision before it
 * asks for a session. runtime/catalog/data.ts is the source of truth; the
 * contract test op-read-only-parity.test.ts fails when the lists drift.
 */
/**
 * host-local 视觉 op：Gateway 进程内执行，不需要 GUI 会话、不需要 lease，
 * 因此调用时也不该声明 write_scope。
 */
export const HOST_LOCAL_OPS: string[] = [
  'vision_status',
  'vision_analyze',
  'vision_compare',
  'vision_detect',
  'vision_track',
  'vision_pass',
  'vision_selftest',
  'vision_help',
]

export const READ_ONLY_OPS: string[] = [
  'status',
  'help',
  'ledger',
  'check_envelope',
  'check_interference',
  'check_interface',
  'plan_status',
  'plan_validate',
  'plan_diag',
  'plan_order',
  'plan_graph',
  'audit_mesh',
  'audit_scene',
  'audit_duplicates',
  'audit_help',
  'audit_connectivity',
  'audit_drift',
  'audit_measure',
  'audit_gate',
  'montage',
  'motion_status',
  'motion_help',
  'motion_joints',
  'deliver_help',
  'deliver_verify',
  'generator_list',
  'generator_get',
  'generator_diff',
  'generator_help',
  'catalog',
  'catalog_help',
  'help_all',
  'material_scan',
  'material_help',
  'gate_plan',
  'gate_help',
  'img_scan',
  'img_help',
  'calib_help',
  'face_ratios',
  'face_compare',
  'face_help',
  'gltf_validate',
  'clear_check',
  'clear_help',
  'vehicle_package',
  'vehicle_spec',
  'vehicle_help',
  'shape_draft',
  'shape_draft_check',
  'shape_plan',
  'shape_help',
  'shape_sections',
  'shape_revolve',
  'render_state',
  'render_wait',
  'sculpt_scan',
  'sculpt_help',
  'fix_help',
  'uv_stats',
  'uv_help',
  'print_walls',
  'print_overhang',
  'print_report',
  'print_help',
  'sweep_analyze',
  'sweep_help',
  'gui_frame',
  'gui_shading',
  'gui_help',
  'render_status',
  'render_lock_status',
  'gate_preflight',
  'gate_checkpoint',
  'model_preflight',
  'model_checkpoint',
  'visualreport_report',
  'visualreport_help',
  'qc_visual_report',
  'shapesolve_help',
  'eval_help',
  'surface_help',
  'img_digest',
  'img_probe',
  'img_measure',
  'img_calib',
  'audit_interference',
  'audit_overlap',
  'audit_snap_floaters',
  'interference_report',
  'asm_scan',
  'asm_manifest',
  'asm_gate',
  'asm_selftest',
  'asm_help',
  'fix_interference',
  'blockout_help',
  'blockout_selftest',
  ...HOST_LOCAL_OPS,
]

export const READ_ONLY = new Set<string>(READ_ONLY_OPS)
export const HOST_LOCAL = new Set<string>(HOST_LOCAL_OPS)

/** true = host-local op（Gateway 不让它碰会话，也不需要写作用域）。 */
export function isHostLocalOp(op: unknown): boolean {
  return HOST_LOCAL.has(String(op))
}
