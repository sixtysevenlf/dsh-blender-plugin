// Blender 侧常见报错的补充提示（T6）
// @imports
import { wslPathShared } from "../config.js";
import { WSL_TMP } from "../paths.js";


  /** v0.8.7：把 Blender 侧常见坑的报错补一句可执行提示（外部反馈 #4 的 P2-3） */
  export function enrichError(msg: unknown, tb: unknown): string {
    const s = String(msg || '');
    const t = String(tb || '');
    const both = s + ' ' + t;
    if (/已使用的库|in use|Cannot overwrite/i.test(both)) {
      return s + '  ← 提示：该 .blend 在本会话被当作库加载过。可先 bpy.data.libraries.remove(<该库>) 释放，或改用 bpy.ops.wm.save_as_mainfile(filepath=..., copy=True) 写副本（copy=True 不动当前 filepath）。';
    }
    if (/Cannot render, no camera/i.test(both)) {
      return s + '  ← 提示：场景没有相机（可能被清理/灯光重建循环删掉）。无头渲染前先设 scene.camera，或用 blender_rt_see 的自定义视角出图。';
    }
    // v0.8.9（外部会话反馈）：open_mainfile 之后旧引用全失效 + GUI 上下文指针失效
    if (/StructRNA of type .* has been removed|ReferenceError: StructRNA/i.test(both)) {
      return s + '  ← 提示：这是一枚「失效引用」—— bpy.ops.wm.open_mainfile() 会重建 bpy.data，换文件前抓到的 Object/Collection/材质引用全部作废。修法：open 之后重新从 bpy.data.objects[...] / bpy.context.scene 取对象，别复用旧变量。';
    }
    if (/context is incorrect/i.test(both)) {
      return s + '  ← 提示：bpy.ops 的上下文不满足。换过文件/换过模式后：先 bpy.context.view_layer.objects.active = obj、obj.select_set(True)，再调 op；仍报错就用 with bpy.context.temp_override(view_layer=vl, active_object=obj, selected_objects=[obj]): bpy.ops....（实测 open 后 temp_override 可正常 join）；实在不行拆成两次 rt_do 调用（每次调用都是新命名空间 = 重新取上下文）。';
    }
    if (/No such file or directory|could not be opened|无法读取/i.test(both) && /\/\/wsl/i.test(both)) {
      return s + '  ← 提示：`//wsl.localhost/…`（正斜杠）会被 Blender 当成**相对 .blend** 路径。用反斜杠 UNC（\\\\wsl.localhost\\<distro>\\…），或先把文件拷到 Windows 可见目录。';
    }
    if (/No such file or directory|could not be opened|无法读取/i.test(both) && /wsl\.localhost|\\\\wsl/i.test(both)) {
      // v0.9.6（D3）：这条以前被误诊成"UNC 形态问题"。真正常见的根因是**挂载不共享**：
      // Node 写得进（在 WSL 命名空间里），Windows 的 Blender 看不见那个挂载点。
      const sh = wslPathShared(WSL_TMP);
      return s + '  ← 提示：路径是 \\\\wsl.localhost\\<distro>\\…，但那个位置 Windows 侧的 Blender 看不到。'
        + '当前 workDir=' + WSL_TMP + '（shared=' + String(sh.shared) + '，' + String(sh.why) + '）。'
        + '把工作目录放到 Windows 读得到的地方：Windows 盘（D:\\…）或发行版根文件系统里的目录'
        + '（WSL /home/… ↔ \\\\wsl.localhost\\<distro>\\home\\…）。'
        + 'v0.9.6 起无头会自动避开这类目录并在回执 scriptStaging / pathWarnings 里说明。';
    }
    return s;
  }
