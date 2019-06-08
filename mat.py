import io
import itertools
import struct

from PIL import Image, ImageOps


def _decode_16bit(data, color_extraction):
    sr, sg, sb = [x for x in color_extraction['channel_shifts']]
    mr, mg, mb = [(1 << bits) - 1 for bits in color_extraction['channel_bits']]
    er, eg, eb = [x for x in color_extraction['channel_expands']]

    colors = []
    for i in range(0, len(data), 2):
        val = data[i] | (data[i + 1] << 8)
        r = ((val >> sr) & mr) << er
        g = ((val >> sg) & mg) << eg
        b = ((val >> sb) & mb) << eb
        a = 255
        colors.append((r, g, b, a))
    return colors


def _read_colors(f, color_extraction, count, colormap):
    frames = []
    for i in range(count):
        mat_type, color, res1, res2, res3, res4 = struct.unpack(
            'iIiiii', f.read(24))
        if mat_type != 0:
            raise Exception("Not a color!")

        if colormap:
            pixel_bytes = [colormap[color]]
        else:
            pixel_bytes = [(i, i, i, 255)]

        # pixel_bytes contains a single pixels in RGBA order
        x = bytes(itertools.chain.from_iterable(pixel_bytes))
        img = Image.frombytes('RGBA', (1, 1), x)
        frames.append(img)

    return frames


def _set_alpha(rgba, lhs, rhs):
    return (rgba[0], rgba[1], rgba[2], 0) if lhs == rhs else rgba


def _read_textures(f, color_extraction, count, colormap):
    # load the headers
    for i in range(count):
        mat_type, transparent_color, res1, res2, res3, res4, res5, res6, res7, idx = struct.unpack(
            'iIiiiiiiii', f.read(40))
        if mat_type != 8 or idx != i:
            raise Exception("Not a texture!")

    # load the data
    frames = []
    for i in range(count):
        width, height, has_transparency, res2, res3, mipmaps = struct.unpack(
            'iiiiii', f.read(24))

        ts = transparent_color if has_transparency else -1

        pixel_bytes = None
        org_width = width
        org_height = height
        for j in range(mipmaps):
            if color_extraction['total_bits'] == 8:
                data = f.read(width * height)
                if j == 0:  # only load first mipmap
                    if has_transparency:
                        if colormap:
                            pixel_bytes = [_set_alpha(
                                colormap[i], i, ts) for i in data]
                        else:
                            pixel_bytes = [_set_alpha(
                                (i, i, i, 255), i, ts) for i in data]
                    else:
                        if colormap:
                            pixel_bytes = [colormap[i] for i in data]
                        else:
                            pixel_bytes = [(i, i, i, 255) for i in data]
            elif color_extraction['total_bits'] == 16:
                data = f.read(width * height * 2)
                if j == 0:  # only load first mipmap
                    pixel_bytes = _decode_16bit(data, color_extraction)
            else:
                raise Exception("Invalid file!")

            if width != 1:
                width //= 2
            if height != 1:
                height //= 2

        # pixel_bytes contains the pixels for the largest mipmap level in RGBA order
        x = bytes(itertools.chain.from_iterable(pixel_bytes))
        img = Image.frombytes('RGBA', (org_width, org_height), x)
        img = ImageOps.flip(img)
        frames.append(img)

    return frames


def load_frames_from_file(f, colormap=None):
    ident, version, mat_type, count, res1, res2 = struct.unpack(
        'Iiiiii', f.read(24))
    if ident != 542392653 or version != 50:
        raise Exception("Invalid file!")

    bits, red_bits, green_bits, blue_bits, red_shift, green_shift, blue_shift, red_expand, green_expand, blue_expand, res3, res4, res5 = struct.unpack(
        'iiiiiiiiiiiii', f.read(52))
    color_extraction = {
        'total_bits': bits,
        'channel_bits': (red_bits, green_bits, blue_bits),
        'channel_shifts': (red_shift, green_shift, blue_shift),
        'channel_expands': (red_expand, green_expand, blue_expand)
    }

    if mat_type == 0:
        return _read_colors(f, color_extraction, count, colormap)
    elif mat_type == 2:
        return _read_textures(f, color_extraction, count, colormap)
    else:
        raise Exception("Invalid file!")


def load_frames_from_bytes(b, colormap=None):
    return load_frames_from_file(io.BytesIO(b), colormap=colormap)
