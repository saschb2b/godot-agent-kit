"""A little gardener: the kit's chibi helpers end to end, and a template for new characters.

    godot-agent asset blender tests/fixture art_src/blender/gardener.py \\
        --to res://models/gardener.glb --param check=1 --turnaround

2.7 heads tall: a wide straw sun hat with a red ribbon band, a short brown bob (a cap,
three fringe clumps, two side locks), a green smock with a cream hem band and a round
cream collar, garden-glove mittens, chunky boots, and a trowel in her right mitten. Left
fist on her hip, head tipped a little to her left.

Params: expression (a key of chibi.EXPRESSIONS, default cheerful), bust=1 (cut at the
hips, for a portrait), check=1 (fail when over budget or outside 2.5 to 3 heads).
"""

import math

from mathutils import Vector

import chibi
import gab
from chibi import Z, lerp, rot, smoothstep

BUST = bool(int(gab.param("bust", 0)))

C = {
    "skin": "#e8b48c", "blush": "#ee8f80", "hair": "#5a3a2a", "brow": "#3e261a", "iris": "#2f5a3a",
    "straw": "#e6c072", "ribbon": "#c0443a", "smock": "#5f8f4a", "cream": "#efe2c0",
    "glove": "#c98a4a", "boot": "#4a3a30", "boot_cuff": "#7a5a40", "sole": "#3a2c24",
    "wood": "#8a5a3a", "steel": "#b8c0c8",
}

SPEC = {
    "H": 0.30, "heads": 2.7, "chin_out": 0.22, "skin": C["skin"], "blush": C["blush"],
    "face": {"expression": gab.param("expression", "cheerful"), "iris": C["iris"], "brow": C["brow"]},
}

fig = chibi.Figure(SPEC)
H, W, HC = fig.H, fig.W, fig.HC

# ── 1, 2: head and face ──
head = fig.head()
fig.face()

# ── 3: hair: a cap and five locks ──


def hairline(a):
    """The hair's edge, a degrees round from the front: a fringe over the brows, down over
    the ears at the sides, to the nape at the back."""
    return lerp(lerp(40.0, -22.0, smoothstep(26.0, 74.0, a)), -50.0, smoothstep(100.0, 175.0, a))


cap = fig.cap(hairline, C["hair"])
locks = []
for k, (a0, a1, w) in enumerate(((-30, -38, 0.21), (-2, -8, 0.23), (28, 34, 0.21))):
    locks.append(fig.lock(f"fringe_{k}", [fig.dir(a0 * 0.8, 66), fig.dir(a0 * 0.95, 55), fig.dir(lerp(a0, a1, 0.4), 45),
                                          fig.dir(lerp(a0, a1, 0.8), 38), fig.dir(a1, 33.0)],
                          [w * W, w * W, w * 0.95 * W, w * 0.62 * W, 0.0], 0.05 * W, C["hair"], lift=1.075))
for side in (-1, 1):
    locks.append(fig.lock(f"side_lock_{side}", [fig.dir(side * 70, 26), fig.dir(side * 74, 8), fig.dir(side * 74, -10),
                                                fig.dir(side * 70, -24), fig.dir(side * 64, -34)],
                          [0.14 * W, 0.14 * W, 0.12 * W, 0.08 * W, 0.0], 0.06 * W, C["hair"], lift=1.06))

# ── 4: the sun hat: a low dome with a ribbon band and a wide straw brim that dips at the
#       sides and lifts a little at the front, tipped back 12 degrees ──
HAT_BASE = HC + Vector((0.0, 0.008, 0.070))
hat = fig.hat(HAT_BASE, [(0.0, 1.0), (0.034, 1.0), (0.072, 0.95), (0.100, 0.80), (0.114, 0.50), (0.120, 0.0)],
              0.160, C["straw"], band=C["ribbon"],
              brim=[(0.00, 0.006), (0.94, 0.003), (0.97, -0.010)], r_out=0.86 * W,
              brim_lift=lambda th, r: (0.030 * max(0.0, -math.sin(th)) - 0.026 * math.cos(th) ** 2) * r ** 1.6,
              tilt=chibi.about(HAT_BASE, rot(x=-12)))

