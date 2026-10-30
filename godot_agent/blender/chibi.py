"""Chibi toon characters for Blender build scripts: `import chibi` beside `import gab`.

A figure of about a dozen smooth parts in flat colours, 2.5 to 3 heads tall, made for a
two-tone toon shader (the kit's `toon` material; see the skill). A build script writes a
SPEC, builds the parts in order and finishes:

    import chibi
    SPEC = {"H": 0.32, "heads": 2.7, "skin": "#ebb48e", "blush": "#ef8f84",
            "face": {"expression": "cheerful", "iris": "#4a2410", "brow": "#7a2c14"}}
    fig = chibi.Figure(SPEC)
    head = fig.head()                       # 1  skull and face, one smooth blob
    fig.face()                              # 2  eight flat shapes and a button nose
    cap = fig.cap(hairline, hair)           # 3  hair: a cap plus a few locks (fig.lock)
    hat = fig.hat(...)                      # 4  headgear (or fig.hood(...))
    fig.bell(rings, coat, hem_band, ...)    # 5  the coat, lofted from the hem up
    fig.neck(...); fig.arm(...); fig.mitten(...); fig.boot(...)
    fig.pose(fig.head_group, chibi.about(neck, chibi.rot(x=-10)))   # whole parts, never bent
    fig.finish("hero", check=True)

The look
- H is the head's height chin to crown, W = 1.08 H its width; the crown stands at
  spec["heads"] x H. Face parameters are in W (across) and H (up from the chin).
- Every part is one closed, smooth surface in one flat swatch, or two split along an edge
  loop (a hem band, a cuff, a hat band). No buttons, buckles, stitching or folds: detail
  that small turns to noise at game scale and under a two-tone light.
- No light is baked in. The vertex colour is the swatch (linear); the vertex alpha is the
  shade class the toon shader reads: CLOTH 1.0, SKIN 0.75, PAINT 0.5 (face shapes), UNLIT
  0.25 (eye highlights, lenses).
- The face is eight kinds of flat shape (eye white, iris, highlight, upper lid, brow,
  mouth, blush, plus a ball nose) cut from the head's own triangles (gab.Decals), so they
  lie exactly on what the renderer draws, layered by small lifts (LIFT). A face is a few
  numbers: start from FACE_DEFAULTS, pick an EXPRESSIONS entry, set the iris and brow
  colours, then adjust single keys (see Figure.face).
- The head, the face shapes and the hair (cap and locks) share one normal field (the
  head's analytic superellipsoid, turned toward the face's forward axis), written as
  custom normals, so the toon terminator crosses skin, features and hair as one calm curve.
- Hidden faces are culled (skin under hair, hair under a hat or hood, sleeve ends in the
  coat), so the triangles go where they show: about 3,800 for a figure, under 3,000 for
  its portrait bust (BUDGET).

Look at a figure with `godot-agent asset review <project> <model> --turnaround --focus <y>`
(the finish report prints the face's height for --focus): front, 3/4, side and back in the
game's materials, the silhouettes, and the face close up.
"""

import math
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix, Vector

import gab
from gab import (FWD, X, Y, Z, about, blob, catmull, clamp01, crease, cull, ellipse, lerp, lobes,  # noqa: F401
                 loft, mix_hex, resample, ring, rot, smoothstep, superellipse, superellipsoid_radius,
                 teardrop, transform, tube)

# Shade classes (vertex alpha), read by the toon shader
CLOTH, SKIN, PAINT, UNLIT = 1.0, 0.75, 0.5, 0.25

# The face swatches a roster shares, so its characters read as one family
FACE = {"white": "#fbf3e6", "lid": "#2b1a14", "highlight": "#fffaf0", "mouth": "#6a2420"}

# Lifts of the face layers above the skin at H = 0.32 (scaled with H): each step clears
# the Compatibility renderer's depth precision at a strategy camera's farthest zoom
LIFT = {"blush": 0.0006, "white": 0.0012, "brow": 0.0012, "mouth": 0.0012, "iris": 0.0024, "lid": 0.0036,
        "highlight": 0.0044}

BUDGET = {"full": 4000, "bust": 3000}

# A friendly, open face; every key Figure.face reads. Sizes across are in W, heights in H.
FACE_DEFAULTS = {
    "eye_u": 0.18, "eye_v": 0.40, "eye_w": 0.22, "eye_h": 0.23,          # eye centre and size
    "iris_w": 0.155, "iris_h": 0.19, "iris_cover": 0.17, "look": (0.0, 0.0), "highlight": 0.05,
    "lid_cut": 0.12, "lid_w": 0.030, "lid_flick": 0.006,                  # upper lid
    "brow_up": 0.085, "brow_len": 0.16, "brow_w": 0.036, "brow_tilt": 5.0, "brow_raise": 0.0,
    "brow_arch": 0.008,
    "nose": (0.072, 0.052, 0.027), "nose_v": 0.29,                        # width, height, stand-out
    "mouth": "smile", "mouth_v": 0.228, "mouth_w": 0.15, "mouth_depth": 0.085, "mouth_shift": 0.0,
    "blush": (0.12, 0.065), "blush_u": 0.29, "blush_v": 0.235,
    "iris": "#4a2c1c", "brow": "#4a2c1c",
}

# Expressions: only the keys that carry the mood. Each reads at 64 px and from a camera above.
EXPRESSIONS = {
    "friendly": {},
    "cheerful": {"mouth": "grin", "mouth_depth": 0.065, "brow_tilt": 9.0, "brow_raise": 0.010,
                 "brow_arch": 0.012, "lid_cut": 0.12, "lid_flick": 0.004},
    "composed": {"mouth": "smirk", "brow_tilt": 3.0, "brow_raise": 0.0, "brow_arch": 0.004, "lid_cut": 0.14,
                 "lid_flick": 0.008},
    "determined": {"mouth": "line", "mouth_w": 0.12, "brow_tilt": 16.0, "brow_raise": -0.012,
                   "brow_arch": 0.0, "brow_w": 0.042, "lid_cut": 0.22},
    "surprised": {"mouth": "o", "brow_tilt": 0.0, "brow_raise": 0.026, "brow_arch": 0.018, "lid_cut": 0.02,
                  "iris_cover": 0.05},
    "worried": {"mouth": "line", "mouth_w": 0.10, "brow_tilt": -14.0, "brow_raise": 0.012, "brow_arch": 0.004,
                "lid_cut": 0.16},
    "sleepy": {"mouth": "smile", "mouth_w": 0.11, "brow_tilt": 2.0, "brow_raise": 0.004, "lid_cut": 0.30,
               "iris_cover": 0.30, "look": (0.0, -0.01)},
}


