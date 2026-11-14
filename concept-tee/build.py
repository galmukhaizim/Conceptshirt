#!/usr/bin/env python3
"""
Product render of a solid pink crewneck t-shirt body fused with a spread
dress-shirt collar (no buttons, no buttonholes, no placket), laid flat on a
gray studio sweep.

Pipeline
  1. Build an SVG scene: geometry, studio-light gradients, drop/contact shadows,
     seams, hems, collar, stitching.
  2. Build a matching region-mask SVG (body / collar leaf / collar band).
  3. Render both with headless Chromium (render.cjs, Playwright).
  4. In Python: jersey-knit texture on the body, poplin weave on the collar,
     soft fabric undulation, fine grain. Save PNG + JPEG + preview.

usage: python3 build.py [--size 2048]
"""
from __future__ import annotations

import argparse
import math
import pathlib
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFilter

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "output"
VB = 1000.0  # SVG viewBox is 1000 x 1000

# ----------------------------------------------------------------------------
# palette
# ----------------------------------------------------------------------------
PINK = "#F093AE"
PINK_HI = "#F9BBCC"
PINK_LO = "#DB7D99"
PINK_DEEP = "#B95A77"
STITCH = "#C96682"
STITCH_HI = "#FBD6E1"
BG_C = "#DDDDE0"
BG_E = "#B3B3B7"


# ----------------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------------
def f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def P(p) -> str:
    return f"{f(p[0])} {f(p[1])}"


def mir(p):
    return (VB - p[0], p[1])


def cubic(p0, p1, p2, p3, n=48):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((
            u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
        ))
    return out


def quad(p0, p1, p2, n=32):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return out


def arc(cx, cy, rx, ry, t0, t1, n=120):
    """Elliptical arc, angles in degrees; point = (cx + rx cos t, cy + ry sin t)."""
    out = []
    for i in range(n + 1):
        t = math.radians(t0 + (t1 - t0) * i / n)
        out.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
    return out


def join(*segs):
    pts = []
    for s in segs:
        if pts and s and math.dist(pts[-1], s[0]) < 1e-6:
            pts.extend(s[1:])
        else:
            pts.extend(s)
    return pts


def offset(pts, d, closed=False):
    """Offset a polyline by d to the left of travel (screen coords, y down)."""
    n = len(pts)
    segs = []
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1e-9
        segs.append((dy / L, -dx / L))
    out = []
    for i in range(n):
        if closed:
            n1, n2 = segs[(i - 1) % n], segs[i]
        else:
            if i == 0:
                n1 = n2 = segs[0]
            elif i == n - 1:
                n1 = n2 = segs[-1]
            else:
                n1, n2 = segs[i - 1], segs[i]
        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        ml = math.hypot(mx, my) or 1e-9
        mx, my = mx / ml, my / ml
        cos_half = max(0.35, mx * n1[0] + my * n1[1])
        s = d / cos_half
        out.append((pts[i][0] + mx * s, pts[i][1] + my * s))
    return out


def poly_path(pts, closed=False):
    d = "M " + P(pts[0]) + " " + " ".join("L " + P(p) for p in pts[1:])
    return d + (" Z" if closed else "")


def trim(pts, x0, x1):
    return [p for p in pts if x0 <= p[0] <= x1]


# ----------------------------------------------------------------------------
# tee geometry (left half; right half is mirrored)
# ----------------------------------------------------------------------------
N = (424, 238)      # neck / shoulder point
S = (306, 264)      # shoulder tip
E = (188, 396)      # sleeve outer end
HM = (268, 486)     # sleeve hem inner end
A = (324, 398)      # armpit
B = (312, 790)      # hem corner
HEM_CTRL = (380, 800)

SHOULDER_C = ((392, 247), (342, 257))
SLEEVE_C = ((270, 300), (212, 356))
SLEEVE_HEM_Q = (222, 448)
UNDERARM_C = ((292, 452), (310, 420))
SIDE_C = ((318, 520), (308, 672))
ARMHOLE_C = ((346, 352), (342, 296))  # from A back up to S

