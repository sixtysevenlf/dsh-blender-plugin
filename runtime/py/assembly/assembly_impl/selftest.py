# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import asm_manifest
from .scan import asm_scan
from .gate import asm_gate
from .merge import asm_merge

def _selftest_box(name, size, loc):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co *= size
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    return ob


def asm_selftest():
    """模块内快速自检：clash 分类 / manifest 往返 / merge dry_run / 坏参数 ok:false。"""
    import tempfile
    ev = {}
    t0 = time.perf_counter()
    made, tmpdir = [], None
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001
        a = _selftest_box("__dsh_asm_a", 20.0, (0, 0, 0))
        b = _selftest_box("__dsh_asm_b", 20.0, (19.0, 0, 0))
        made += [a, b]
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        r = json.loads(asm_scan(objects=["__dsh_asm_a", "__dsh_asm_b"], summary_only=False,
                                max_pairs=10, report_gap_mm=2.0))
        by = {}
        for p in r.get("pairs") or []:
            by[tuple(sorted([p.get("a"), p.get("b")]))] = p
        rec = by.get(("__dsh_asm_a", "__dsh_asm_b")) or {}
        ev["scan_clash"] = rec.get("class") == "clash"
        ev["scan_depth_measured"] = rec.get("depth_mm") is not None
        ev["scan_verdict_fail"] = r.get("verdict") == "fail"
        r_bad = json.loads(asm_scan(objects=["__dsh_asm_nope"]))
        ev["scan_bad_input_okfalse"] = r_bad.get("ok") is False
        tmpdir = tempfile.mkdtemp(prefix="dsh_asm_selftest_")
        partfile = os.path.join(tmpdir, "part_a.blend")
        bpy.ops.wm.save_as_mainfile(filepath=partfile, copy=True)
        man = os.path.join(tmpdir, "assembly.json")
        m1 = json.loads(asm_manifest(action="init", path=man, assembly="selftest", force=True))
        m2 = json.loads(asm_manifest(action="add_part", path=man,
                                     part={"id": "part_a", "file": partfile, "objects": ["__dsh_asm_a"]}))
        m3 = json.loads(asm_manifest(action="seal", path=man))
        ev["manifest_roundtrip"] = bool(m1.get("ok") and m2.get("ok") and m3.get("ok"))
        ev["manifest_sealed"] = bool(m3.get("sealed"))
        dry = json.loads(asm_merge(manifest=man, out_blend=os.path.join(tmpdir, "merged.blend"),
                                   dry_run=True))
        ev["merge_dry_run"] = bool(dry.get("ok") and dry.get("dry_run") is True)
        ev["merge_dry_no_write"] = not os.path.isfile(os.path.join(tmpdir, "merged.blend"))
        ev["merge_order"] = dry.get("order")
        ev["merge_target_names"] = (dry.get("parts") or [{}])[0].get("target_names")
        g_bad = json.loads(asm_gate(objects=[]))
        ev["gate_bad_input_okfalse"] = g_bad.get("ok") is False
        ok = all(v is True for k, v in ev.items() if k not in ("merge_order", "merge_target_names"))
        return _j({"ok": bool(ok), "version": ASSEMBLY_VERSION, "evidence": ev,
                   "ms": int((time.perf_counter() - t0) * 1000)})
    except Exception as e:
        import traceback
        return _j({"ok": False, "version": ASSEMBLY_VERSION, "evidence": ev,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "traceback": traceback.format_exc()[-600:]})
    finally:
        bpy.context.scene.unit_settings.scale_length = prev_scale
        for nm in [str(o.name) for o in made]:
            ob = bpy.data.objects.get(nm)
            if ob is not None:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me is not None and me.users == 0:
                    try:
                        bpy.data.meshes.remove(me, do_unlink=True)
                    except Exception:
                        pass
        if tmpdir:
            try:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


