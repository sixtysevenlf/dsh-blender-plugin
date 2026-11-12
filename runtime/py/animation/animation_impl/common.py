# -*- coding: utf-8 -*-
"""animation common: limits, structured failures and pure matrix/vector math.

The module deliberately has no bpy/mathutils import: the adapter in report.py
converts mathutils values to plain 4x4 row tuples so every metric in metrics.py
and every static check in plan.py is unit-testable outside Blender.

Matrix convention is row-major: m[row][col]; a 4x4 transform maps column
vectors, which matches mathutils.Matrix iteration by rows. quaternions are
plain (w, x, y, z) tuples; angles are degrees unless a name says otherwise.
"""
import json
import math

ANIMATION_VERSION = 1

MAX_OBJECTS = 16
MAX_BONES = 64
MAX_CONTACT_BONES = 16
MAX_INTERSECTION_OBJECTS = 16
MAX_SAMPLES = 240
MAX_CONTACT_INTERVALS = 64
MAX_INTERSECTION_PAIRS = 64
MAX_REFERENCE_FRAMES = 240
MAX_REFERENCE_BYTES = 2 * 1024 * 1024
TRIANGLE_BUDGET = 200000

DEFAULT_BONE_FILTER = "deform"
BONE_FILTERS = ("deform", "all")
DEFAULT_CONTACT_POINT = "tail"
CONTACT_POINTS = ("head", "tail", "center")
DEFAULT_GROUND_Z = 0.0
DEFAULT_CONTACT_EPSILON = 0.01

UNITS = {"length": "m", "angle": "deg", "speed": "m/s", "angular_speed": "deg/s", "fps": "1/s"}


class AnimationError(Exception):
    """Structured failure: code/stage/root_cause/fix/fallback plus extra fields."""

    def __init__(self, code, stage, root_cause, fix, fallback=None, **extra):
        super().__init__(root_cause)
        self.code = str(code)
        self.stage = str(stage)
        self.root_cause = str(root_cause)
        self.fix = str(fix)
        self.fallback = fallback
        self.extra = dict(extra)

    def payload(self):
        out = {
            "ok": False,
            "module": "animation",
            "version": ANIMATION_VERSION,
            "code": self.code,
            "stage": self.stage,
            "error": self.root_cause,
            "root_cause": self.root_cause,
            "fix": self.fix,
            "units": dict(UNITS),
        }
        if self.fallback:
            out["fallback"] = self.fallback
        out.update(self.extra)
        return out


def fail(code, stage, root_cause, fix, fallback=None, **extra):
    raise AnimationError(code, stage, root_cause, fix, fallback=fallback, **extra)


def warning(code, detail=""):
    return {"code": str(code), "detail": str(detail)}


def truncated(items, limit):
    """Return (bounded list, truncated flag) preserving input order."""
    items = list(items or [])
    if len(items) <= limit:
        return items, False
    return items[:limit], True


# ── vector / matrix / quaternion helpers ─────────────────────────────────────

def mat_identity():
    return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mat_translation(x, y, z):
    return ((1.0, 0.0, 0.0, float(x)), (0.0, 1.0, 0.0, float(y)), (0.0, 0.0, 1.0, float(z)), (0.0, 0.0, 0.0, 1.0))


