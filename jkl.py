import io
import re

SUBSECTION_RE = re.compile(br'(.+)\s+\d+\Z')  # [ITEM TYPE] [COUNT]EOL
ITEM_RE = re.compile(br'(\d+):')  # [IDX]: ...
CMP_RE = re.compile(br'(\d+):\s+(\S+)')  # [IDX]: [FILENAME]
# [IDX]: [FILENAME] 1 1
FLOAT_FRAGMENT = r'-?\d*(\.\d+)?(?:[eE][-+]?\d+)?'
FLOAT_FRAGMENT2 = r'-?\d*(?:\.\d+)?(?:[eE][-+]?\d+)?'
VECTOR_RE = re.compile(
    fr'\(({FLOAT_FRAGMENT2})/({FLOAT_FRAGMENT2})/({FLOAT_FRAGMENT2})\)'.encode())
MATERIAL_RE = re.compile(
    fr'(\d+):\s+(\S+)\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})'.encode())
POSXYZ_RE = re.compile(
    fr'(\d+):\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})'.encode())  # [IDX]: [F] [F] [F]
TEXUV_RE = re.compile(
    fr'(\d+):\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})'.encode())  # [IDX]: [F] [F]
SURFACE_RE = re.compile(
    fr'(\d+):\s+(\-?\d+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(\-?\d+)\s+(\-?\d+)\s+(\-?\d+)\s+(\-?\d+)\s+({FLOAT_FRAGMENT})\s+(\-?\d+)'.encode())

SECTOR_RE = re.compile(br'SECTOR\s+(\d+)')
SECTOR_COLORMAP_RE = re.compile(br'COLORMAP\s+(\d+)')
SECTOR_SURFACES_RE = re.compile(br'SURFACES\s+(\d+)\s+(\d+)')
SECTOR_EXTRA_LIGHT_RE = re.compile(
    fr'EXTRA\s+LIGHT\s+({FLOAT_FRAGMENT2})'.encode())

TEMPLATE_RE = re.compile(br'(\S+)\s+(\S+)\s+(\D.+)\Z')
THING_RE = re.compile(
    fr'(\d+):\s+(\S+)\s+(\S+)\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})\s+({FLOAT_FRAGMENT})\s+(-?\d+)(\s+-?\d+)?\s*(.*)'.encode())


def _get_surface_rest_re(nverts, mots):
    text = r'\s+'
    text += r'(-?\d+),\s*(-?\d+)\s+' * nverts
    if mots:
        text += fr'({FLOAT_FRAGMENT2})\s+' * nverts * 3
    text += fr'({FLOAT_FRAGMENT2})\s+' * (nverts - 1)
    text += fr'({FLOAT_FRAGMENT2})'  # no trailing whitespace
    rest_re = re.compile(text.encode())
    return rest_re


def _parse_subsections(lines):
    ss = {}
    cur = []
    for line in lines:
        if ITEM_RE.match(line):
            cur.append(line)
        else:
            match = SUBSECTION_RE.match(line)
            if match:
                name = match.group(1).strip().lower()
                ss[name] = cur = []
    return ss


def _get_light_config(config):
    if b'thingflags' not in config:
        return None
    if int(config[b'thingflags'][2:], 16) & 0x1 == 0:  # does not emit light
        return None

    light = float(config.get(b'light', b'0.0'))
    if light <= 0:
        return None

    match = VECTOR_RE.match(config.get(b'lightoffset', b''))
    if match:
        offset = (float(match.group(1)), float(
            match.group(2)), float(match.group(3)))
    else:
        offset = (0, 0, 0)
    intensity = float(config.get(b'lightintensity', b'1.0'))

    return {'light': light, 'intensity': intensity, 'offset': offset}


