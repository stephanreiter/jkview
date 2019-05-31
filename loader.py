import base64
import json

import cmp
import episode
import gob
import jkl
import mat


def _encode_image(data, image_type):
    return 'data:image/' + image_type + ';base64,' + base64.b64encode(data).decode()


def _load_level(jkl_name, gobs, censor=[]):
    with gob.open_gob_files(gobs) as vfs:
        surfaces = []
        textures = []

        material_to_texture = {}

        level = jkl.read_from_bytes(vfs.read(jkl_name))
        for k, sector in level.sectors.items():
            colormap_index = sector['colormap']
            colormap_full_name = b'misc/cmp/' + \
                level.colormaps[colormap_index]
            colormap = cmp.read_from_bytes(vfs.read(colormap_full_name))
            for s in range(sector['surfaces'][0], sector['surfaces'][1]):
                surface = level.surfaces[s]
                if surface['geo'] != 4:
                    continue

                if surface['surfflags'] & 0x200:
                    continue  # horizon
                if surface['surfflags'] & 0x400:
                    continue  # ceiling

                material_index = surface['material']
                material_key = '{}_{}'.format(material_index, colormap_index)
                if material_key not in material_to_texture:
                    material_name = level.materials[material_index]
                    texture = None
                    for prefix in [b'mat', b'3do/mat']:
                        try:
                            material_full_name = prefix + b'/' + material_name
                            do_censor = vfs.src(material_full_name) in censor
                            texture = mat.make_texture_from_bytes(
                                vfs.read(material_full_name), colormap=colormap, censor=do_censor)
                            break
                        except KeyError:
                            pass
                    if not texture:
                        print("Failed to load texture {}".format(material_name))
                    material_to_texture[material_key] = len(textures)
                    textures.append(texture)

                surfaces.append({
                    'vertices': surface['vertices'],
                    'normal': surface['normal'],
                    'texture': material_to_texture[material_key]
                })

        texture_data = []
        for i in range(len(textures)):
            if textures[i]:
                datauri = _encode_image(textures[i][0], textures[i][1])
                frames = textures[i][2]
                texture_data.append((datauri, frames))
            else:
                texture_data.append(('', 1))

        return surfaces, texture_data


def load_level_from_file(target, censor=False):
    # read the episode.jk file from the target
    with gob.open_gob_file(target) as vfs:
        info = episode.read_from_bytes(vfs.read(b'episode.jk'))

    # the load the first level
    OFFICIAL = ['Res1hi.gob', 'Res2.gob', 'JK1.GOB']
    return _load_level(b'jkl/' + info.levels[0], gobs=[target] + OFFICIAL, censor=OFFICIAL if censor else [])