def mat_rot_z(deg):
    r = math.radians(float(deg))
    c, s = math.cos(r), math.sin(r)
    return ((c, -s, 0.0, 0.0), (s, c, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mat_mul(a, b):
    return tuple(tuple(sum(float(a[r][k]) * float(b[k][c]) for k in range(4)) for c in range(4)) for r in range(4))


def mat_inverse(m):
    """Gauss-Jordan inverse of a 4x4 row-major matrix (raises ValueError if singular)."""
    a = [[float(m[r][c]) for c in range(4)] + [1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("singular 4x4 matrix")
        a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        a[col] = [v / pv for v in a[col]]
        for r in range(4):
            if r == col:
                continue
            factor = a[r][col]
            if factor:
                a[r] = [a[r][k] - factor * a[col][k] for k in range(8)]
    return tuple(tuple(a[r][4 + c] for c in range(4)) for r in range(4))


def mat3(m):
    return tuple(tuple(float(m[r][c]) for c in range(3)) for r in range(3))


def mat3_mul(a, b):
    return tuple(tuple(sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)) for r in range(3))


def mat3_inverse(a):
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        raise ValueError("singular 3x3 matrix")
    inv = (
        ((a[1][1] * a[2][2] - a[1][2] * a[2][1]), (a[0][2] * a[2][1] - a[0][1] * a[2][2]), (a[0][1] * a[1][2] - a[0][2] * a[1][1])),
        ((a[1][2] * a[2][0] - a[1][0] * a[2][2]), (a[0][0] * a[2][2] - a[0][2] * a[2][0]), (a[0][2] * a[1][0] - a[0][0] * a[1][2])),
        ((a[1][0] * a[2][1] - a[1][1] * a[2][0]), (a[0][1] * a[2][0] - a[0][0] * a[2][1]), (a[0][0] * a[1][1] - a[0][1] * a[1][0])),
    )
    return tuple(tuple(v / det for v in row) for row in inv)


def mat3_identity():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def mat3_orthonormalize(m):
    """Gram-Schmidt the columns of a 3x3 so quaternion extraction sees a rotation only.

    Pose products can carry scale/shear (bone scale, stretched parents). Feeding that
    straight into mat3_to_quat skews the angle, so the rotation part is rebuilt from an
    orthonormal column basis; a degenerate basis falls back to identity.
    """
    c0 = (float(m[0][0]), float(m[1][0]), float(m[2][0]))
    c1 = (float(m[0][1]), float(m[1][1]), float(m[2][1]))
    n0 = vec_norm(c0)
    if n0 < 1e-12:
        return mat3_identity()
    c0 = (c0[0] / n0, c0[1] / n0, c0[2] / n0)
    dot = c1[0] * c0[0] + c1[1] * c0[1] + c1[2] * c0[2]
    c1 = (c1[0] - dot * c0[0], c1[1] - dot * c0[1], c1[2] - dot * c0[2])
    n1 = vec_norm(c1)
    if n1 < 1e-12:
        return mat3_identity()
    c1 = (c1[0] / n1, c1[1] / n1, c1[2] / n1)
    c2 = (c0[1] * c1[2] - c0[2] * c1[1], c0[2] * c1[0] - c0[0] * c1[2], c0[0] * c1[1] - c0[1] * c1[0])
    return (
        (c0[0], c1[0], c2[0]),
        (c0[1], c1[1], c2[1]),
        (c0[2], c1[2], c2[2]),
    )


def quat_normalize(q):
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return (w / n, x / n, y / n, z / n)


def mat3_to_quat(m):
    """Shepperd's method: rotation 3x3 -> normalized (w, x, y, z)."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return quat_normalize((w, x, y, z))


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_conjugate(q):
    return (q[0], -q[1], -q[2], -q[3])


def quat_angle_deg(q):
    """Shortest arc angle of a rotation quaternion, in degrees."""
    w = min(1.0, max(-1.0, abs(float(q[0]))))
    return math.degrees(2.0 * math.acos(w))


def quat_delta_shortest(a, b):
    """b relative to a, flipped to the w >= 0 hemisphere (shortest arc)."""
    d = quat_mul(quat_conjugate(quat_normalize(a)), quat_normalize(b))
    return (-d[0], -d[1], -d[2], -d[3]) if d[0] < 0.0 else d


def vec_sub(a, b):
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2]))


def vec_add(a, b):
    return (float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2]))


def vec_scale(a, s):
    return (float(a[0]) * float(s), float(a[1]) * float(s), float(a[2]) * float(s))


def vec_norm(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def vec_distance(a, b):
    return vec_norm(vec_sub(a, b))


def json_size_bytes(value):
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return None