def tee_outline_d() -> str:
    m = mir
    d = [
        f"M {P(N)}",
        f"C {P(SHOULDER_C[0])} {P(SHOULDER_C[1])} {P(S)}",
        f"C {P(SLEEVE_C[0])} {P(SLEEVE_C[1])} {P(E)}",
        f"Q {P(SLEEVE_HEM_Q)} {P(HM)}",
        f"C {P(UNDERARM_C[0])} {P(UNDERARM_C[1])} {P(A)}",
        f"C {P(SIDE_C[0])} {P(SIDE_C[1])} {P(B)}",
        f"C {P(HEM_CTRL)} {P(m(HEM_CTRL))} {P(m(B))}",
        f"C {P(m(SIDE_C[1]))} {P(m(SIDE_C[0]))} {P(m(A))}",
        f"C {P(m(UNDERARM_C[1]))} {P(m(UNDERARM_C[0]))} {P(m(HM))}",
        f"Q {P(m(SLEEVE_HEM_Q))} {P(m(E))}",
        f"C {P(m(SLEEVE_C[1]))} {P(m(SLEEVE_C[0]))} {P(m(S))}",
        f"C {P(m(SHOULDER_C[1]))} {P(m(SHOULDER_C[0]))} {P(m(N))}",
        f"C {P((560, 262))} {P((440, 262))} {P(N)} Z",
    ]
    return " ".join(d)


def sleeve_d(right=False) -> str:
    m = mir if right else (lambda p: p)
    return " ".join([
        f"M {P(m(S))}",
        f"C {P(m(SLEEVE_C[0]))} {P(m(SLEEVE_C[1]))} {P(m(E))}",
        f"Q {P(m(SLEEVE_HEM_Q))} {P(m(HM))}",
        f"C {P(m(UNDERARM_C[0]))} {P(m(UNDERARM_C[1]))} {P(m(A))}",
        f"C {P(m(ARMHOLE_C[0]))} {P(m(ARMHOLE_C[1]))} {P(m(S))} Z",
    ])


def armhole_d(right=False) -> str:
    m = mir if right else (lambda p: p)
    return f"M {P(m(A))} C {P(m(ARMHOLE_C[0]))} {P(m(ARMHOLE_C[1]))} {P(m(S))}"


def hem_pts():
    return cubic(B, HEM_CTRL, mir(HEM_CTRL), mir(B), 80)


def sleeve_hem_pts(right=False):
    pts = quad(E, SLEEVE_HEM_Q, HM, 40)
    if right:
        # travel direction mirrored: keep "inward" on the left of travel
        pts = [mir(p) for p in pts][::-1]
    return pts


# ----------------------------------------------------------------------------
# collar geometry
# ----------------------------------------------------------------------------
CCX, CCY = 500.0, 258.0
FOLD_RX, FOLD_RY = 70.0, 40.0         # fold line (leaf inner edge)
HOLE_RX, HOLE_RY, HOLE_CY = 52.0, 24.0, 264.0
SEAM_RX, SEAM_RY, SEAM_CY = 76.0, 64.0, 262.0  # neckline seam (band bottom)
GAP = 10.0                            # half-gap between leaf fronts at the fold
PL = (392.0, 358.0)                   # left collar point
PR = mir(PL)

def fold_point(x):
    y = CCY + FOLD_RY * math.sqrt(max(0.0, 1 - ((x - CCX) / FOLD_RX) ** 2))
    return (x, y)

FL = fold_point(CCX - GAP)
FR = fold_point(CCX + GAP)

def fold_angle(p):
    return math.degrees(math.atan2((p[1] - CCY) / FOLD_RY, (p[0] - CCX) / FOLD_RX)) % 360

T_FL = fold_angle(FL)          # ~96 deg
T_FR = fold_angle(FR) + 360    # ~444 deg (go the long way, over the top)

OUTER_C = [  # from PR around the top to PL
    ((614, 326), (640, 276), (632, 236)),
    ((622, 204), (568, 186), (500, 186)),
    ((432, 186), (378, 204), (368, 236)),
    ((360, 276), (386, 326), PL),
]

def outer_curve_pts():
    pts = [PR]
    cur = PR
    for c1, c2, p in OUTER_C:
        pts = join(pts, cubic(cur, c1, c2, p, 60))
        cur = p
    return pts


