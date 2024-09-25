# Based on https://jtsorlinis.github.io/rendering-tutorial/
from PIL import Image
import transformations as tf

import base64
import io
import math


def _edge(a, b, c):
   return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _interpolate(a, b, c, bary):
    return a * bary[0] + b * bary[1] + c * bary[2]


def _interpolate_vector(a, b, c, bary, s):
    return tuple(_interpolate(a[i], b[i], c[i], bary) * s for i in range(len(a)))


def _evaluate_edges(a, b, c, inv_abc, p):
    abp = (b[0] - a[0]) * (p[1] - a[1]) - \
        (b[1] - a[1]) * (p[0] - a[0])  # edge(a,b,p)
    bcp = (c[0] - b[0]) * (p[1] - b[1]) - \
        (c[1] - b[1]) * (p[0] - b[0])  # edge(b,c,p)
    cap = (a[0] - c[0]) * (p[1] - c[1]) - \
        (a[1] - c[1]) * (p[0] - c[0])  # edge(c,a,p)
    return (bcp * inv_abc, cap * inv_abc, abp * inv_abc)


def _reject_edges(bary):
    return bary[0] < 0 or bary[1] < 0 or bary[2] < 0


def _reject_coords(p, dims):
    return p[0] < 0 or p[1] < 0 or p[0] >= dims[0] or p[1] >= dims[1]


def _render_triangle_2(rgb, z, dims, a, b, c, material):
    # Transform to image coordinates
    hw = dims[0] * 0.5
    hh = dims[1] * 0.5
    ia = (a[0][0] * hw + hw, a[0][1] * -hh + hh)
    ib = (b[0][0] * hw + hw, b[0][1] * -hh + hh)
    ic = (c[0][0] * hw + hw, c[0][1] * -hh + hh)

    abc = _edge(ia, ib, ic)
    if abc <= 0:
       return

    inv_abc = 1 / abc
    min_x = math.floor(min(ia[0], ib[0], ic[0]))
    min_y = math.floor(min(ia[1], ib[1], ic[1]))
    max_x = math.ceil(max(ia[0], ib[0], ic[0]))
    max_y = math.ceil(max(ia[1], ib[1], ic[1]))
    for py in range(min_y, max_y, 2):
      for px in range(min_x, max_x, 2):
        ps = [(px, py), (px + 1, py), (px, py + 1), (px + 1, py + 1)]
        bary = [_evaluate_edges(ia, ib, ic, inv_abc, p) for p in ps]
        zidx = [p[1] * dims[0] + p[0] for p in ps]

        pos_z = [_interpolate(a[0][2], b[0][2], c[0][2], f) for f in bary]
        mask = [_reject_coords(ps[i], dims) or _reject_edges(
            bary[i]) or pos_z[i] >= z[zidx[i]] for i in range(4)]
        if all(mask):
            continue

        def pos_w(f): return _interpolate(a[0][3], b[0][3], c[0][3], f)
        uv = [_interpolate_vector(
            a[1], b[1], c[1], f, 1 / pos_w(f)) for f in bary]
        uv_dx = (uv[1][0] - uv[0][0], uv[1][1] - uv[0][1])
        uv_dy = (uv[2][0] - uv[0][0], uv[2][1] - uv[0][1])
        for i in range(4):
            if mask[i]:
                continue
            z[zidx[i]] = pos_z[i]
            color = material.sample(uv[i], uv_dx, uv_dy)
            rgb.putpixel(ps[i], color)


def _lerp(a, b, f):
    return tuple(a[i] * (1 - f) + b[i] * f for i in range(len(a)))


def _lerp_vertex(a, b, f):
    return [_lerp(a[i], b[i], f) for i in range(len(a))]


def _clip_to_plane(p, v):
    clipped = []
    len_v = len(v)
    for i in range(len_v):
        j = (i + 1) % len_v
        di = v[i][0][0] * p[0] + v[i][0][1] * p[1] + \
            v[i][0][2] * p[2] + v[i][0][3] * p[3]
        dj = v[j][0][0] * p[0] + v[j][0][1] * p[1] + \
            v[j][0][2] * p[2] + v[j][0][3] * p[3]
        if di >= 0:
            clipped.append(v[i])
            if dj < 0:
                f = di / (di - dj)
                clipped.append(_lerp_vertex(v[i], v[j], f))
        else:
            if dj >= 0:
                f = dj / (dj - di)
                clipped.append(_lerp_vertex(v[j], v[i], f))
    return clipped


def _project_vertex(v):
   inv_w = 1 / v[0][3]
   result = [[x * inv_w for x in a] for a in v]
   result[0][3] = inv_w  # use during rendering
   return result


_CLIP_PLANES = (
    (1, 0, 0, 1),  # left
    (-1, 0, 0, 1),  # right
    (0, -1, 0, 1),  # top
    (0, 1, 0, 1),  # bottom
    (0, 0, 1, 0),  # near
    (0, 0, -1, 1)  # far
)


