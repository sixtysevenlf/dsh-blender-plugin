
import bpy, bmesh, json, os, time, math
from mathutils import Vector

OUT = 'D:/DSH/blender/tmp/p0_e2e'
os.makedirs(OUT, exist_ok=True)
EV = {}
def rec(k, v):
    EV[k] = v
    print('[E2E]', k, json.dumps(v, ensure_ascii=False)[:600])

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
def add_cube(name, size, loc):
    me = bpy.data.meshes.new(name + '_mesh')
    ob = bpy.data.objects.new(name, me)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me); bm.free()
    ob.scale = size; ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    return ob

ref_box = add_cube('REF_box', (2.0, 1.0, 0.8), (0, 0, 0.4))
cam_d = bpy.data.cameras.new('REF_cam'); cam = bpy.data.objects.new('REF_cam', cam_d)
sc.collection.objects.link(cam); sc.camera = cam
cam_d.type = 'ORTHO'; cam_d.ortho_scale = 4.0
cam.location = (0, -10, 0.4)
cam.rotation_euler = (Vector((0,0,0.4)) - Vector(cam.location)).to_track_quat('-Z','Y').to_euler()
sc.render.engine = 'BLENDER_WORKBENCH'
sc.render.resolution_x = 640; sc.render.resolution_y = 480; sc.render.resolution_percentage = 100
sc.render.image_settings.file_format = 'PNG'; sc.render.image_settings.color_mode = 'RGBA'
try:
    sc.render.film_transparent = True
    sc.display.shading.color_type = 'SINGLE'; sc.display.shading.single_color = (0.6,0.6,0.6)
    sc.display.shading.light = 'STUDIO'; sc.display.render_aa = '8'
except Exception: pass
ref_path = OUT + '/ref_box.png'
sc.render.filepath = ref_path
bpy.ops.render.render(write_still=True)

IMG = K.dsh_img_api
d0 = IMG('digest', {'path': ref_path, 'out_dir': OUT + '/digest', 'attach': 1})
rec('digest_noscale', {'ok': d0.get('ok'), 'verdict': d0.get('verdict'), 'bbox': (d0.get('silhouette') or {}).get('bbox_px'), 'aspect': (d0.get('silhouette') or {}).get('aspect_wh'), 'numbers_len': len(d0.get('numbers') or [])})
bb = (d0.get('silhouette') or {}).get('bbox_px') or [0,0,1,1]
x, y, w, h = bb
known = {'mm': 2000.0, 'p1': [x, y + h/2.0], 'p2': [x + w, y + h/2.0], 'label': 'box_width_2000mm'}
d1 = IMG('digest', {'path': ref_path, 'known_len': known, 'out_dir': OUT + '/digest2', 'attach': 1})
nums = {n.get('key'): n for n in (d1.get('numbers') or [])}
rec('digest_calibrated', {'ok': d1.get('ok'), 'verdict': d1.get('verdict'), 'px_per_mm': (d1.get('scale') or {}).get('px_per_mm'), 'width_mm': nums.get('width_mm', {}).get('value'), 'height_mm': nums.get('height_mm', {}).get('value'), 'aspect': (d1.get('silhouette') or {}).get('aspect_wh')})
rec('digest_overlay', (d1.get('overlay') or {}))

parts = []
for i in range(3):
    for jj in range(4):
        parts.append({'id': 'p%d%d' % (i,jj), 'prim': 'box', 'size_mm': [180, 120, 80], 'at_mm': [i*220-220, jj*160-240, 40]})
t0 = time.time()
bo = K.dsh_blockout_api('base', {'name':'e2e12', 'units':'mm', 'parts': parts, 'collection':'BO_e2e12',
    'render': {'engine':'workbench','res':[512,512],'views':'auto3','sheet':True,'via':'auto'},
    'export': {'glb': OUT + '/e2e12.glb'}, 'audit': True})
rec('blockout', {'ok': bo.get('ok'), 'objects': len(bo.get('parts') or []), 'tris': bo.get('total_tris'), 'ms': bo.get('ms'), 'views': [(v.get('name'), v.get('ms'), v.get('bytes')) for v in ((bo.get('render') or {}).get('views') or [])], 'glb': bo.get('export'), 'wall_ms': int((time.time()-t0)*1000)})

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
base = add_cube('BASE', (3.0,1.2,0.1), (0,0,-0.05))
a = add_cube('A', (1.0,1.0,1.0), (0,0,0.5))
b = add_cube('B', (1.0,1.0,1.0), (0.998,0,0.5))
t0 = time.time(); s1 = K.dsh_assembly_api('scan', {'objects': ['A','B'], 'spec': {}, 'limit': 0, 'summary_only': False})
rec('scan_clash', {'ok': s1.get('ok'), 'verdict': s1.get('verdict'), 'counts': s1.get('counts'), 'pairs': [{k: p.get(k) for k in ('a','b','class','depth_mm','volume_mm3','declared')} for p in (s1.get('pairs') or [])][:4], 'ms': s1.get('ms'), 'wall_ms': int((time.time()-t0)*1000)})
b.location.x = 1.01
bpy.context.view_layer.update()
s2 = K.dsh_assembly_api('scan', {'objects': ['A','B'], 'spec': {}, 'limit': 0, 'summary_only': False})
rec('scan_gap', {'ok': s2.get('ok'), 'verdict': s2.get('verdict'), 'counts': s2.get('counts'), 'ms': s2.get('ms')})

ok = bool(d0.get('ok')) and bool(d1.get('ok')) and d1.get('verdict') == 'ready_for_spec' and bool(bo.get('ok')) \
     and s1.get('verdict') == 'fail' and (s1.get('counts') or {}).get('clash', 0) >= 1 and (s2.get('counts') or {}).get('clash', 0) == 0
print('HEADLESS ' + json.dumps({'ok': ok, 'evidence': EV}, ensure_ascii=False))