def leaf_pts():
    """Closed polygon: PL -> FL -> fold arc over the top -> FR -> PR -> outer curve -> PL."""
    return join([PL, FL], arc(CCX, CCY, FOLD_RX, FOLD_RY, T_FL, T_FR, 160), [FR, PR], outer_curve_pts())


def leaf_d() -> str:
    oc = OUTER_C
    return " ".join([
        f"M {P(PL)} L {P(FL)}",
        f"A {f(FOLD_RX)} {f(FOLD_RY)} 0 1 1 {P(FR)}",
        f"L {P(PR)}",
        *[f"C {P(c1)} {P(c2)} {P(p)}" for c1, c2, p in oc],
        "Z",
    ])


def leaf_stitch_pts(inset):
    """Edge stitching along the finished edges only (front edges + outer edge), not the fold."""
    open_pts = join([FR, PR], outer_curve_pts(), [PL, FL])
    return offset(open_pts, inset, closed=False)


def band_d() -> str:
    """Band outer face: between fold line (top) and neckline seam (bottom)."""
    left = (CCX - FOLD_RX, CCY)
    right = (CCX + FOLD_RX, CCY)
    sl = (CCX - SEAM_RX, SEAM_CY)
    sr = (CCX + SEAM_RX, SEAM_CY)
    return " ".join([
        f"M {P(left)}",
        f"A {f(FOLD_RX)} {f(FOLD_RY)} 0 0 0 {P(right)}",
        f"L {P(sr)}",
        f"A {f(SEAM_RX)} {f(SEAM_RY)} 0 0 1 {P(sl)} Z",
    ])


def band_top_pts():
    return arc(CCX, CCY, FOLD_RX, FOLD_RY, 40, 140, 120)  # lower arc, left-to-right in the V


def hole_d() -> str:
    return (f"M {f(CCX - HOLE_RX)} {f(HOLE_CY)} "
            f"A {f(HOLE_RX)} {f(HOLE_RY)} 0 1 1 {f(CCX + HOLE_RX)} {f(HOLE_CY)} "
            f"A {f(HOLE_RX)} {f(HOLE_RY)} 0 1 1 {f(CCX - HOLE_RX)} {f(HOLE_CY)} Z")


def fold_ellipse_d() -> str:
    return (f"M {f(CCX - FOLD_RX)} {f(CCY)} "
            f"A {f(FOLD_RX)} {f(FOLD_RY)} 0 1 1 {f(CCX + FOLD_RX)} {f(CCY)} "
            f"A {f(FOLD_RX)} {f(FOLD_RY)} 0 1 1 {f(CCX - FOLD_RX)} {f(CCY)} Z")


# ----------------------------------------------------------------------------
# SVG scene
# ----------------------------------------------------------------------------
def stitch_group(pts, dash="2.4 2.1", w=1.15, op=0.85, hi=True):
    d = poly_path(pts)
    g = [f'<path d="{d}" fill="none" stroke="{STITCH}" stroke-width="{w}" '
         f'stroke-dasharray="{dash}" stroke-linecap="round" opacity="{op}"/>']
    if hi:
        hpts = offset(pts, 0.9)
        g.insert(0, f'<path d="{poly_path(hpts)}" fill="none" stroke="{STITCH_HI}" stroke-width="{w*0.9}" '
                    f'stroke-dasharray="{dash}" stroke-linecap="round" opacity="0.45"/>')
    return "\n".join(g)


