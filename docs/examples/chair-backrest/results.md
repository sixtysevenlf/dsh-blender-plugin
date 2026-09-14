# 椅子靠背接缝：外部证据不足 + 探针判别（自动生成）

> 阶段一（只用外部证据）：**外部证据不足（attach, insert 都能满足）→ 必须报 unresolved**

> 阶段二（加探针证据）：**探针后唯一支持：insert**

| 假设 | 允许区间 | 试了 | 最优看板参数 | 外部误差(侧+后) | 探针读数 | 与真值差 | 外部判定 | 探针判定 |
|---|---|---|---|---|---|---|---|---|
| attach | dy 0.0~0.03 · tilt -2.0~6.0 deg · dz -0.02~0.02 · 榫长 0.0~0.0 | 48 | dy=0.010 tilt=-2.0 dz=0.020 榫=0.000 | 184 | 0.0000 m | 0.0600 m | unresolved | refuted |
| insert | dy 0.0~0.03 · tilt -2.0~6.0 deg · dz -0.02~0.02 · 榫长 0.03~0.1 | 48 | dy=0.010 tilt=-2.0 dz=0.020 榫=0.030 | 184 | 0.0650 m | 0.0050 m | unresolved | supported |

## 可辨识性（只用外部证据时，哪些参数是能钉住的）

| 假设 | 参数 | 该参数各取值下的最小外部误差 | 极差 | 可辨识? |
|---|---|---|---|---|
| attach | dy | 0.0:476 / 0.01:184 / 0.02:485 / 0.03:974 | 790 | 是 |
| attach | tilt | -2.0:184 / 6.0:495 | 311 | 是 |
| attach | dz | -0.02:186 / 0.02:184 | 2 | 否（平坦） |
| attach | tenon_len | 0.0:184 | 0 | 否（平坦） |
| insert | dy | 0.0:476 / 0.01:184 / 0.02:485 / 0.03:974 | 790 | 是 |
| insert | tilt | -2.0:184 / 6.0:495 | 311 | 是 |
| insert | dz | -0.02:186 / 0.02:184 | 2 | 否（平坦） |
| insert | tenon_len | 0.03:184 / 0.065:184 / 0.1:184 | 0 | 否（平坦） |

> 极差 ≈ 0 表示该维度对外部证据**不可辨识**：程序必须说出来，而不是随手取一个值（本示例里是 tenon_len）。

## 判定依据（两句人话）

- **attach**：外部 —— 外部证据能满足（best=184 <= 220），但 tenon_len 在外部视图下不可辨识 → 需要探针；探针 —— 探针（拆解 + 深度规）：读数 0.0000 m，与真值 0.0600 m 差 0.0600 m > 0.0100 m
- **insert**：外部 —— 外部证据能满足（best=184 <= 220），但 tenon_len 在外部视图下不可辨识 → 需要探针；探针 —— 探针（拆解 + 深度规）：读数 0.0650 m，与真值 0.0600 m 差 0.0050 m <= 0.0100 m

## 证据文件

| 文件 | 内容 |
|---|---|
| gt_external_side.png / gt_external_rear.png | 真值的外部两张（接缝被挡板遮住） |
| gt_probe_no_seat.png | 探针证据：藏掉座椅后看榫区 |
| best_attach_probe.png | attach 假设最优参数在探针视图下 |
| best_insert_probe.png | insert 假设最优参数在探针视图下 |

## 复现

    blender -b --factory-startup --python docs/examples/chair-backrest/run.py -- <outdir>
    # 或 blender_rt_headless(preload='view', script=..., outdir=...)

## 结论怎么用

- 外部证据不足时不要在 attach / insert 之间随便选：报 unresolved，并列出「需要什么探针」；
- 判别前禁止 boolean_union / weld / apply_transform（会毁掉可回退性）；
- 拿到探针证据后再判 supported / refuted，并把两次判定的证据一起存档。