class JklFile:
    def __init__(self, sections):
        self._read_materials(sections[b'materials'])
        self._read_georesource(sections[b'georesource'])
        self._read_sectors(sections[b'sectors'])
        self._prune_materials()

        templates = self._read_templates(sections[b'templates'])
        self._read_things(sections[b'things'], templates)

    def _prune_materials(self):
        used_materials = {}
        for k, s in self.surfaces.items():
            mat = s['material']
            used_materials[mat] = True

        for k in list(self.materials.keys()):
            if not k in used_materials:
                del self.materials[k]

    def _read_materials(self, lines):
        ss = _parse_subsections(lines)

        mats = {}
        for line in ss[b'world materials']:
            match = MATERIAL_RE.match(line)
            key = int(match.group(1))
            name = match.group(2)
            mats[key] = name
        self.materials = mats

    def _read_georesource(self, lines):
        ss = _parse_subsections(lines)

        xyzs = {}
        for line in ss[b'world vertices']:
            match = POSXYZ_RE.match(line)
            key = int(match.group(1))
            x = float(match.group(2))
            y = float(match.group(4))
            z = float(match.group(6))
            xyzs[key] = (x, y, z)

        uvs = {}
        for line in ss[b'world texture vertices']:
            match = TEXUV_RE.match(line)
            key = int(match.group(1))
            u = float(match.group(2))
            v = float(match.group(4))
            uvs[key] = (u, v)

        surfaces = {}
        mots = True
        for line in ss[b'world surfaces']:
            match = SURFACE_RE.match(line)
            if match:
                key = int(match.group(1))
                mat = int(match.group(2))
                surfflags = int(match.group(3)[2:], 16)
                # faceflags = int(match.group(4)[2:], 16)
                geo = int(match.group(5))
                light = int(match.group(6))
                # tex = int(match.group(7))
                # adjoin = int(match.group(8))
                extra_light = float(match.group(9))
                nverts = int(match.group(11))

                rest = line[match.end(11):]

                uv_scale = 0.5 if (surfflags & 0x10) else 1
                uv_scale *= 2 if (surfflags & 0x20) else 1
                uv_scale *= 8 if (surfflags & 0x40) else 1
                ignore_lighting = (light == 1)

                rest_re = _get_surface_rest_re(nverts, mots)
                match = rest_re.match(rest)
                if not match and mots:
                    mots = False
                    rest_re = _get_surface_rest_re(nverts, mots)
                    match = rest_re.match(rest)

                vertices = []
                for i in range(nverts):
                    xyz_idx = int(match.group(2 * i + 1))
                    uv_idx = int(match.group(2 * i + 2))

                    uv = (0.0, 0.0) if uv_idx == -1 \
                        else (uvs[uv_idx][0] * uv_scale, uvs[uv_idx][1] * uv_scale)
                    if ignore_lighting:
                        diffuse = (1.0, 1.0, 1.0)
                    elif mots:
                        # TODO? l = float(match.group(2 * nverts + 4 * i + 1))
                        r = float(match.group(2 * nverts + 4 * i + 2))
                        g = float(match.group(2 * nverts + 4 * i + 3))
                        b = float(match.group(2 * nverts + 4 * i + 4))
                        r = min(1, r + extra_light)
                        g = min(1, g + extra_light)
                        b = min(1, b + extra_light)
                        diffuse = (r, g, b)
                    else:
                        l = float(match.group(2 * nverts + i + 1))
                        l = min(1, l + extra_light)
                        diffuse = (l, l, l)
                    vertices.append([xyzs[xyz_idx], uv, diffuse])

                surfaces[key] = {
                    'vertices': vertices,
                    'surfflags': surfflags,
                    'geo': geo,
                    'material': mat
                }

            else:
                match = POSXYZ_RE.match(line)  # normal vector
                key = int(match.group(1))
                x = float(match.group(2))
                y = float(match.group(4))
                z = float(match.group(6))
                surfaces[key]['normal'] = (x, y, z)

        self.surfaces = surfaces

        cmps = {}
        for line in ss[b'world colormaps']:
            match = CMP_RE.match(line)
            key = int(match.group(1))
            cmps[key] = match.group(2)
        self.colormaps = cmps

    def _read_sectors(self, lines):
        sectors = {}

        cur = None
        for line in lines:
            match = SECTOR_RE.match(line)
            if match:
                key = int(match.group(1))
                sectors[key] = cur = {}
                continue

            match = SECTOR_COLORMAP_RE.match(line)
            if match:
                cur['colormap'] = int(match.group(1))
                continue

            match = SECTOR_SURFACES_RE.match(line)
            if match:
                first = int(match.group(1))
                cur['surfaces'] = (first, first + int(match.group(2)))
                continue

            match = SECTOR_EXTRA_LIGHT_RE.match(line)
            if match:
                cur['extra_light'] = float(match.group(1))
                continue

        self.sectors = sectors

    def _read_config(self, text):
        config = {}
        for cfgpair in text.split():
            k, v = cfgpair.split(b'=')
            config[k] = v
        return config

    def _read_templates(self, lines):
        tmpls = {}
        for line in lines:
            match = TEMPLATE_RE.match(line)
            if match:
                name = match.group(1).lower()
                base_name = match.group(2).lower()
                config = self._read_config(match.group(3))
                if base_name in tmpls:
                    # merge: overwrite values of base with new ones
                    config = {**tmpls[base_name], **config}
                tmpls[name] = config
        return tmpls

    def _read_things(self, lines, templates):
        lights = []
        models = []
        spawn_points = []
        for line in lines:
            match = THING_RE.match(line)
            if match:
                # key = int(match.group(1))
                template = match.group(2).lower()
                # name = match.group(3)
                x = float(match.group(4))
                y = float(match.group(6))
                z = float(match.group(8))
                pitch = float(match.group(10))
                yaw = float(match.group(12))
                roll = float(match.group(14))
                sector = int(match.group(16))
                config = self._read_config(
                    match.group(18)) if match.group(18) else {}
                if template in templates:
                    # merge: overwrite values of template with new ones
                    config = {**templates[template], **config}

                if b'model3d' in config:
                    mdl = {'pos': (x, y, z), 'rot': (
                        pitch, yaw, roll), 'sector': sector, 'model': config[b'model3d']}
                    models.append(mdl)
                if b'type' in config and config[b'type'] == b'player':
                    spawn_points.append(
                        {'pos': (x, y, z), 'rot': (pitch, yaw, roll)})
                light = _get_light_config(config)
                if light:
                    light['pos'] = (x, y, z)
                    lights.append(light)

        self.lights = lights
        self.models = models
        self.spawn_points = spawn_points


def _strip(line):
    start = line.find(b'#')
    if start != -1:
        line = line[:start]
    return line.strip()


def _defines_section(line):
    return len(line) > 8 and line[:8].lower() == b'section:'


def _ends_section(line):
    return line == b'end' or _defines_section(line)


def _parse_section(lines, f):
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
            line = _parse_section(section_lines, f)
            sections[section] = section_lines
    return JklFile(sections)


def read_from_bytes(b):
    return read_from_file(io.BytesIO(b))