def beauty_svg() -> str:
    tee = tee_outline_d()
    leaf = leaf_d()
    band = band_d()
    hole = hole_d()
    fold = fold_ellipse_d()

    hem = hem_pts()
    hem_st1 = trim(offset(hem, 15), 334, 666)
    hem_st2 = trim(offset(hem, 21), 334, 666)
    hem_fold = trim(offset(hem, 27), 330, 670)

    sl_l = sleeve_hem_pts(False)
    sl_r = sleeve_hem_pts(True)
    sl_l1, sl_l2 = offset(sl_l, 13)[3:-3], offset(sl_l, 19)[3:-3]
    sl_r1, sl_r2 = offset(sl_r, 13)[3:-3], offset(sl_r, 19)[3:-3]
    sl_lf, sl_rf = offset(sl_l, 25)[4:-4], offset(sl_r, 25)[4:-4]

    leaf_st = leaf_stitch_pts(4.2)
    band_top = band_top_pts()
    band_st = offset(band_top, -3.6)  # travel left->right along the lower arc: inward (down) is right of travel

    defs = f"""
<defs>
  <radialGradient id="bg" cx="0.5" cy="0.42" r="0.78">
    <stop offset="0" stop-color="{BG_C}"/>
    <stop offset="0.55" stop-color="#CBCBCF"/>
    <stop offset="1" stop-color="{BG_E}"/>
  </radialGradient>
  <linearGradient id="bgsweep" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.16"/>
    <stop offset="0.5" stop-color="#ffffff" stop-opacity="0"/>
    <stop offset="1" stop-color="#000000" stop-opacity="0.10"/>
  </linearGradient>
  <radialGradient id="vig" cx="0.5" cy="0.5" r="0.75">
    <stop offset="0.6" stop-color="#000" stop-opacity="0"/>
    <stop offset="1" stop-color="#000" stop-opacity="0.22"/>
  </radialGradient>

  <radialGradient id="body" gradientUnits="userSpaceOnUse" cx="430" cy="400" r="560">
    <stop offset="0" stop-color="{PINK_HI}"/>
    <stop offset="0.38" stop-color="{PINK}"/>
    <stop offset="1" stop-color="{PINK_LO}"/>
  </radialGradient>
  <linearGradient id="bodylight" gradientUnits="userSpaceOnUse" x1="200" y1="200" x2="800" y2="820">
    <stop offset="0" stop-color="#fff" stop-opacity="0.10"/>
    <stop offset="0.5" stop-color="#fff" stop-opacity="0"/>
    <stop offset="1" stop-color="#4a0f22" stop-opacity="0.08"/>
  </linearGradient>
  <linearGradient id="sleeveL" gradientUnits="userSpaceOnUse" x1="200" y1="300" x2="330" y2="360">
    <stop offset="0" stop-color="#fff" stop-opacity="0.07"/>
    <stop offset="0.75" stop-color="#4a0f22" stop-opacity="0"/>
    <stop offset="1" stop-color="#4a0f22" stop-opacity="0.16"/>
  </linearGradient>
  <linearGradient id="sleeveR" gradientUnits="userSpaceOnUse" x1="800" y1="300" x2="670" y2="360">
    <stop offset="0" stop-color="#4a0f22" stop-opacity="0.06"/>
    <stop offset="0.75" stop-color="#4a0f22" stop-opacity="0"/>
    <stop offset="1" stop-color="#4a0f22" stop-opacity="0.16"/>
  </linearGradient>

  <linearGradient id="leafg" gradientUnits="userSpaceOnUse" x1="390" y1="180" x2="610" y2="360">
    <stop offset="0" stop-color="#F8BACB"/>
    <stop offset="0.5" stop-color="#F29DB6"/>
    <stop offset="1" stop-color="#E48BA6"/>
  </linearGradient>
  <linearGradient id="bandg" gradientUnits="userSpaceOnUse" x1="0" y1="296" x2="0" y2="328">
    <stop offset="0" stop-color="#F3A7BD"/>
    <stop offset="0.6" stop-color="#E58FA9"/>
    <stop offset="1" stop-color="#C96E8B"/>
  </linearGradient>
  <linearGradient id="bandin" gradientUnits="userSpaceOnUse" x1="0" y1="216" x2="0" y2="296">
    <stop offset="0" stop-color="#A24B68"/>
    <stop offset="0.5" stop-color="#C46B87"/>
    <stop offset="1" stop-color="#D98AA3"/>
  </linearGradient>
  <linearGradient id="holeg" gradientUnits="userSpaceOnUse" x1="0" y1="238" x2="0" y2="290">
    <stop offset="0" stop-color="#7E7E83"/>
    <stop offset="0.55" stop-color="#9C9CA0"/>
    <stop offset="1" stop-color="#8E8E92"/>
  </linearGradient>

  <filter id="shadowSoft" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="13"/>
  </filter>
  <filter id="shadowMid" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="5"/>
  </filter>
  <filter id="shadowContact" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="1.6"/>
  </filter>
  <filter id="blur3" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="3"/>
  </filter>
  <filter id="blur6" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="6"/>
  </filter>
  <filter id="blur10" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="10"/>
  </filter>
  <filter id="inner" x="-10%" y="-10%" width="120%" height="120%">
    <feMorphology in="SourceAlpha" operator="erode" radius="4" result="er"/>
    <feGaussianBlur in="er" stdDeviation="5" result="bl"/>
    <feComposite in="SourceAlpha" in2="bl" operator="out" result="rim"/>
    <feFlood flood-color="#3a0c1b" flood-opacity="0.26"/>
    <feComposite in2="rim" operator="in"/>
  </filter>
  <filter id="innerCrisp" x="-10%" y="-10%" width="120%" height="120%">
    <feMorphology in="SourceAlpha" operator="erode" radius="2" result="er"/>
    <feGaussianBlur in="er" stdDeviation="2.2" result="bl"/>
    <feComposite in="SourceAlpha" in2="bl" operator="out" result="rim"/>
    <feFlood flood-color="#3a0c1b" flood-opacity="0.30"/>
    <feComposite in2="rim" operator="in"/>
  </filter>
  <filter id="innerHole" x="-20%" y="-20%" width="140%" height="140%">
    <feMorphology in="SourceAlpha" operator="erode" radius="6" result="er"/>
    <feGaussianBlur in="er" stdDeviation="7" result="bl"/>
    <feComposite in="SourceAlpha" in2="bl" operator="out" result="rim"/>
    <feFlood flood-color="#000" flood-opacity="0.45"/>
    <feComposite in2="rim" operator="in"/>
  </filter>

  <clipPath id="clipTee"><path d="{tee}"/></clipPath>
  <clipPath id="clipLeaf"><path d="{leaf}"/></clipPath>
  <clipPath id="clipBand"><path d="{band}"/></clipPath>
  <clipPath id="clipFold"><path d="{fold}"/></clipPath>
  <clipPath id="clipHole"><path d="{hole}"/></clipPath>
</defs>
"""

    garment_union = f'<path d="{tee}"/><path d="{leaf}"/><path d="{band}"/><path d="{fold}"/>'

    scene = f"""
<rect width="1000" height="1000" fill="url(#bg)"/>
<rect width="1000" height="1000" fill="url(#bgsweep)"/>

<!-- shadows on the backdrop -->
<g fill="#1a0a10" opacity="0.30" filter="url(#shadowSoft)" transform="translate(8 20)">{garment_union}</g>
<g fill="#1a0a10" opacity="0.22" filter="url(#shadowMid)" transform="translate(2 6)">{garment_union}</g>
<g fill="#1a0a10" opacity="0.30" filter="url(#shadowContact)" transform="translate(0 1.5)">{garment_union}</g>

<!-- tee body -->
<path d="{tee}" fill="url(#body)"/>
<path d="{tee}" fill="url(#bodylight)"/>

<g clip-path="url(#clipTee)">
  <!-- sleeve tonal separation -->
  <path d="{sleeve_d(False)}" fill="url(#sleeveL)"/>
  <path d="{sleeve_d(True)}" fill="url(#sleeveR)"/>

  <!-- soft folds / undulations -->
  <g filter="url(#blur10)" opacity="0.5">
    <ellipse cx="372" cy="470" rx="26" ry="70" transform="rotate(28 372 470)" fill="#3a0c1b" opacity="0.14"/>
    <ellipse cx="628" cy="470" rx="26" ry="70" transform="rotate(-28 628 470)" fill="#3a0c1b" opacity="0.14"/>
    <ellipse cx="500" cy="705" rx="150" ry="9" fill="#3a0c1b" opacity="0.10"/>
    <ellipse cx="500" cy="690" rx="140" ry="7" fill="#fff" opacity="0.14"/>
    <ellipse cx="455" cy="560" rx="120" ry="12" transform="rotate(-8 455 560)" fill="#fff" opacity="0.08"/>
    <ellipse cx="560" cy="610" rx="110" ry="10" transform="rotate(6 560 610)" fill="#3a0c1b" opacity="0.06"/>
  </g>

  <!-- armhole seams: ridge shadow + seam line -->
  <g fill="none" stroke-linecap="round">
    <path d="{armhole_d(False)}" stroke="#3a0c1b" stroke-width="7" opacity="0.10" filter="url(#blur3)"/>
    <path d="{armhole_d(True)}" stroke="#3a0c1b" stroke-width="7" opacity="0.10" filter="url(#blur3)"/>
    <path d="{armhole_d(False)}" stroke="#fff" stroke-width="1.6" opacity="0.22" transform="translate(1.2 0)"/>
    <path d="{armhole_d(True)}" stroke="#fff" stroke-width="1.6" opacity="0.22" transform="translate(-1.2 0)"/>
    <path d="{armhole_d(False)}" stroke="{PINK_DEEP}" stroke-width="1.3" opacity="0.75"/>
    <path d="{armhole_d(True)}" stroke="{PINK_DEEP}" stroke-width="1.3" opacity="0.75"/>
  </g>

  <!-- hems: folded-hem ridge + double-needle stitching -->
  <g fill="none" stroke-linecap="round">
    <path d="{poly_path(hem_fold)}" stroke="#3a0c1b" stroke-width="3" opacity="0.10" filter="url(#blur3)"/>
    <path d="{poly_path(hem_fold)}" stroke="#fff" stroke-width="1.2" opacity="0.18"/>
    <path d="{poly_path(sl_lf)}" stroke="#3a0c1b" stroke-width="3" opacity="0.10" filter="url(#blur3)"/>
    <path d="{poly_path(sl_rf)}" stroke="#3a0c1b" stroke-width="3" opacity="0.10" filter="url(#blur3)"/>
    <path d="{poly_path(sl_lf)}" stroke="#fff" stroke-width="1.2" opacity="0.18"/>
    <path d="{poly_path(sl_rf)}" stroke="#fff" stroke-width="1.2" opacity="0.18"/>
  </g>
  {stitch_group(hem_st1)}
  {stitch_group(hem_st2)}
  {stitch_group(sl_l1)}
  {stitch_group(sl_l2)}
  {stitch_group(sl_r1)}
  {stitch_group(sl_r2)}
</g>

<!-- fabric edge thickness -->
<path d="{tee}" fill="#000" filter="url(#inner)"/>

<!-- collar: back band inner face, then the neck opening -->
<path d="{fold}" fill="url(#bandin)"/>
<path d="{hole}" fill="url(#holeg)"/>
<path d="{hole}" fill="#000" filter="url(#innerHole)"/>
<g clip-path="url(#clipHole)">
  <path d="{fold}" fill="none" stroke="#000" stroke-width="10" opacity="0.18" filter="url(#blur3)" transform="translate(0 10)"/>
</g>

<!-- collar band outer face (visible between the spread leaves) -->
<path d="{band}" fill="url(#bandg)"/>
<path d="{band}" fill="#000" filter="url(#innerCrisp)"/>
<g clip-path="url(#clipBand)">
  <!-- center-front join (band ends meet, no button) -->
  <path d="M 500 {f(FL[1])} L 500 {f(SEAM_CY + SEAM_RY)}" stroke="#7d2f49" stroke-width="0.9" opacity="0.35"/>
  <path d="M 501.2 {f(FL[1])} L 501.2 {f(SEAM_CY + SEAM_RY)}" stroke="#fff" stroke-width="0.8" opacity="0.22"/>
  {stitch_group(band_st, dash="2.2 2", w=1.0, op=0.8)}
</g>
<!-- fold highlight on band top edge + neckline seam shadow onto body -->
<path d="{poly_path(band_top)}" fill="none" stroke="#fff" stroke-width="1.2" opacity="0.35" clip-path="url(#clipBand)"/>
<g clip-path="url(#clipTee)">
  <path d="M {f(CCX - SEAM_RX)} {f(SEAM_CY)} A {f(SEAM_RX)} {f(SEAM_RY)} 0 0 0 {f(CCX + SEAM_RX)} {f(SEAM_CY)}"
        fill="none" stroke="#3a0c1b" stroke-width="6" opacity="0.16" filter="url(#blur3)" transform="translate(0 3)"/>
  <path d="M {f(CCX - SEAM_RX)} {f(SEAM_CY)} A {f(SEAM_RX)} {f(SEAM_RY)} 0 0 0 {f(CCX + SEAM_RX)} {f(SEAM_CY)}"
        fill="none" stroke="{PINK_DEEP}" stroke-width="1.1" opacity="0.55"/>
</g>

<!-- leaf shadow onto body / band -->
<g opacity="0.42" filter="url(#blur6)" transform="translate(3 9)">
  <path d="{leaf}" fill="#2a0613"/>
</g>
<g opacity="0.35" filter="url(#shadowContact)" transform="translate(0.5 2)">
  <path d="{leaf}" fill="#2a0613"/>
</g>

<!-- collar leaf -->
<path d="{leaf}" fill="url(#leafg)"/>
<g clip-path="url(#clipLeaf)">
  <!-- gentle roll near the fold -->
  <path d="{fold}" fill="none" stroke="#3a0c1b" stroke-width="14" opacity="0.14" filter="url(#blur6)"/>
  <path d="{fold}" fill="none" stroke="#fff" stroke-width="2" opacity="0.30" filter="url(#shadowContact)" transform="translate(0 -1)"/>
  <g filter="url(#blur6)">
    <ellipse cx="412" cy="332" rx="22" ry="12" fill="#fff" opacity="0.16"/>
    <ellipse cx="588" cy="332" rx="22" ry="12" fill="#fff" opacity="0.12"/>
  </g>
  {stitch_group(leaf_st, dash="2.4 2.1", w=1.15, op=0.85)}
</g>
<path d="{leaf}" fill="#000" filter="url(#innerCrisp)"/>

<rect width="1000" height="1000" fill="url(#vig)"/>
"""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" width="1000" height="1000">'
            f"{defs}{scene}</svg>")


