import json
import math
import transformations as tf
import io

from PIL import Image, ImageFilter

import cmp
import gob
import jkl
import mat
import threedo


def _make_material_from_frames(frames, make_lowres):
    # find the average color of the pixels
    if frames[0].width > 1 or frames[0].height > 1:
        pixel = frames[0].resize((1, 1))
    else:
        pixel = frames[0]
    color = pixel.getpixel((0, 0))

    # create an image
    with io.BytesIO() as output:
        if True:  # ALWAYS SHOW THE FIRST FRAME len(frames) == 1:
            mime = 'image/png'
            frames[0].save(output, format='PNG')
        else:
            mime = 'image/gif'
            frames[0].save(output, format='GIF',
                           append_images=frames[1:], save_all=True, duration=1000)

        image = output.getvalue()
        dims = (frames[0].width, frames[0].height)

    mat = {'color': color, 'image': image, 'mime': mime, 'dims': dims}

    if make_lowres:
        with io.BytesIO() as output:
            lowres_frame = frames[0].filter(ImageFilter.GaussianBlur(16))
            lowres_frame.save(output, format='PNG')
            mat['lowres'] = output.getvalue()

    return mat


class MaterialCache:
    def __init__(self, vfs, official):
        self.vfs = vfs
        self.official = official
        self.materials = []
        self.cache = {}

    def set_current_colormap(self, colormap_name, colormap):
        self.colormap_name = colormap_name
        self.colormap = colormap

    def load(self, material_name):
        material_key = '{}_{}'.format(material_name, self.colormap_name)
        if material_key not in self.cache:
            material = None
            for prefix in [b'mat', b'3do/mat']:
                try:
                    material_full_name = prefix + b'/' + material_name
                    frames = mat.load_frames_from_bytes(
                        self.vfs.read(material_full_name), colormap=self.colormap)
                    is_official = self.vfs.src(
                        material_full_name) in self.official
                    material = _make_material_from_frames(frames, is_official)
                    material['name'] = material_full_name
                except KeyError:
                    pass

            self.cache[material_key] = len(self.materials)
            self.materials.append(material)

        return self.cache[material_key]


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
        ret.append([pos, v[1], norm, v[3]])
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
            try:
                material_name = model.materials[surface['material']]
            except:
                continue  # if there's no material, don't render the surface

            surfaces.append({
                'vertices': _transform_vertices(mesh_transform, surface['vertices']),
                'material': texcache.load(material_name)
            })

    for child in node['children']:
        _instantiate_node(surfaces, model, child, transform, texcache)


def _instantiate_model(model, pos, rot, texcache):
    surfaces = []
    transform = tf.concatenate_matrices(tf.translation_matrix(
        pos), _rotation_matrix(rot))
    _instantiate_node(surfaces, model, model.root_node, transform, texcache)
    return surfaces


def _load_level(jkl_name, gobs, official=[]):
    with gob.open_gob_files(gobs) as vfs:
        surfaces = []

        colormaps = {}
        sector_colormaps = {}
        sector_colormap_names = {}

        texcache = MaterialCache(vfs, official)

        level = jkl.read_from_bytes(vfs.read(jkl_name))

        # load sectors
        for k, sector in level.sectors.items():
            colormap_name = level.colormaps[sector['colormap']]
            if not colormap_name in colormaps:
                try:
                    cm = cmp.read_from_bytes(
                        vfs.read(b'misc/cmp/' + colormap_name))
                except:
                    cm = None  # colormap not found
                colormaps[colormap_name] = cm
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

                try:
                    material_name = level.materials[surface['material']]
                except:
                    continue  # if there's no material, don't render the surface

                surfaces.append({
                    'vertices': surface['vertices'],
                    'normal': surface['normal'],
                    'material': texcache.load(material_name)
                })

        # load models and instantiate them in the scene
        models = {}
        model_surfaces = []
        for instance in level.models:
            if instance['sector'] == -1:
                continue  # model is in no sector, TODO: use default colormap?

            filename = instance['model']
            if not filename in models:
                full_filename = b'3do/' + filename
                try:
                    models[filename] = threedo.read_from_bytes(
                        vfs.read(full_filename))
                except:
                    continue  # model not found

            colormap = sector_colormaps[instance['sector']]
            colormap_name = sector_colormap_names[instance['sector']]
            texcache.set_current_colormap(
                colormap_name, colormaps[colormap_name])

            model_surfaces.extend(_instantiate_model(
                models[filename], instance['pos'], instance['rot'], texcache))

        return surfaces, model_surfaces, texcache.materials


def load_level_from_gob(levelname, gob_path):
    OFFICIAL = ['Res1hi.gob', 'Res2.gob', 'JK1.GOB']
    return _load_level(b'jkl/' + levelname, gobs=[gob_path] + OFFICIAL, official=OFFICIAL)
