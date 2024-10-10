import io
import struct


def read_from_file(f):
    ident, version, transparency, padding = struct.unpack(
        'Iii52s', f.read(64))
    if ident != 542133571:
        raise Exception("Invalid file!")

    colors = []
    rgbs = f.read(256 * 3)
    for i in range(0, 768, 3):
        colors.append((rgbs[i], rgbs[i + 1], rgbs[i + 2], 255))  # RGBA

    # for i in range(64):
    #     intensities = f.read(256)

    # if transparency:
    #     for i in range(256):
    #         alphas = f.read(256)

    return colors


def read_from_bytes(b):
    return read_from_file(io.BytesIO(b))