def mask_svg() -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" width="1000" height="1000">'
            f'<rect width="1000" height="1000" fill="#000"/>'
            f'<path d="{tee_outline_d()}" fill="#ff0000"/>'
            f'<path d="{fold_ellipse_d()}" fill="#000000"/>'
            f'<path d="{band_d()}" fill="#0000ff"/>'
            f'<path d="{leaf_d()}" fill="#00ff00"/>'
            f"</svg>")


# ----------------------------------------------------------------------------
# texture synthesis
# ----------------------------------------------------------------------------
def fractal_noise(size, rng, octaves=((16, 1.0), (32, 0.55), (64, 0.3), (128, 0.16), (256, 0.09))):
    acc = np.zeros((size, size), np.float32)
    for grid, amp in octaves:
        g = rng.standard_normal((grid, grid)).astype(np.float32)
        img = Image.fromarray(g, mode="F").resize((size, size), Image.BICUBIC)
        acc += amp * np.asarray(img, np.float32)
    acc -= acc.mean()
    acc /= (acc.std() + 1e-6)
    return acc


def gauss(arr, sigma):
    """Gaussian blur of a float32 array via FFT (PIL has no float-mode blur)."""
    if sigma <= 0:
        return arr
    h, w = arr.shape
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    kernel = np.exp(-2.0 * (np.pi ** 2) * (sigma ** 2) * (fx ** 2 + fy ** 2))
    out = np.fft.irfft2(np.fft.rfft2(arr.astype(np.float32)) * kernel, s=(h, w))
    return out.astype(np.float32)