# hidden faces: the skull under the cap, the hair under the hat
chibi.cull(head, lambda co: fig.angles(co)[1] > hairline(fig.angles(co)[0]) + 3.0 or fig.angles(co)[1] < -80.0)
for obj in [cap] + locks:
    chibi.cull(obj, lambda co: hat.covers(co, 0.010))

# ── 5, 6: the smock, a round collar and the neck ──
fig.bell([(0.30 * H, 0.196, 0.178, 0.0), (0.180, 0.194, 0.174, 0.0), (0.265, 0.180, 0.160, 0.0),
          (0.350, 0.160, 0.142, 0.0), (0.420, 0.144, 0.128, 0.004), (0.482, 0.098, 0.090, 0.008)],
         C["smock"], C["cream"], 0.030, waves=4, wave_amp=0.03, front_rise=0.036, segs=20)
fig.neck(0.052, 0.452, taper=1.2)
collar = chibi.tube("collar", [Vector((0.092 * math.cos(t), 0.006 + 0.084 * math.sin(t), 0.496))
                               for t in [2 * math.pi * k / 16 for k in range(16)]],
                    [(0.030, 0.024)] * 16, sides=6, up=Z, closed=True)
fig.add(collar, "collar", C["cream"])

# ── 7 to 10: arms and garden-glove mittens; her right hand holds the trowel ──
GRIP = Vector((-0.186, -0.080, 0.262))
fig.arm(-1, [(-0.116, 0.0, 0.440), (-0.192, 0.010, 0.350), GRIP + Vector((0.006, 0.026, 0.016))], 0.042,
        C["smock"], C["cream"])
fig.arm(1, [(0.116, 0.0, 0.440), (0.232, 0.040, 0.338), (0.194, -0.026, 0.262)], 0.042, C["smock"], C["cream"])
fig.mitten(-1, GRIP, fingers=(0.1, -0.6, -1.0), back=(-1.0, 0.0, 0.0), size=(0.068, 0.060, 0.080),
           colour=C["glove"], curl=0.1)
fig.mitten(1, (0.188, -0.034, 0.250), fingers=(-0.3, -0.5, -1.0), back=(1.0, 0.0, 0.2), size=(0.070, 0.058, 0.078),
           colour=C["glove"])
TOOL = Vector((0.0, -0.75, -0.66)).normalized()   # the trowel points forward and down
handle = chibi.tube("trowel_handle", [GRIP - TOOL * 0.040, GRIP + TOOL * 0.050], [0.013, 0.012], sides=6)
fig.add(handle, "prop", C["wood"], smooth=50)
blade = chibi.teardrop("trowel_blade", GRIP + TOOL * 0.046, TOOL, 0.095, 0.058, 0.012, Vector((1.0, 0.0, 0.0)),
                       taper=0.55, segs=6, rows=3)
fig.add(blade, "prop", C["steel"])

# ── 11, 12: boots, toes out ──
for side in (-1, 1):
    fig.boot(side, (0.074 * side, -0.042, 0.0), 14 * side, 0.112, 0.42 * H, 0.090, C["boot"], C["boot_cuff"],
             C["sole"], cuff_h=0.024, sole_h=0.015, toe=0.66, toe_h=0.50, shin=0.42)

# ── pose: the head tips to her left and a little up, as whole parts ──
fig.pose(fig.head_group, chibi.about(Vector((0.0, 0.030, fig.chin + 0.020)), rot(y=-6) @ rot(x=-6)))

fig.finish("gardener_bust" if BUST else "gardener", bust_cut=0.28 if BUST else None,
           check=bool(int(gab.param("check", 0))))