def _cull(a, b, c):
    t = (b[0][0] - a[0][0]) * (c[0][1] - a[0][1]) - \
        (b[0][1] - a[0][1]) * (c[0][0] - a[0][0])
    return t >= 0


def _render_triangle(rgb, z, dims, vertices, material):
    for p in _CLIP_PLANES:
        vertices = _clip_to_plane(p, vertices)
        if len(vertices) < 3:
            return

    projected = [_project_vertex(v) for v in vertices]
    if _cull(projected[0], projected[1], projected[2]):
        return

    for i in range(2, len(projected)):
        _render_triangle_2(
            rgb, z, dims, projected[0], projected[i - 1], projected[i], material)


def _transform_position(m, v):
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2] + m[0][3],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2] + m[1][3],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2] + m[2][3],
        m[3][0] * v[0] + m[3][1] * v[1] + m[3][2] * v[2] + m[3][3]
    )


def _transform_vertex(m, v):
    return [_transform_position(m, v[0])] + v[1:]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0]
    )


def _view_matrix(pos, rot):
    angle = -rot[1] * math.pi / 180.0
    f = (math.sin(angle), math.cos(angle), 0)
    u = (0, 0, 1)
    r = _cross(f, u)
    rot = (
        (r[0], r[1], r[2], 0),
        (u[0], u[1], u[2], 0),
        (f[0], f[1], f[2], 0),
        (0, 0, 0, 1)
    )
    return tf.concatenate_matrices(rot, tf.translation_matrix((-pos[0], -pos[1], -pos[2])))


def _projection_matrix(fov, aspect, z_near, z_far):
    fov_rad = fov * math.pi / 180.0
    h = 1 / math.tan(fov_rad * 0.5)
    w = h / aspect
    q = z_far / float(z_far - z_near)
    return (
        (w, 0, 0, 0),
        (0, h, 0, 0),
        (0, 0, q, -q * z_near),
        (0, 0, 1, 0)
    )


class Material:
    def __init__(self, material):
        self.data = material['data'][len('data:image/png;base64,'):]
        self.image = None
        self.levels = {}

    def _get_level(self, level):
        if level not in self.levels:
            if level == 0:
                self.levels[level] = (
                    self.image, self.image.width, self.image.height)
            else:
                (prev, prev_w, prev_h) = self._get_level(level - 1)
                if prev_w > 1 or prev_h > 1:
                    w = int(math.ceil(prev_w / 2))
                    h = int(math.ceil(prev_h / 2))
                    self.levels[level] = (prev.resize((w, h)), w, h)
                else:
                    self.levels[level] = (prev, prev_w, prev_h)
        return self.levels[level]

    def _sample_level(self, uv, level):
        image, width, height = self._get_level(level)
        st = (width * uv[0], height * -uv[1])
        s0 = int(st[0]) % width
        t0 = int(st[1]) % height
        s1 = int(st[0] + 1) % width
        t1 = int(st[1] + 1) % height
        f = (st[0] % 1, st[1] % 1)
        top = _lerp(image.getpixel((s0, t0)), image.getpixel((s1, t0)), f[0])
        bot = _lerp(image.getpixel((s0, t1)), image.getpixel((s1, t1)), f[0])
        return _lerp(top, bot, f[1])

    def sample(self, uv, uv_dx, uv_dy):
        if not self.image:
            self.image = Image.open(io.BytesIO(base64.b64decode(self.data)))
            self.data = None  # release memory

        area = max(max(abs(uv_dx[0]), abs(uv_dx[1])) * self.image.width,
                   max(abs(uv_dy[0]), abs(uv_dy[1])) * self.image.height)
        if area <= 1:
            color = self._sample_level(uv, 0)
        else:
            mip_level = math.log2(area)
            low = math.floor(mip_level)
            color = _lerp(self._sample_level(uv, low),
                          self._sample_level(uv, low + 1), mip_level - low)
        return tuple(int(x) for x in color)


def render_level(surfaces, materials, camera_pos_rot):
    dims = (640, 480)
    rgb = Image.new('RGB', dims)
    z = [1.0] * dims[0] * dims[1]

    view = _view_matrix(camera_pos_rot['pos'], camera_pos_rot['rot'])
    proj = _projection_matrix(
        fov=70, aspect=dims[0] / float(dims[1]), z_near=0.1, z_far=1000)
    view_proj = tf.concatenate_matrices(proj, view)

    loaded_materials = {}
    for surface in surfaces:
        material_index = surface['material']
        if material_index not in loaded_materials:
            loaded_materials[material_index] = Material(
                materials[material_index])
        material = loaded_materials[material_index]
        t = [_transform_vertex(view_proj, [v[0], v[1]])
             for v in surface['vertices']]
        for i in range(2, len(t)):
            _render_triangle(rgb, z, dims, [t[0], t[i], t[i - 1]], material)

    return rgb