def knit_texture(size, rng, scale):
    """Jersey knit: vertical wales with a finer horizontal course modulation."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    wale = 3.3 * scale
    course = 2.5 * scale
    wobble = gauss(rng.standard_normal((size, size)).astype(np.float32), 6 * scale) * 9.0
    ribs = np.cos(2 * np.pi * (x + wobble) / wale) * (1.0 + 0.35 * gauss(rng.standard_normal((size, size)).astype(np.float32), 10 * scale) * 6.0)
    courses = np.cos(2 * np.pi * (y + 0.35 * wobble) / course + 0.8 * np.sin(2 * np.pi * x / wale))
    k = 0.62 * ribs + 0.22 * courses * (0.5 + 0.5 * ribs)
    k += 0.35 * rng.standard_normal((size, size)).astype(np.float32)
    k = gauss(k, 0.45 * scale)
    k -= k.mean()
    k /= (k.std() + 1e-6)
    return k


def poplin_texture(size, rng, scale):
    """Crisp plain weave: fine crosshatch, tighter than the knit."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    p = 2.1 * scale
    w = np.cos(2 * np.pi * x / p) * np.cos(2 * np.pi * y / p)
    w += 0.35 * np.cos(2 * np.pi * x / p) + 0.35 * np.cos(2 * np.pi * y / p)
    w += 0.5 * rng.standard_normal((size, size)).astype(np.float32)
    w = gauss(w, 0.35 * scale)
    w -= w.mean()
    w /= (w.std() + 1e-6)
    return w


