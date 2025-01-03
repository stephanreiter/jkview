import io
import re

SUBSECTION_RE = re.compile(br'(.+)\s+\d+\Z')  # [ITEM TYPE] [COUNT]EOL
ITEM_RE = re.compile(br'(\d+):')  # [IDX]: ...
MAT_RE = re.compile(br'(\d+):\s+(\S+)')  # [IDX]: [FILENAME]
INTEGER_VALUE_RE = re.compile(br'([\D ]*)\s+(-?\d+)\Z')
FLOAT_FRAGMENT2 = r'-?\d*(?:\.\d+)?(?:[eE][-+]?\d+)?'

VERTEX_XYZI_RE = re.compile(
    fr'(\d+):\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})'.encode())
VERTEX_UV_RE = re.compile(
    fr'(\d+):\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})'.encode())
NORMAL_RE = re.compile(
    fr'(\d+):\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})'.encode())
FACE_RE = re.compile(
    fr'(\d+):\s+(-?\d+?)\s+(0x[0-9a-fA-F]+)\s+(-?\d+?)\s+(-?\d+?)\s+(-?\d+?)\s+({FLOAT_FRAGMENT2})\s+(\d+)'.encode())

NODE_RE = re.compile(
    fr'(\d+):\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+({FLOAT_FRAGMENT2})\s+(\D+)'.encode())


def _get_face_rest_re(nverts):
    text = br'\s+'
    text += br'(-?\d+),\s*(-?\d+)\s+' * (nverts - 1)
    text += br'(-?\d+),\s*(-?\d+)'  # no trailing whitespace
    rest_re = re.compile(text)
    return rest_re