def mesh(name, verts, faces, recalc=True):
    """An object from vertex and face lists, every face turned outward."""
    return gab.mesh_object(name, verts, faces, recalc=recalc)


def face_spec(face: dict) -> dict:
    """A complete face: FACE_DEFAULTS, then face["expression"]'s keys, then face's own."""
    mood = face.get("expression")
    if mood is not None and mood not in EXPRESSIONS:
        raise ValueError(f"unknown expression {mood!r}; use one of {sorted(EXPRESSIONS)}")
    return {**FACE_DEFAULTS, **EXPRESSIONS.get(mood, {}), **face}


class Figure:
    """One chibi under construction: its spec, its parts by name and its head frame.

    spec keys: H (head height, metres), heads (boot sole to crown, in heads), skin, blush,
    face (see face()), and optionally chin_out (how far the chin comes forward, 0..0.3),
    material (the toon material's name, default "toon"), palette (a .gpl path, res:// or
    relative to the project, every swatch must be in it), budget ({"full", "bust"}
    triangles), heads_range ((min, max) heads), face_swatches (overrides FACE).
    """

    def __init__(self, spec):
        self.spec = spec
        self.H = spec["H"]
        self.W = 1.08 * self.H
        self.chin = (spec["heads"] - 1.0) * self.H
        self.HC = Vector((0.0, 0.0, self.chin + self.H / 2))
        self.R = Vector((self.W / 2, 0.95 * self.H / 2, self.H / 2))
        self.HEAD_POWER = 2.2
        self.parts = []           # (object, part label)
        self.head_group = []      # parts that move with the head
        self.normal_group = []    # parts lit by the head's normal field (head, face, hair)
        self.head_matrix = Matrix.Identity(4)
        self.head_plan = Matrix.Identity(4)
        self.swatches = {}        # hex -> parts using it
        self.mat = gab.material(spec.get("material", "toon"))
        self.k = self.H / 0.32
        self.head_obj = None
        self.surf = None
        self.decals = None
        self.notes = []

    # ── painting and bookkeeping ──

    def add(self, obj, part, colour, shade=CLOTH, smooth=80.0, head=False, mat=None):
        """Registers a part: one material, smooth shading (edges sharper than `smooth`
        degrees stay hard; None for flat), and its colour: a swatch, or a function of a
        face's centre and index giving (swatch, shade) per face, so bands sit on edge loops.
        head: the part moves with the head when it is posed."""
        gab.assign(obj, mat or self.mat)
        if smooth is None:
            gab.flat(obj)
        else:
            gab.smooth(obj, smooth)
        me = obj.data
        layer = gab._color_layer(me)
        for poly in me.polygons:
            sw, sh = colour(poly.center, poly.index) if callable(colour) else (colour, shade)
            self.swatches.setdefault(sw, set()).add(part)
            c = gab.rgb(sw)
            for li in poly.loop_indices:
                layer.data[li].color = (c[0], c[1], c[2], sh)
        self.parts.append((obj, part))
        if head:
            self.head_group.append(obj)
        return obj

    def pose(self, objs, m):
        """Moves whole parts by one 4x4 transform (never bending them)."""
        for o in objs:
            transform(o, m)
        if self.head_obj is not None and self.head_obj in objs:
            self.head_matrix = m @ self.head_matrix

    def everything(self, but=()):
        """Every part's object except those labelled in `but`."""
        return [o for o, part in self.parts if part not in but]

    # ── the head and its normal field ──

    def head_point(self, d, scale=1.0):
        """The head's surface along unit direction d from its centre."""
        k = superellipsoid_radius(d, self.HEAD_POWER)
        mx, my, mz = self.head_shape(d)
        return self.HC + Vector((d.x * self.R.x * mx * k, d.y * self.R.y * my * k, d.z * self.R.z * mz * k)) * scale

    def head_shape(self, d):
        jaw = smoothstep(-0.40, -0.95, d.z)                       # narrows below the mouth
        cheek = math.exp(-((d.z + 0.38) / 0.26) ** 2) * smoothstep(0.05, -0.75, d.y)
        mx = 1.0 - 0.08 * jaw + 0.045 * cheek                     # full cheeks
        if d.y < 0:   # a flat face, its lower half (mouth and chin) carried forward by chin_out,
            front = smoothstep(0.0, -0.9, d.y)   # so the mouth still faces a camera looking down
            low = math.exp(-((d.z + 0.85) / 0.25) ** 2)
            my = 1.0 - 0.10 * front + self.spec.get("chin_out", 0.0) * front * low
        else:
            my = 1.0 + 0.04 * d.y
        return (mx, my, 1.0)

    def head_normal(self, p):
        """The calm normal field at a point in the head's rest frame: the superellipsoid's
        gradient, turned toward the face's forward axis on the face front."""
        d = p - self.HC
        q = self.HEAD_POWER
        g = Vector(tuple(math.copysign(abs(d[i] / self.R[i]) ** (q - 1), d[i]) / self.R[i] for i in range(3)))
        n = g.normalized()
        n = n.lerp(FWD, 0.35 * smoothstep(0.2, 0.8, n.dot(FWD))).normalized()
        return n

    # Ring latitudes of the head: close (6 degrees) from the chin to the cheeks, where the jaw
    # makes the outline, 8 to 9 through the eyes, and wide over the skull, which hair covers
    HEAD_LATS = [-84, -78, -72, -66, -60, -54, -48, -42, -36, -30, -23, -15, -7, 2, 12, 24, 38, 54, 72]

    def head(self, segs=30, lats=None):
        """Part 1: the skull and face as one smooth blob, triangulated, plus the surfaces
        the face's decals are cut from."""
        obj = blob("head", self.HC, self.R, segs=segs, power=self.HEAD_POWER, shape=self.head_shape,
                   lats=lats or self.HEAD_LATS)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        self.add(obj, "head", self.spec["skin"], SKIN, head=True)
        self.head_obj = obj
        self.normal_group.append(obj)
        self.surf = gab.FrontSurface(obj)
        self.decals = gab.Decals(obj, self.head_normal)
        return obj

    # ── part 2: the face ──

    def uv(self, u, v):
        """Face coordinates (u across in W from the centre line, v up from the chin in H)
        to the (x, z) the decals are cast from."""
        return (self.HC.x + u * self.W, self.chin + v * self.H)

    def face_focus(self):
        """The face's centre where the finished model has it, in Godot's axes (y up): the
        --focus height for a turnaround's face close-ups."""
        return (self.head_matrix @ Vector((self.HC.x, self.HC.y, self.chin + 0.45 * self.H))).z

    def face(self):
        """Eye whites, irises, highlights, upper lids, brows, a nose ball, one mouth and two
        blush ovals: eight shape kinds, 13 pieces. spec["face"] is merged over
        FACE_DEFAULTS and its "expression" (a key of EXPRESSIONS); keys (sizes in W,
        heights in H):

        eye_u, eye_v      eye centre: across from the centre line, up from the chin
        eye_w, eye_h      eye white (an egg, fuller below)
        iris_w, iris_h    iris ellipse, clipped to the white; iris (colour)
        iris_cover        share of the iris under the upper lid (0.17: never a stare)
        look (u, v)       moves both irises
        highlight         one glint's diameter, up and to the viewer's left in both eyes
        lid_cut           how much of the white's top the lid hides (0 open .. 0.35 heavy)
        lid_w, lid_flick  lid thickness; the flick lifting its outer tail
        brow_up, brow_raise, brow_len, brow_w, brow_tilt (degrees, + lifts the outer end),
        brow_arch         a tapered arc floating clear of the lid; brow (colour)
        nose (w, h, out), nose_v   the button nose: a ball in skin mixed toward blush
        mouth             line, smile, smirk (one stroke), grin (an open D), o
        mouth_v, mouth_w, mouth_depth (grin), mouth_shift (off the centre line)
        blush (w, h), blush_u, blush_v   flat ovals, half way from skin to spec["blush"]
        """
        F, W = face_spec(self.spec["face"]), self.W
        swatch = {**FACE, **self.spec.get("face_swatches", {})}
        lift = {k: v * self.k for k, v in LIFT.items()}

        def decal(name, cells, layer, colour, shade):
            obj = self.decals.decal(name, cells, lift[layer])
            self.add(obj, "face", colour, shade, smooth=89, head=True)
            self.normal_group.append(obj)

        hw, hh = F["eye_w"] * W / 2, F["eye_h"] * W / 2
        top = hh * (1 - 2 * F["lid_cut"])        # the white's top after the lid's cut

        def white_point(th):
            c, sn = superellipse(th, 2.4)
            return Vector((c * hw * (1.0 - 0.07 * sn), min(sn * hh, top)))   # an egg, fuller below

        white = [white_point(2 * math.pi * k / 16) for k in range(16)]
        lid_mid = F["lid_w"] * W
        iw, ih = F["iris_w"] * W / 2, F["iris_h"] * W / 2
        # the lid covers the iris's top `iris_cover` (so the eye never stares); lid_cut then
        # sets how much white shows below it
        lid_edge = top - lid_mid / 2
        iris_c = Vector((F["look"][0] * W, lid_edge + (2 * F["iris_cover"] - 1) * ih + F["look"][1] * W))
        self.notes.append(f"eyes: the lid covers {(iris_c.y + ih - lid_edge) / (2 * ih):.0%} of the iris, "
                          f"{(iris_c.y - ih + hh) / W:.3f} W of white below it")
        iris = []
        for k in range(14):   # an ellipse, clipped to the white
            d = Vector((math.cos(2 * math.pi * k / 14), math.sin(2 * math.pi * k / 14)))
            r = min(1.0 / math.sqrt((d.x / iw) ** 2 + (d.y / ih) ** 2),
                    gab.ray_to_outline(iris_c, d, white) - 0.005 * W)
            iris.append(iris_c + d * r)
        for side in (-1, 1):
            ex, ez = self.uv(side * F["eye_u"], F["eye_v"])

            def at(p):
                return (ex + p.x, ez + p.y)

            decal(f"eye_white_{side}", [[at(p) for p in white]], "white", swatch["white"], PAINT)
            decal(f"iris_{side}", [[at(p) for p in iris]], "iris", F["iris"], PAINT)
            # one highlight, up and to the viewer's left in both eyes
            hl = at(iris_c + Vector((-0.35 * iw, 0.35 * ih)))
            rr = F["highlight"] * W / 2
            decal(f"highlight_{side}", [ellipse(hl, rr, rr, 10)], "highlight", swatch["highlight"], UNLIT)
            # the upper lid: along the white's top outline, half inside it, thickest outward
            pts = [white_point(math.radians(lerp(172, 8, k / 7))) for k in range(8)]
            if side < 0:
                pts.reverse()            # the inner (nose) end first
            tail = (pts[-1] - pts[-2]).normalized()
            pts.append(pts[-1] + tail * 0.015 * W + Vector((0, F["lid_flick"] * W)))
            widths = [W * F["lid_w"] / 0.030 * (lerp(0.014, 0.034, k / 8) + 0.006 * math.sin(math.pi * k / 8))
                      for k in range(9)]
            widths[-1] *= 0.55
            lo, up = gab.stroke([at(p) for p in pts], widths)
            decal(f"lid_{side}", gab.strip_cells(lo, up), "lid", swatch["lid"], PAINT)
            # the brow: a tapered arc with round ends, floating clear of the lid and the hair
            L = F["brow_len"] * W
            bz = ez + hh + (F["brow_up"] + F["brow_raise"]) * W
            tilt = math.tan(math.radians(F["brow_tilt"]))
            cps, bw = [], []
            for t in gab.cosine_steps(8):
                cps.append((ex + side * ((t - 0.5) * L + 0.01 * W),
                            bz + F["brow_arch"] * W * math.sin(math.pi * t) + (t - 0.5) * L * tilt))
                bw.append(W * lerp(F["brow_w"], 0.016, t))
            lo, up = gab.stroke(cps, bw)
            decal(f"brow_{side}", gab.strip_cells(lo, up), "brow", F["brow"], PAINT)
            # blush: a flat oval, half way from the skin to the blush colour
            decal(f"blush_{side}", [ellipse(self.uv(side * F["blush_u"], F["blush_v"]), F["blush"][0] * W / 2,
                                            F["blush"][1] * W / 2, 10)],
                  "blush", mix_hex(self.spec["skin"], self.spec["blush"], 0.5), SKIN)
        decal("mouth", self._mouth(F), "mouth", swatch["mouth"], PAINT)
        # the button nose: a ball standing out of the face, lit with its own normals
        nw, nh, out = (v * W for v in F["nose"])
        nx, nz = self.uv(0.0, F["nose_v"])
        at_n = self.surf.wrap(nx, nz, 0.0) + FWD * (out - nw * 0.42)
        nose = blob("nose", at_n, (nw / 2, nw * 0.42, nh / 2), segs=8, rows=4, lats=(-40, 15))
        self.add(nose, "face", mix_hex(self.spec["skin"], self.spec["blush"], 0.25), SKIN, head=True)

    def _mouth(self, F):
        """The mouth's cells: a line (line, smile, smirk) or one filled shape (grin, o)."""
        W = self.W
        mx, mz = self.uv(0.0, F["mouth_v"])
        hw = F["mouth_w"] * W / 2
        kind = F["mouth"]
        if kind in ("line", "smile", "smirk"):
            # a smirk lifts one corner; mouth_shift moves the whole line off the centre
            shift = F["mouth_shift"] * W
            pts = []
            for t in gab.cosine_steps(10):
                u = 2 * t - 1
                z = {"line": 0.0, "smile": 0.020 * W * u * u,
                     "smirk": 0.010 * W * u * u + 0.014 * W * (u + 1) / 2}[kind]
                pts.append((mx + shift + u * hw, mz + z))
            lo, up = gab.stroke(pts, [0.030 * W] * len(pts))
            return gab.strip_cells(lo, up)
        if kind == "grin":   # an open D, no teeth: a straight top over a round bowl
            depth = F["mouth_depth"] * W
            out = [(mx - hw, mz), (mx + hw, mz)]
            for k in range(1, 12):  # the bowl, right to left
                a = math.pi * k / 12
                out.append((mx + math.cos(a) * hw, mz - math.sin(a) ** 0.8 * depth))
            return [out]
        if kind == "o":
            return [ellipse((mx, mz), 0.038 * W, 0.045 * W)]
        raise ValueError(f"unknown mouth {kind!r}; use line, smile, smirk, grin or o")

    # ── part 3: hair ──

    def cap(self, hairline, colour, segs=20, rows=5, scale=1.07):
        """The hair cap: a shell over the head (scale x the head), its edge on a hairline.
        hairline(a) is the edge's latitude in degrees at angle a degrees round from the
        face's front (0 front, 180 back); the edge tucks into the skin, so it reads thick.
        It is lit by the head's own normal field, so hair and face share one calm terminator."""
        obj = self.shell("hair_cap", hairline, colour, scale, segs, rows, "hair")
        self.normal_group.append(obj)
        return obj

    def shell(self, name, edge, colour, scale, segs, rows, part, lining=None, rim=0.0):
        """A shell over the head (a hair cap, a close helmet) whose lower edge runs along
        edge(a), a latitude in degrees at angle a round from the front. Without a lining the
        edge tucks into the skin; with one, a band `rim` deep (in head scale) turns from the
        edge in toward the face and is painted `lining`. Round the sides and back the band
        turns up into the shell instead, so it never folds out below the edge."""
        verts, faces = [], []
        n_under = 1
        for i in range(n_under + rows):
            for k in range(segs):
                th = -math.pi / 2 + 2 * math.pi * k / segs
                a = abs(math.degrees(math.atan2(math.cos(th), -math.sin(th))))
                lat0 = math.radians(edge(a))
                if i < n_under:
                    if lining is None:
                        lat, sc = lat0 + math.radians(-3.0), 0.995
                    else:   # the lining: turned toward the face at the front, up and in elsewhere
                        side = smoothstep(55.0, 100.0, a)
                        lat = lat0 + math.radians(lerp(-7.0, 6.0, side))
                        sc = scale - rim * lerp(1.0, 0.55, side)
                else:
                    t = (i - n_under) / rows
                    lat, sc = lerp(lat0, math.radians(88), t ** 1.15), scale
                d = Vector((math.cos(lat) * math.cos(th), math.cos(lat) * math.sin(th), math.sin(lat)))
                verts.append(self.head_point(d, sc))
        verts.append(self.HC + Z * self.R.z * scale)
        top = len(verts) - 1
        last = n_under + rows - 1
        for i in range(last):
            for k in range(segs):
                k1 = (k + 1) % segs
                faces.append((i * segs + k, i * segs + k1, (i + 1) * segs + k1, (i + 1) * segs + k))
        faces += [(last * segs + k, last * segs + (k + 1) % segs, top) for k in range(segs)]
        obj = mesh(name, verts, faces)
        if lining is None:
            return self.add(obj, part, colour, head=True)
        return self.add(obj, part, lambda c, i: (lining, CLOTH) if i < segs else (colour, CLOTH), head=True)

    def lock(self, name, path_dirs, widths, thick, colour, lift=1.07, sides=8, per=1, part="hair", head=True,
             calm=True):
        """A clump of hair lying on the head (a fringe clump, a side lock): path_dirs are
        directions from the head's centre (root to tip; fig.dir(a, lat) gives them), widths
        its width along them (0 at the tip); it is flat against the head, `thick` deep.
        calm: lit by the head's normal field (like the cap), so a flat clump never shows a
        creased edge under the toon light. Three to five locks read better than many thin ones."""
        pts = [self.head_point(Vector(d).normalized(), lift) for d in path_dirs]
        pts = catmull(pts, per)
        nrms = [((p - self.HC).normalized()) for p in pts]
        # the widths, resampled onto the spline's points
        m = len(widths) - 1
        ws = []
        for i in range(len(pts)):
            f = i / (len(pts) - 1) * m
            j = min(int(f), m - 1)
            ws.append(lerp(widths[j], widths[j + 1], f - j))
        radii = [(w / 2, thick / 2) if w > 0 else 0.0 for w in ws]
        obj = tube(name, pts, radii, sides=sides, cap0=True, normals=nrms)
        if calm:
            self.normal_group.append(obj)
        return self.add(obj, part, colour, head=head)

    def angles(self, co):
        """(a, lat) of a rest-frame point: degrees round from the face's front, and up."""
        d = co - self.HC
        d = Vector((d.x / self.R.x, d.y / self.R.y, d.z / self.R.z)).normalized()
        return abs(math.degrees(math.atan2(d.x, -d.y))), math.degrees(math.asin(max(-1.0, min(1.0, d.z))))

    def dir(self, a, lat):
        """A unit direction from the head's centre: a degrees round from the front toward
        the model's left (+X), lat degrees up."""
        th = -math.pi / 2 + math.radians(a)
        la = math.radians(lat)
        return Vector((math.cos(la) * math.cos(th), math.cos(la) * math.sin(th), math.sin(la)))

    # ── part 4: headgear ──

    def hat(self, base, crown, r_in, colour, band=None, band_rows=1, brim=None, r_out=None, brim_lift=None,
            brim_colour=None, segs=20, brim_segs=36, squash=0.93, brim_squash=0.94, brim_inner=0.99,
            lean=None, tilt=None, brim_smooth=40.0, name="hat"):
        """An oversized signature hat: a crown lofted from a profile, a band on its lowest
        rows, and a brim swept round as a closed section, every piece turned by `tilt`.

        base       the crown's bottom centre (in the head's rest frame; the hat moves with it)
        crown      [(h, f)]: rings at height h above base, radius f x r_in; a last entry with
                   f 0 closes the top in one point (a dome; at the previous height, a flat lid)
        squash     the crown's depth as a share of its width; lean(h) moves the ring at height
                   h back (+Y), so the top can lean
        band       the band's swatch, on the crown's lowest band_rows rows of faces
        brim       [(r_share, z)]: the brim's section, 0 at the crown, 1 at r_out (a thin
                   wedge such as [(0, 0.006), (0.94, 0.003), (0.97, -0.010)]); None: no brim
        brim_lift(th, r_share) -> z: rolls the brim up or droops it (th as ring(): -90
                   degrees is the front)
        tilt       a 4x4 for every piece, e.g. chibi.about(base, chibi.rot(x=-15))

        Returns a namespace with crown, brim, base, tilt, up (the crown's axis) and
        covers(co, margin): whether a rest-frame point is under the hat, for cull()."""
        base = Vector(base)
        tilt = tilt if tilt is not None else Matrix.Identity(4)

        def crown_point(th, h, f):
            dy = lean(h) if lean else 0.0
            return base + Vector((math.cos(th) * r_in * f, math.sin(th) * r_in * f * squash + dy, h))

        n = segs
        verts = [crown_point(2 * math.pi * k / n, h, f) for h, f in crown[:-1] for k in range(n)]
        verts.append(crown_point(0.0, *crown[-1]))
        faces = [(i * n + k, i * n + (k + 1) % n, (i + 1) * n + (k + 1) % n, (i + 1) * n + k)
                 for i in range(len(crown) - 2) for k in range(n)]
        last = (len(crown) - 2) * n
        faces += [(last + k, last + (k + 1) % n, len(verts) - 1) for k in range(n)]
        crown_obj = mesh(f"{name}_crown", verts, faces)
        transform(crown_obj, tilt)
        self.add(crown_obj, "hat", lambda c, i: (band, CLOTH) if band and i < n * band_rows else (colour, CLOTH),
                 head=True)
        brim_obj = None
        if brim:
            rings = []
            for r_share, z0 in brim:
                def at(th, r_share=r_share, z0=z0):
                    return z0 + (brim_lift(th, r_share) if brim_lift else 0.0)
                rr = lerp(r_in * brim_inner, r_out, r_share)
                rings.append(ring(base, rr, rr * brim_squash, brim_segs, z_fn=at))
            m = brim_segs
            verts = [p for r_ in rings for p in r_]
            faces = []
            P = len(rings)
            for i in range(P):   # the section is closed: the last ring joins the first
                i1 = (i + 1) % P
                for k in range(m):
                    k1 = (k + 1) % m
                    faces.append((i * m + k, i * m + k1, i1 * m + k1, i1 * m + k))
            brim_obj = mesh(f"{name}_brim", verts, faces)
            transform(brim_obj, tilt)
            self.add(brim_obj, "hat", brim_colour or colour, smooth=brim_smooth, head=True)
        up = (tilt.to_3x3() @ Z).normalized()
        at = tilt @ base
        return SimpleNamespace(crown=crown_obj, brim=brim_obj, base=base, tilt=tilt, up=up, at=at,
                               covers=lambda co, margin=0.010: (co - at).dot(up) > margin)

    FLARE_LAT = -15.0   # below this "latitude" a hood leaves the head and walks down to the shoulders

    def hood(self, name, opening, colour, lining, scale, shoulder, segs=32, thick=0.05, depth=24.0,
             lats=(80, 66, 52, 40, 28, 16, 5, -6), flare=(-35, -55, -73, -90), drape=None):
        """A deep hood that runs from the crown down into the shoulders as one smooth piece,
        so no neck shows between hood and mantle, with a face opening and a rolled lining.

        Above FLARE_LAT the hood is the head's shape at `scale`; below it the "latitudes"
        -15 .. -90 walk a smooth curve from that ring down to the `shoulder` ring (rx, ry, z,
        dy in the body's frame, where the posed hood lands: plan_head first). opening(a) is
        the opening's edge, a latitude at angle a degrees round from the front; -90 closes
        the hood down to the shoulders there. The lining is one band `thick` (head scale)
        deep, rolled in round the edge and running `depth` degrees up inside. drape(a, u)
        gives (out, down) in metres: how far the flare is pushed outward and lowered at
        angle a and flare share u, to shape the back. Rings sit at the same latitudes all
        round (`lats`, `flare`), so the outline stays even."""
        to_rest = self.head_plan.inverted()
        rx, ry, z_s, dy = shoulder
        fl = self.FLARE_LAT

        def point(a, lat, sc):
            """a: degrees round from the front toward +X (signed); lat: as above."""
            if lat >= fl:
                return self.head_point(self.dir(a, lat), sc)
            u = (fl - lat) / (fl + 90.0)
            p0 = self.head_point(self.dir(a, fl), sc)
            down = (p0 - self.head_point(self.dir(a, fl + 6.0), sc)).normalized()
            th = -math.pi / 2 + math.radians(a)
            k = sc / scale
            p1 = to_rest @ Vector((rx * k * math.cos(th), dy + ry * k * math.sin(th), z_s))
            ctrl = p0 + down * (p1 - p0).length * 0.45
            p = p0 * (1 - u) ** 2 + ctrl * 2 * u * (1 - u) + p1 * u * u
            if drape:
                out = Vector((p.x - self.HC.x, p.y - self.HC.y, 0.0)).normalized()
                push, drop = drape(abs(a), u)
                p = p + (out * push - Z * drop) * k
            return p

        def normal(a, lat):
            e = 1.5
            pa, pb = point(a + e, lat, scale) - point(a - e, lat, scale), point(a, lat + e, scale) - point(a, lat - e, scale)
            n = pb.cross(pa).normalized()
            p = point(a, lat, scale)
            return n if n.dot(p - Vector((self.HC.x, self.HC.y, p.z))) > 0 else -n

        t = thick * self.R.x
        verts, cols, faces, lining_faces = [], [], [], []
        nominal = sorted(set(lats) | set(flare) | {fl}, reverse=True)
        verts.append(self.HC + Z * self.R.z * scale)   # the crown
        for k in range(segs):
            a = 360.0 * k / segs
            a = a - 360.0 if a > 180.0 else a
            edge = max(-90.0, opening(abs(a)))
            col = [(edge, len(verts))]
            verts.append(point(a, edge, scale))
            for lat in reversed(nominal):
                if lat > edge + 4.0:
                    col.append((lat, len(verts)))
                    verts.append(point(a, lat, scale))
            col.append((90.0, 0))
            cols.append((a, edge, col))
        for k in range(segs):   # stitch each column to the next by latitude
            A, B = cols[k][2], cols[(k + 1) % segs][2]
            i = j = 0
            while i < len(A) - 1 or j < len(B) - 1:
                if j == len(B) - 1 or (i < len(A) - 1 and A[i + 1][0] <= B[j + 1][0]):
                    tri = (A[i][1], A[i + 1][1], B[j][1])
                    i += 1
                else:
                    tri = (A[i][1], B[j + 1][1], B[j][1])
                    j += 1
                if len(set(tri)) == 3:   # (both columns end on the crown)
                    faces.append(tri)
        # the lining: edge, a rolled lip, then the inner surface up into the hood
        open_cols = [k for k in range(segs) if cols[k][1] > -89.5 or cols[(k - 1) % segs][1] > -89.5
                     or cols[(k + 1) % segs][1] > -89.5]
        band = {}
        for k in open_cols:
            a, edge, col = cols[k]
            n = normal(a, edge)
            e = verts[col[0][1]]
            m = (e - point(a, edge + 3.0, scale)).normalized()
            ids = [col[0][1]]
            for p in (e - n * t * 0.5 + m * t * 0.45, e - n * t, point(a, edge + depth, scale) - normal(a, edge + depth) * t):
                ids.append(len(verts))
                verts.append(p)
            band[k] = ids
        for k in open_cols:
            k1 = (k + 1) % segs
            if k1 not in band:
                continue
            for r in range(3):   # wound like the shell, so the edge they share runs both ways
                lining_faces.append((band[k][r], band[k1][r], band[k1][r + 1], band[k][r + 1]))
        obj = mesh(name, verts, faces + lining_faces, recalc=False)
        # one consistent winding: turn it all over if the shell faces inward
        me = obj.data
        n_outer = len(faces)
        score = sum(p.normal.dot(p.center - Vector((self.HC.x, self.HC.y, min(p.center.z, self.HC.z))))
                    for p in me.polygons[:n_outer])
        if score < 0:
            for p in me.polygons:
                p.flip()
            me.update()
        return self.add(obj, "hood", lambda c, i: (lining, CLOTH) if i >= n_outer else (colour, CLOTH),
                        smooth=100.0, head=True)

    def plan_head(self, pivot, m3):
        """Sets the head's pose (a turn m3 about pivot) before the parts are built, so a part
        that must land on the body (a hood on the shoulders) can be built to meet it."""
        self.head_plan = about(pivot, m3)

    def pose_head(self):
        """Moves the head and everything on it by the planned pose."""
        self.pose(self.head_group, self.head_plan)

    # ── part 5: the coat ──

    def bell(self, rings, colour, band, band_h, waves=0, wave_amp=0.0, front_rise=0.0, segs=24, power=2.15,
             top_cap=False):
        """The coat (a smock, a robe, a dress): lofted superellipse rings (z, rx, ry, dy)
        from the hem up to the neck, the hem rising front_rise at the front so the boots
        show; `band` paints the hem's lowest band (on an edge loop band_h above the hem).
        Soft hem waves fade upward. The top is left open (a collar, a scarf or the head
        closes it) unless top_cap."""
        z_hem = rings[0][0]
        full = [(rings[0][0], rings[0][1], rings[0][2], rings[0][3])]
        full.append((z_hem + band_h,) + tuple(lerp(a, b, band_h / (rings[1][0] - z_hem))
                                             for a, b in zip(rings[0][1:], rings[1][1:])))
        full += rings[1:]
        out = []

        def wave(th, z):
            fade = smoothstep(z_hem + 0.18, z_hem, z)
            return 1.0 + wave_amp * fade * math.cos(waves * th + 0.4) if waves else 1.0

        def rise(th, share):
            return front_rise * share * max(0.0, -math.sin(th)) ** 2

        out.append(ring((0, rings[0][3], z_hem + 0.012), rings[0][1] * 0.86, rings[0][2] * 0.86, segs, power,
                        z_fn=lambda th: rise(th, 1.0)))
        for i, (z, rx, ry, dy) in enumerate(full):
            share = 1.0 if i < 2 else (0.4 if i == 2 else 0.0)
            out.append(ring((0, dy, z), rx, ry, segs, power, shape=lambda th, z=z: wave(th, z),
                            z_fn=lambda th, s=share: rise(th, s)))
        obj = loft("coat", out, cap0=True, cap1=top_cap)

        def paint(c, i):
            return (band, CLOTH) if i < segs * 2 else (colour, CLOTH)

        return self.add(obj, "coat", paint)

    def neck(self, radius, bottom, sides=10, taper=1.0):
        """A short neck from inside the collar up into the head; `taper` widens its foot, so
        it fills the collar it stands in and nothing shows between them."""
        top = self.chin + 0.25 * self.H
        obj = tube("neck", [(0.0, 0.010, bottom), (0.0, 0.010, top)], [radius * taper, radius], sides=sides, cap0=False,
                   cap1=False)
        return self.add(obj, "neck", self.spec["skin"], SKIN)

    # ── parts 7 to 10: arms and mittens ──

    def arm(self, side, pts, radius, colour, cuff, cuff_len=0.02, sides=10):
        """A sleeve: a tapered tube through shoulder, elbow and wrist (side -1 is the
        model's right), a cuff band at the end. The shoulder end is open inside the coat."""
        pts = [Vector(p) for p in pts]
        path = catmull(pts, 2)
        d = (path[-1] - path[-2]).normalized()
        path[-1] = path[-1] + d * 0.002
        n = len(path)
        radii = [radius * lerp(1.05, 0.92, i / (n - 1)) for i in range(n)]
        # the cuff: one ring cuff_len back from the end, slightly flared
        path.insert(n - 1, path[-1] - d * cuff_len)
        radii.insert(n - 1, radius * 1.02)
        radii[-1] = radius * 1.04
        obj = tube(f"arm_{side}", path, radii, sides=sides, cap0=False)   # the shoulder end sits in the coat
        cuff_start = path[-2]

        def paint(c, i):
            return (cuff, CLOTH) if (c - cuff_start).dot(d) > -0.001 else (colour, CLOTH)

        return self.add(obj, "arm", paint)

    def mitten(self, side, at, fingers, back, size, colour, thumb=1.0, curl=0.0):
        """A mitten: a soft box round `at`, its fingers along `fingers`, the back of the hand
        toward `back`, plus a thumb lobe on the thumb side. size = (width, depth, length).
        No fingers: a closed mitten reads as a hand at any size and never tangles."""
        f = Vector(fingers).normalized()
        b = (Vector(back) - f * Vector(back).dot(f)).normalized()
        x = b.cross(f)
        basis = Matrix((x, b, f)).transposed()
        w, dpt, ln = size
        body = blob(f"mitten_{side}", at, (w / 2, dpt / 2, ln / 2), segs=8, rows=5, power=2.3,
                    shape=lambda d: (1.0 + 0.08 * d.z, 1.0 - 0.1 * max(0.0, d.z) - curl * max(0.0, -d.y), 1.0),
                    rot=basis)
        self.add(body, "mitten", colour)
        # the thumb sits on the palm side toward the body's centre line
        tside = -side * thumb
        t_at = Vector(at) + basis @ Vector((tside * w * 0.42, -dpt * 0.15, -ln * 0.05))
        tb = blob(f"thumb_{side}", t_at, (w * 0.20, dpt * 0.26, ln * 0.30), segs=6, rows=3,
                  rot=basis @ rot(x=-25, y=tside * 30))
        self.add(tb, "mitten", colour)
        return body

    # ── parts 11, 12: boots ──

    def boot(self, side, at, yaw, height, length, width, colour, cuff, sole, cuff_h, sole_h, toe=0.64, toe_h=0.42,
             shin=0.40, flare=1.14):
        """A chunky boot: a round toe box that sticks forward (`toe` of the length lies
        ahead of the shin) under a narrower round shin (`shin` x the width as its radius),
        flared into a cuff at the top. toe_h is the toe box's height as a share of the
        width. A low cage subdivided once, the sole edge creased hard; sole and cuff bands on
        its edge loops. The top is left open: the coat hides it."""
        at = Vector(at)
        R = rot(z=yaw)
        front, back, hw, r = length * toe, length * (1 - toe), width * 0.5, width * shin
        th_box = width * toe_h
        prof = [  # (z, reach to the front, reach to the back, half width); the subdivision
            #         rounds the cage in, so the toe box is drawn larger than it ends up
            (0.0, front, back, hw),
            (sole_h, front * 1.05, back * 1.04, hw * 1.08),
            (sole_h + th_box * 0.85, front * 0.94, back * 1.0, hw * 1.04),
            (sole_h + th_box * 1.15, r * 1.15, back * 0.86, r * 1.06),
            (height - cuff_h, r, r, r),
            (height, r * flare, r * flare, r * flare),
        ]
        cage = []
        for z, lf, lb, w in prof:
            ring_ = []
            for k in range(6):
                th = -math.pi / 2 + 2 * math.pi * k / 6
                c, s = math.cos(th), math.sin(th)
                ring_.append(at + R @ Vector((c * w, s * (lb if s > 0 else lf), z)))
            cage.append(ring_)
        obj = loft(f"boot_{side}", cage, cap0=True, cap1=False, ngon=True)
        z_sole = at.z + 1e-4
        crease(obj, lambda a, b: a.z < z_sole and b.z < z_sole)
        gab.subdivide(obj, 1)
        top = at.z + height

        def paint(c, i):
            if c.z < at.z + sole_h * 0.9:
                return sole, CLOTH
            if c.z > top - cuff_h:
                return cuff, CLOTH
            return colour, CLOTH

        self.add(obj, "boot", paint, smooth=40)
        return obj

    # ── finishing ──

    def finish(self, name, bust_cut=None, check=False):
        """Writes the toon normals, cuts the bust (everything below bust_cut goes and the
        rest drops to the origin), joins every part into one mesh and reports.

        Returns the joined object. With check, fails when a swatch is off the palette, over
        the triangle budget, outside the heads range or, for a bust, when anything would
        leave a portrait arch as it turns."""
        # the head's normal field, carried through the head's pose, into a point attribute
        inv = self.head_matrix.inverted()
        turn = self.head_matrix.to_3x3()
        for o in self.normal_group:
            me = o.data
            attr = me.attributes.new("toon_n", "FLOAT_VECTOR", "POINT")
            for v in me.vertices:
                attr.data[v.index].vector = (turn @ self.head_normal(inv @ v.co)).normalized()
        if bust_cut is not None:   # cut each part, so the report counts what the bust keeps
            kept = []
            for o, part in self.parts:
                bm = bmesh.new()
                bm.from_mesh(o.data)
                bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, bust_cut),
                                       plane_no=(0, 0, 1), clear_inner=True)
                bm.to_mesh(o.data)
                bm.free()
                o.data.update()
                if len(o.data.polygons):
                    kept.append((o, part))
                else:
                    bpy.data.objects.remove(o)
            self.parts = kept
        by_part = {}
        for o, part in self.parts:
            by_part[part] = by_part.get(part, 0) + gab.triangles(o)
        objs = [o for o, _ in self.parts]
        model = gab.join(objs, name)
        me = model.data
        if bust_cut is not None:
            me.transform(Matrix.Translation((0, 0, -bust_cut)))
            me.update()
        # custom normals: the toon field where it was written, the mesh's own elsewhere
        # (so a brim, the soles and a lantern frame keep their hard edges)
        me.update()
        toon = me.attributes["toon_n"]
        corner = [Vector(c.vector) for c in me.corner_normals]
        normals = []
        for loop in me.loops:
            t = Vector(toon.data[loop.vertex_index].vector)
            normals.append(t.normalized() if t.length > 0.5 else corner[loop.index])
        me.normals_split_custom_set([tuple(n) for n in normals])
        me.attributes.remove(me.attributes["toon_n"])
        if "crease_edge" in me.attributes:
            me.attributes.remove(me.attributes["crease_edge"])
        me.update()
        self.report(model, by_part, bust_cut, check)
        return model

    def report(self, model, by_part, bust_cut, check):
        bust = bust_cut is not None
        tris = gab.triangles(model)
        zs = [v.co.z for v in model.data.vertices]
        xs = [v.co.x for v in model.data.vertices]
        ys = [v.co.y for v in model.data.vertices]
        heads = (self.chin + self.H) / self.H
        print(f"{model.name}{' bust' if bust else ''}: {tris} triangles, size {max(xs) - min(xs):.3f} x "
              f"{max(ys) - min(ys):.3f} x {max(zs) - min(zs):.3f}, materials "
              f"{[m.name for m in model.data.materials]}")
        print(f"heads: crown {self.chin + self.H:.3f} / head {self.H:.3f} = {heads:.2f}; H {self.H:.3f}, "
              f"W {self.W:.3f}, chin {self.chin:.3f}")
        focus = self.face_focus() - (bust_cut or 0.0)
        print(f"face centre at y {focus:.3f} (asset review --turnaround --focus {focus:.3f} "
              f"--face-size {1.6 * self.H:.3f})")
        print("triangles by part:", ", ".join(f"{k} {v}" for k, v in sorted(by_part.items(), key=lambda kv: -kv[1])))
        print("swatches:", ", ".join(f"{k} ({'/'.join(sorted(v))})" for k, v in sorted(self.swatches.items())))
        layer = model.data.color_attributes["Color"]
        classes = sorted({round(c.color[3], 2) for c in layer.data})
        print("shade classes:", classes)
        limit = self.spec.get("budget", BUDGET)["bust" if bust else "full"]
        missing = []
        if self.spec.get("palette"):
            pal = gab.project_file(self.spec["palette"])
            swatches = gab.gpl_swatches(pal)
            if not swatches:
                print(f"palette {self.spec['palette']} not found (project {gab.project})")
            missing = sorted(set(self.swatches) - swatches)
            print(f"swatches not in {self.spec['palette']}:", missing or "none")
        for note in self.notes:
            print(note)
        reach = 0.0
        if bust:   # a portrait arch frames the bust at yaw 0 and turns it: keep it in a cylinder round the head
            hc = self.head_matrix @ self.HC
            reach = max(math.hypot(x - hc.x, y - hc.y) for x, y in zip(xs, ys)) / max(zs)
            print(f"arch: everything within {reach:.2f} x the bust's height of the head's axis (at most 0.55)")
        if check:
            lo, hi = self.spec.get("heads_range", (2.5, 3.0))
            if missing:
                raise RuntimeError(f"swatches outside the palette: {missing}")
            if tris > limit:
                raise RuntimeError(f"{tris} triangles, over the {limit} budget")
            if reach > 0.55:
                raise RuntimeError(f"the bust reaches {reach:.2f} x its height from the head's axis, over 0.55")
            if heads < lo or heads > hi:
                raise RuntimeError(f"{heads:.2f} heads, outside {lo} to {hi}")