def composite(beauty: Image.Image, masks: Image.Image, seed=7) -> Image.Image:
    size = beauty.size[0]
    scale = size / 2048.0
    rng = np.random.default_rng(seed)

    img = np.asarray(beauty.convert("RGB"), np.float32) / 255.0
    m = np.asarray(masks.convert("RGB"), np.float32) / 255.0
    m_body, m_leaf, m_band = m[..., 0], m[..., 1], m[..., 2]
    m_collar = np.clip(m_leaf + m_band, 0, 1)
    m_fabric = np.clip(m_body + m_collar, 0, 1)

    knit = knit_texture(size, rng, scale)
    poplin = poplin_texture(size, rng, scale)
    undul_body = fractal_noise(size, rng, octaves=((6, 1.0), (12, 0.6), (24, 0.3), (48, 0.12)))
    undul_body = gauss(undul_body, 8 * scale)
    undul_body /= (undul_body.std() + 1e-6)
    undul_collar = gauss(fractal_noise(size, rng), 2 * scale)
    undul_collar /= (undul_collar.std() + 1e-6)
    fuzz = rng.standard_normal((size, size)).astype(np.float32)

    # luminance modulation per region (fractions of the base color)
    mod = (
        m_body * (0.030 * knit + 0.016 * undul_body + 0.011 * fuzz)
        + m_collar * (0.026 * poplin + 0.010 * undul_collar + 0.006 * fuzz)
    )
    # slightly stronger texture in the mid-tones, weaker in deep shadow
    lum = img.mean(axis=2)
    mod *= np.clip(0.55 + lum, 0.6, 1.25)

    out = img * (1.0 + mod[..., None])

    # a faint sheen on the collar (crisper, slightly glossier cotton)
    sheen = gauss(np.clip(poplin, 0, None), 1.2 * scale) * m_collar * 0.012
    out += sheen[..., None]

    # overall photographic grain
    out += (0.0045 * rng.standard_normal(out.shape)).astype(np.float32)

    out = np.clip(out, 0, 1)
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGB")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def render(svg_path: pathlib.Path, png_path: pathlib.Path, size: int):
    subprocess.run(["node", str(HERE / "render.cjs"), str(svg_path), str(png_path), str(size)],
                   check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=2048)
    ap.add_argument("--no-texture", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    work = HERE / "_work"
    work.mkdir(exist_ok=True)

    beauty_svg_path = HERE / "pink_collar_tee.svg"
    beauty_svg_path.write_text(beauty_svg())
    mask_svg_path = work / "masks.svg"
    mask_svg_path.write_text(mask_svg())

    beauty_png = work / "beauty.png"
    mask_png = work / "masks.png"
    render(beauty_svg_path, beauty_png, args.size)
    render(mask_svg_path, mask_png, args.size)

    beauty = Image.open(beauty_png)
    if args.no_texture:
        final = beauty.convert("RGB")
    else:
        final = composite(beauty, Image.open(mask_png))

    final.save(OUT / "pink_collar_tee.png", optimize=True)
    final.save(OUT / "pink_collar_tee.jpg", quality=93, subsampling=0)
    final.resize((1024, 1024), Image.LANCZOS).save(OUT / "pink_collar_tee_preview.png")
    print("wrote", OUT / "pink_collar_tee.png")


if __name__ == "__main__":
    main()