class ThreedoFile:
    def __init__(self, sections):
        self._read_materials(sections[b'modelresource'])
        self._read_geometry(sections[b'geometrydef'])
        self._read_hierarchy(sections[b'hierarchydef'])

    def _read_materials(self, lines):
        mats = {}
        for line in lines:
            match = MAT_RE.match(line)
            if match:
                key = int(match.group(1))
                mats[key] = match.group(2)
        self.materials = mats

    def _read_hierarchy(self, lines):
        nodes = {}
        for line in lines:
            match = NODE_RE.match(line)
            if match:
                key = int(match.group(1))
                # flags = int(match.group(2)[2:], 16)
                # ntype = int(match.group(3)[2:], 16)
                mesh = int(match.group(4))
                parent = int(match.group(5))
                # child = int(match.group(6))
                # sibling = int(match.group(7))
                # num_children = int(match.group(8))
                x = float(match.group(9))
                y = float(match.group(10))
                z = float(match.group(11))
                pitch = float(match.group(12))
                yaw = float(match.group(13))
                roll = float(match.group(14))
                pivot_x = float(match.group(15))
                pivot_y = float(match.group(16))
                pivot_z = float(match.group(17))
                # name = match.group(18)

                nodes[key] = {
                    'mesh': mesh,
                    'offset': (x, y, z),
                    'rot': (pitch, yaw, roll),
                    'pivot': (pivot_x, pivot_y, pivot_z),
                    'children': [],
                    'parent': parent
                }

        # build hierarchy and find root
        root_nodes = []
        for _, n in nodes.items():
            p = n['parent']
            if p >= 0:
                nodes[p]['children'].append(n)
            else:
                root_nodes.append(n)
            del n['parent']  # no longer need this bit

        if not root_nodes:
            raise Exception("No root nodes!")

        self.root_nodes = root_nodes

    def _read_geometry(self, lines):
        geosets = {}
        curgeoset = {}
        curmesh = {}
        vdata = {}
        target = ''
        for line in lines:
            sline = line.lstrip()
            if sline.startswith(b'VERTICES'):
                target = 'xyzi'
                continue
            elif sline.startswith(b'TEXTURE VERTICES'):
                target = 'uv'
                continue
            elif sline.startswith(b'VERTEX NORMALS'):
                target = 'norm'
                continue
            elif sline.startswith(b'FACES'):
                target = 'faces'
                continue
            elif sline.startswith(b'FACE NORMALS'):
                target = 'fnorm'
                continue

            match = INTEGER_VALUE_RE.match(line)
            if match:
                target = ''
                name = match.group(1)
                value = int(match.group(2))
                if name == b'GEOSET':
                    geosets[value] = curgeoset = {}
                elif name == b'MESH':
                    curgeoset[value] = curmesh = {}
                    vdata = {'xyzi': {}, 'uv': {}, 'norm': {}}

            if target == 'xyzi':
                match = VERTEX_XYZI_RE.match(line)
                if not match:
                    continue
                key = int(match.group(1))
                x = float(match.group(2))
                y = float(match.group(3))
                z = float(match.group(4))
                i = float(match.group(5))
                vdata[target][key] = (x, y, z, i)
            elif target == 'uv':
                match = VERTEX_UV_RE.match(line)
                if not match:
                    continue
                key = int(match.group(1))
                u = float(match.group(2))
                v = float(match.group(3))
                vdata[target][key] = (u, v)
            elif target == 'norm':
                match = NORMAL_RE.match(line)
                if not match:
                    continue
                key = int(match.group(1))
                x = float(match.group(2))
                y = float(match.group(3))
                z = float(match.group(4))
                vdata[target][key] = (x, y, z)
            elif target == 'faces':
                match = FACE_RE.match(line)
                if not match:
                    continue
                key = int(match.group(1))
                mat = int(match.group(2))
                ftype = int(match.group(3)[2:], 16)
                geo = int(match.group(4))
                # light = int(match.group(5))
                # tex = int(match.group(6))
                extra_light = float(match.group(7))
                nverts = int(match.group(8))

                rest_re = _get_face_rest_re(nverts)
                rest = line[match.end(8):]
                match = rest_re.match(rest)
                if not match:
                    continue

                twosided = (ftype & 0x1) != 0
                translucent = (ftype & 0x2) != 0

                vertices = []
                for i in range(nverts):
                    xyzi_idx = int(match.group(2 * i + 1))
                    uv_idx = int(match.group(2 * i + 2))

                    xyzi = vdata['xyzi'][xyzi_idx]
                    pos = xyzi[0:3]
                    norm = vdata['norm'][xyzi_idx]
                    # check for valid UV index to load skin https://www.massassi.net/levels/files/564.shtml
                    uv = vdata['uv'][uv_idx] if geo == 4 and uv_idx in vdata['uv'] else (0, 0)
                    diffuse = [extra_light + xyzi[3]] * 3
                    vertices.append([pos, uv, diffuse, norm])

                if twosided:
                    for v in reversed(vertices[1:-1]):
                        v = list(v)  # copy vertex to flip normal
                        v[3] = (-v[3][0], -v[3][1], -v[3][2])
                        vertices.append(v)

                curmesh[key] = {'vertices': vertices, 'geo': geo,
                                'material': mat, 'translucent': translucent}

        # keep only the highest resolution (geoset 0)
        self.meshes = geosets[0]


def _strip(line):
    start = line.find(b'#')
    if start != -1:
        line = line[:start]
    return line.strip()


def _defines_section(line):
    return len(line) > 8 and line[:8].lower() == b'section:'


def _ends_section(line):
    return line == b'end' or _defines_section(line)


def _parse_section(lines, f, section):
    for line in f:
        line = _strip(line)
        if not line:
            continue
        elif _ends_section(line):
            return line
        lines.append(line)
    return b''


def read_from_file(f):
    sections = {}
    for line in f:
        line = _strip(line)
        while _defines_section(line):
            section = line[8:].strip().lower()
            section_lines = []
            line = _parse_section(section_lines, f, section)
            sections[section] = section_lines
    return ThreedoFile(sections)


def read_from_bytes(b):
    return read_from_file(io.BytesIO(b))
