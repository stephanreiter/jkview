import io
import re
import struct

SUBSECTION_RE = re.compile(br'(.+)\s+\d+\Z')  # [ITEM TYPE] [COUNT]EOL
ITEM_RE = re.compile(br'(\d+):')  # [IDX]: ...
CMP_RE = re.compile(br'(\d+):\s+(\S+)')  # [IDX]: [FILENAME]
# [IDX]: [FILENAME] 1 1
MATERIAL_RE = re.compile(
    br'(\d+):\s+(\S+)\s+(-?\d*(\.\d+)?)\s+(-?\d*(\.\d+)?)')
POSXYZ_RE = re.compile(
    br'(\d+):\s+(-?\d*(\.\d+)?)\s+(-?\d*(\.\d+)?)\s+(-?\d*(\.\d+)?)')  # [IDX]: [F] [F] [F]
TEXUV_RE = re.compile(
    br'(\d+):\s+(-?\d*(\.\d+)?)\s+(-?\d*(\.\d+)?)')  # [IDX]: [F] [F]
SURFACE_RE = re.compile(
    br'(\d+):\s+(\-?\d+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(\-?\d+)\s+(\-?\d+)\s+(\-?\d+)\s+(\-?\d+)\s+(-?\d*(\.\d+)?)\s+(\-?\d+)')

SECTOR_RE = re.compile(br'SECTOR\s+(\d+)')
SECTOR_COLORMAP_RE = re.compile(br'COLORMAP\s+(\d+)')
SECTOR_SURFACES_RE = re.compile(br'SURFACES\s+(\d+)\s+(\d+)')
#SECTOR_EXTRALIGHT_RE = re.compile(br'EXTRA\s+LIGHT\s+(-?\d*(\.\d+)?)')
#SECTOR_TINT_RE = re.compile(br'TINT\s+(-?\d*(\.\d+)?)\s+(-?\d*(\.\d+)?)\s+(-?\d*(\.\d+)?)')


def _get_surface_rest_re(nverts):
    text = br'\s+'
    text += br'(-?\d+),(-?\d+)\s+' * nverts
    text += br'(-?\d*(\.\d+)?)\s+' * (nverts - 1)
    text += br'(-?\d*(\.\d+)?)'  # no trailing whitespace
    rest_re = re.compile(text)
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


class JklFile:
    def __init__(self, sections):
        self._read_materials(sections[b'materials'])
        self._read_georesource(sections[b'georesource'])
        self._read_sectors(sections[b'sectors'])
        self._prune_materials()

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
        for line in ss[b'world surfaces']:
            match = SURFACE_RE.match(line)
            if match:
                key = int(match.group(1))
                mat = int(match.group(2))
                surfflags = int(match.group(3)[2:], 16)
                faceflags = int(match.group(4)[2:], 16)
                geo = int(match.group(5))
                #light = int(match.group(6))
                #tex = int(match.group(7))
                #adjoin = int(match.group(8))
                extra_light = float(match.group(9))
                nverts = int(match.group(11))

                rest_re = _get_surface_rest_re(nverts)
                rest = line[match.end(11):]
                match = rest_re.match(rest)

                uv_scale = 0.5 if (surfflags & 0x10) else 1
                uv_scale *= 2 if (surfflags & 0x20) else 1
                uv_scale *= 8 if (surfflags & 0x40) else 1

                vertices = []
                for i in range(nverts):
                    xyz_idx = int(match.group(2 * i + 1))
                    uv_idx = int(match.group(2 * i + 2))
                    intensity = float(match.group(2 * nverts + i * 2 + 1))

                    uv = (0.0, 0.0) if uv_idx == - \
                        1 else (uvs[uv_idx][0] * uv_scale, uvs[uv_idx][1] * uv_scale)
                    diffuse = min(intensity, 1.0) + extra_light
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

            match = SECTOR_COLORMAP_RE.match(line)
            if match:
                cur['colormap'] = int(match.group(1))

            match = SECTOR_SURFACES_RE.match(line)
            if match:
                first = int(match.group(1))
                cur['surfaces'] = (first, first + int(match.group(2)))

            #match = SECTOR_EXTRALIGHT_RE.match(line)
            #if match:
            #    cur['extra_light'] = float(match.group(1))

            #match = SECTOR_TINT_RE.match(line)
            #if match:
            #    b = float(match.group(1))
            #    g = float(match.group(3))
            #    r = float(match.group(5))
            #    cur['tint'] = (r, g, b)

        self.sectors = sectors


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
    return None


def read_from_file(f):
        sections = {}
        for line in f:
            line = _strip(line)
            while _defines_section(line):
                section = line[8:].strip().lower()
                section_lines = []
                line = _parse_section(section_lines, f, section)
                sections[section] = section_lines
        return JklFile(sections)


def read_from_bytes(b):
    return read_from_file(io.BytesIO(b))
