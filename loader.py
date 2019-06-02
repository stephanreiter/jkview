import base64
import json
import math
import transformations as tf

import cmp
import episode
import gob
import jkl
import mat
import threedo


class TextureCache:
    def __init__(self, vfs, censor):
        self.vfs = vfs
        self.censor = censor
        self.textures = []
        self.cache = {}

    def set_current_colormap(self, colormap_name, colormap):
        self.colormap_name = colormap_name
        self.colormap = colormap

    def load(self, material_name):
        material_key = '{}_{}'.format(material_name, self.colormap_name)
        if material_key not in self.cache:
            texture = None
            for prefix in [b'mat', b'3do/mat']:
                try:
                    material_full_name = prefix + b'/' + material_name
                    do_censor = self.vfs.src(material_full_name) in self.censor
                    texture = mat.make_texture_from_bytes(
                        self.vfs.read(material_full_name), colormap=self.colormap, censor=do_censor)
                    break
                except KeyError:
                    pass

            self.cache[material_key] = len(self.textures)
            self.textures.append(texture)
        return self.cache[material_key]


def _encode_image(data, image_type):
    return 'data:image/' + image_type + ';base64,' + base64.b64encode(data).decode()


def _transform_vector(m, v, w):
    r = 0
    x = m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] + m[r][3] * w
    r = 1
    y = m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] + m[r][3] * w
    r = 2
    z = m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] + m[r][3] * w
    return (float(x), float(y), float(z))


def _transform_vertices(transform, vertices):
    ret = []
    for v in vertices:
        pos = _transform_vector(transform, v[0], 1.0)
        norm = _transform_vector(transform, v[2], 0.0)
        ret.append((pos, v[1], norm, v[3]))
    return ret


def _rotation_matrix(rot):
    rot = [r * math.pi / 180.0 for r in rot]  # convert to rad
    return tf.euler_matrix(rot[2], rot[1], rot[0], 'ryzx')


def _instantiate_node(surfaces, model, node, transform, texcache):
    rot = node['rot']
    transform = tf.concatenate_matrices(
        transform, tf.translation_matrix(node['offset']), _rotation_matrix(rot))

    if node['mesh'] != -1:
        mesh_transform = tf.concatenate_matrices(
            transform, tf.translation_matrix(node['pivot']))
        mesh = model.meshes[node['mesh']]
        for k, surface in mesh.items():
            material_name = model.materials[surface['material']]
            surfaces.append({
                'vertices': _transform_vertices(mesh_transform, surface['vertices']),
                'texture': texcache.load(material_name)
            })

    for child in node['children']:
        _instantiate_node(surfaces, model, child, transform, texcache)


def _instantiate_model(model, pos, rot, texcache):
    surfaces = []
    transform = tf.concatenate_matrices(tf.translation_matrix(
        pos), _rotation_matrix(rot))
    _instantiate_node(surfaces, model, model.root_node, transform, texcache)
    return surfaces


def _load_level(jkl_name, gobs, censor=[]):
    with gob.open_gob_files(gobs) as vfs:
        surfaces = []

        colormaps = {}
        sector_colormaps = {}
        sector_colormap_names = {}

        texcache = TextureCache(vfs, censor)

        level = jkl.read_from_bytes(vfs.read(jkl_name))

        # load sectors
        for k, sector in level.sectors.items():
            colormap_name = level.colormaps[sector['colormap']]
            if not colormap_name in colormaps:
                colormaps[colormap_name] = cmp.read_from_bytes(
                    vfs.read(b'misc/cmp/' + colormap_name))
            texcache.set_current_colormap(
                colormap_name, colormaps[colormap_name])

            sector_colormaps[k] = colormaps[colormap_name]
            sector_colormap_names[k] = colormap_name

            for s in range(sector['surfaces'][0], sector['surfaces'][1]):
                surface = level.surfaces[s]
                if surface['geo'] != 4:
                    continue

                if surface['surfflags'] & 0x200:
                    continue  # horizon
                if surface['surfflags'] & 0x400:
                    continue  # ceiling

                material_name = level.materials[surface['material']]

                surfaces.append({
                    'vertices': surface['vertices'],
                    'normal': surface['normal'],
                    'texture': texcache.load(material_name)
                })

        # load models and instantiate them in the scene
        models = {}
        model_surfaces = []
        for instance in level.models:
            if instance['sector'] == -1:
                continue # model is in no sector, TODO: use default colormap?

            filename = instance['model']
            if not filename in models:
                full_filename = b'3do/' + filename
                models[filename] = threedo.read_from_bytes(
                    vfs.read(full_filename))

            colormap = sector_colormaps[instance['sector']]
            colormap_name = sector_colormap_names[instance['sector']]
            texcache.set_current_colormap(
                colormap_name, colormaps[colormap_name])

            model_surfaces.extend(_instantiate_model(
                models[filename], instance['pos'], instance['rot'], texcache))

        texture_data = []
        for texture in texcache.textures:
            if texture:
                datauri = _encode_image(texture[0], texture[1])
                frames = texture[2]
                size = texture[3]
                texture_data.append((datauri, frames, size))
            else:
                texture_data.append(('', 1))

        return surfaces, model_surfaces, texture_data


def load_level_from_file(target, censor=False):
    # read the episode.jk file from the target
    with gob.open_gob_file(target) as vfs:
        info = episode.read_from_bytes(vfs.read(b'episode.jk'))

    # the load the first level
    OFFICIAL = ['Res1hi.gob', 'Res2.gob', 'JK1.GOB']
    return _load_level(b'jkl/' + info.levels[0], gobs=[target] + OFFICIAL, censor=OFFICIAL if censor else [])