def asm_help():
    return _j({
        "module": "assembly.py", "version": ASSEMBLY_VERSION,
        "what": "装配层：全对全穿模/包含分类扫描 + 零件清单 + N 路合并 + 装配出厂门（verdict-first）",
        "ops": {
            "scan": {"alias": "audit_clash_all",
                     "sig": "scope|objects|file, manifest?, spec?, limit=0, max_pairs=20000, "
                            "depth_eps_mm=0.2, contact_eps_mm=0.3, report_gap_mm=5.0, summary_only=false",
                     "out": "verdict + counts{clash,hidden_ok,contact,gap,float,contained,unresolved} + "
                            "pairs + matrix(sparse) + coverage{complete,truncated,budget_hit} + offenders + repairs",
                     "why": "旧全对全在 tri 不相交时 continue ⇒ 完全包含漏判；本 op 补 containment 分支 + 逐对分类 + allow 白名单"},
            "manifest": {"action": "init|add_part|add_interface|add_allow|validate|seal|show",
                         "sig": "path, assembly, part={id,file,objects,owner,transform,interface_frames}, "
                                "interfaces=[{id,a,b,frame_a,frame_b,fit,nominal_mm,tol_mm,allow_overlap_mm}], "
                                "allow=[{a,b,max_depth_mm,max_volume_mm3,reason}], check_files=true, check_md5=true",
                         "out": "errors[]/warnings[]/manifest_md5/verdict；校验 id 唯一/file/file_md5/接口两端/basis 正交/单位"},
            "merge": {"sig": "manifest, out_blend, dry_run=true, transform_policy=manifest|as_is|snap_interface, "
                             "snap_tol_mm=2.0, recalc_normals=true, snapshot=true, gate=true, strict=true",
                      "flow": "校验 → (snapshot) → 按 part id 字典序 append → 改名 <partid>__<原名> → 变换 → "
                              "recalc normals → asm_gate → 通过才 save + merge_receipt.json；失败不保存"},
            "gate": {"alias": "merge_gate",
                     "sig": "scope|objects, manifest?, spec?, require{}, summary_only=true, strict=true, "
                            "tolerance_mm=0.5",
                     "gates": "mesh_health + normals + clash_all + floaters + interfaces + measure → 三态；"
                              "export_allowed 仅 verdict=pass"},
            "repair": {"alias": "fix_clash", "planner": "fix_interference",
                       "policy": "缺省（不打 policy）= safe 行为：自动只做 translate/snap，"
                                 "布尔保留 allow_boolean=true 显式授权（兼容旧调用）"
                                 "| 显式 policy=safe：硬禁布尔/scale"
                                 "| manual：只规划、绝不自动执行、不规划布尔"
                                 "| aggressive：保留旧行为，布尔受 allow_boolean 控制",
                       "sig": "scope|objects|manifest, spec?, dry_run=true, max_move_mm=50.0, "
                              "allow_boolean=false, snapshot=true, max_rounds=3, clearance_mm=0.5, policy",
                       "flow": "asm_scan → 规划（clash/contained：体积小件沿 AABB 交叠最小轴平移 "
                               "AABB 分离所需量+clearance；float：audit_snap_floaters 计划；allow_boolean 且两件闭合"
                               "流形：从较大件布尔减较小件）→（dry_run=false）文件级 snapshot → 逐动作 apply "
                               "→ view_layer.update → 重跑 asm_scan，最多 max_rounds 轮",
                       "out": "dry_run=true：plan[]/blocked[]/expected（只读，场景不变）；"
                              "dry_run=false：before/after counts + residual + applied + blocked + snapshot",
                       "verdict": "dry_run：planned（有动作）/ blocked（无动作）/ pass（本来没坏）；"
                                  "执行后：残留 clash/contained/float=0 ⇒ ok:true 且 verdict=复扫三态，"
                                  "有残留 ⇒ ok:false + residual",
                       "safety": "dry_run 默认 true；布尔只在 allow_boolean=true 且两件闭合流形时规划；"
                                 "布尔执行必须有可用文件级快照（K.dsh_txn_api），否则拒绝；"
                                 "目标件有父级/动画 ⇒ blocked（整件平移会被覆盖）；"
                                 "plan[].risk / blocked[].blocked_reason 逐条可读",
                       "risk_policy": "safe 不自动做布尔/scale；manual 只出计划；aggressive 才保留 allow_boolean 布尔路径"},
            "fix_interference": {"planner_only": True,
                                 "sig": "a?, b?, plan_only=true, manifest?, scope?, objects?, spec?, "
                                        "max_move_mm=50.0, allow_boolean=false, clearance_mm=0.5, max_rounds=3, "
                                        "policy=safe",
                                 "out": "plan[]/blocked[]/expected（只读；a/b 可只保留该对）",
                                 "why": "asm_scan / asm_gate 的 repairs 里给的就是这个规划器调用；执行用 fix_clash"},
            "export": {"alias": "asm_export",
                       "sig": "manifest?, scope|objects, out_dir?, name=assembly, formats=[glb,fbx,obj], "
                              "merge=false, dry_run=false, include_receipt=true, ledger=true, verify=true",
                       "flow": "merge=true 且 dry_run=false → 先 asm_merge(dry_run=false)（内部 gate+snapshot，不过门不导出）"
                               "→ 官方导出器写 GLB/FBX/OBJ（自动 enable io_scene_gltf2/io_scene_fbx）"
                               "→ <out_dir>/<name>_manifest.json + append-only <out_dir>/merge_ledger.jsonl",
                       "out": "{ok, verdict, dir, files{glb,fbx,obj,manifest,ledger,blend,obj_mtl}, counts, "
                              "gate, merge, md5, manifest_md5, warnings}；dry_run=true 只回计划不写盘",
                       "verdict": "gate 三态；全部格式失败 ⇒ ok:false；部分失败 ⇒ ok:true + warnings",
                       "safety": "dry_run=true 绝不写盘/建目录；manifest 记录逐文件 md5/bytes/gate/merge receipt；"
                                 "ledger 只 append 不重写"},
            "selftest": {"sig": "无参", "out": "模块内快速自检（clash/manifest/merge dry_run/坏参数）"},
        },
        "verdict_rules": {
            "scan": "clash>0 或 contained>0 或 float>0 ⇒ fail；unresolved/截断/未判完对象 ⇒ degraded；否则 pass",
            "gate": "任一 fail ⇒ fail；任一 degraded/skipped ⇒ degraded；全部 pass ⇒ pass；"
                    "degraded ≠ 通过，export_allowed 仅 pass",
            "unresolved": "证据不足（开网格 / 法线朝内 / 未测）一律 unresolved，不得当 pass",
        },
        "manifest_schema": {"schema": MANIFEST_SCHEMA, "units": "mm", "coord": {"up": "Z", "forward": "-Y"},
                            "parts": "id/file/objects/owner/file_md5/transform{loc_mm,rot_euler_deg,scale}/interface_frames",
                            "interfaces": "id/a/b/frame_a/frame_b/fit/nominal_mm/tol_mm/allow_overlap_mm/clearance_min_mm",
                            "allow_pairs": "id/a/b/max_depth_mm/max_volume_mm3/reason"},
        "protocol": [
            "多会话并行：分件方各自 save parts/*.blend → Lead 用 asm_manifest init/add_part/add_interface/seal → asm_merge(dry_run 看计划) → asm_merge(dry_run=false) 自动过 asm_gate",
            "allow 唯一来源是 manifest/spec（跨进程可复现），不要指望 K.dsh_contract 的会话内 declared 状态",
            "设计内隐藏交叠写 spec.allow（max_depth_mm/max_volume_mm3）；不许用全局阈值放过",
            "gate 不过 ⇒ 不保存；修法看 offenders/repairs（fix_interference / fix_repair / 调接口帧）",
        ],
        "deps": {"required": "K.dsh_kit",
                 "optional": "contract（深测复用）/audit（mesh+connectivity+measure）/clearance/gate/txn（merge 快照）"},
        "honest_limits": ["采样式深度/体积是估计值", "完全包含按实心口径报 contained（空心壳需人核）",
                          "开网格/法线朝内 ⇒ unresolved", "全对全不算接触面积", "shots 本版未实现"],
    })
