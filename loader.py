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
        mime = 'image/png'
        frames[0].save(output, format='PNG')
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
        self.colormap_name = ''
        self.colormap = None

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
    x = m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2] + m[0][3] * w
    y = m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2] + m[1][3] * w
    z = m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2] + m[2][3] * w
    return (float(x), float(y), float(z))


def _transform_vertices(transform, vertices):
    ret = []
    for v in vertices:
        pos = _transform_vector(transform, v[0], 1.0)
        norm = _transform_vector(transform, v[3], 0.0)
        ret.append([pos, v[1], v[2], norm])
    return ret


def _add_light(v, l):
    return [
        v[0],
        v[1],
        (min(1, v[2][0] + l), min(1, v[2][1] + l), min(1, v[2][2] + l))
    ]


def _normalize_vector(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length == 0:
        return v
    il = 1.0 / length
    return (v[0] * il, v[1] * il, v[2] * il)


def _apply_lighting(v, sector, lights):
    pos = v[0]
    n = _normalize_vector(v[3])
    total = sector.get('ambient_light', 0)
    for light in lights:
        lpos = (light['pos'][0] + light['offset'][0], light['pos'][1] +
                light['offset'][1], light['pos'][2] + light['offset'][2])
        l = (lpos[0] - pos[0], lpos[1] - pos[1], lpos[2] - pos[2])
        distance = math.sqrt(l[0] * l[0] + l[1] * l[1] + l[2] * l[2])
        # range from https://forums.massassi.net/Editing_Forums/Jedi_Knight_and_Mysteries_of_the_Sith_Editing_Forum/thread_30544_page_1.html
        range = light['intensity'] * 1.25 * 2
        if distance >= range:
            continue
        ndotl = (n[0] * l[0] + n[1] * l[1] + n[2]
                 * l[2]) / distance  # normalize l
        if ndotl > 0:
            total += ndotl * (1 - distance / range) * light['light']
    return _add_light(v, total + sector.get('extra_light', 0))


def _rotation_matrix(rot):
    rot = [r * math.pi / 180.0 for r in rot]  # convert to rad
    return tf.euler_matrix(rot[2], rot[1], rot[0], 'ryzx')


def _instantiate_node(surfaces, model, node, transform, sector, lights, texcache):
    rot = node['rot']
    transform = tf.concatenate_matrices(
        transform, tf.translation_matrix(node['offset']), _rotation_matrix(rot))

    if node['mesh'] != -1:
        mesh_transform = tf.concatenate_matrices(
            transform, tf.translation_matrix(node['pivot']))
        mesh = model.meshes[node['mesh']]
        for _, surface in mesh.items():
            try:
                material_name = model.materials[surface['material']]
            except:
                continue  # if there's no material, don't render the surface

            vertices = _transform_vertices(mesh_transform, surface['vertices'])
            surfaces.append({
                'vertices': [_apply_lighting(v, sector, lights) for v in vertices],
                'material': texcache.load(material_name)
            })

    for child in node['children']:
        _instantiate_node(surfaces, model, child, transform,
                          sector, lights, texcache)


def _instantiate_model(model, pos, rot, sector, lights, texcache):
    surfaces = []
    transform = tf.concatenate_matrices(tf.translation_matrix(
        pos), _rotation_matrix(rot))
    for root_node in model.root_nodes:
        _instantiate_node(surfaces, model, root_node,
                          transform, sector, lights, texcache)
    return surfaces


def _load_level(jkl_name, gobs, official=[]):
    with gob.try_open_gob_files(gobs) as vfs:
        surfaces = []
        sky_surfaces = []

        level = jkl.read_from_bytes(vfs.read(jkl_name))

        # in a previous version we used the per sector colormap for loading materials
        # this made some maps appear with pink textures, e.g. Massassi 3092 and 3051 -
        # thanks ECHOMAN for pointing this out!

        # jdmclark explained that: "Jedi Knight handles colormaps differently depending
        # on whether it's using the software or hardware renderer. In software mode,
        # the game applies the colormap from the camera's containing sector to the whole
        # screen (hardware palettized 8-bit). With the hardware renderer, the game always
        # uses colormap 0 (called the master colormap) to convert textures to 16-bit as
        # they're loaded. The hardware renderer doesn't totally ignore the sector
        # colormaps - it uses some tint fields from their headers for special effects -
        # but the palette part is always ignored.

        # so let's just use the master colormap for all sectors
        texcache = MaterialCache(vfs, official)
        try:
            master_colormap_name = level.colormaps[0]
            master_colormap = cmp.read_from_bytes(
                vfs.read(b'misc/cmp/' + master_colormap_name))
            texcache.set_current_colormap(
                master_colormap_name, master_colormap)
        except:
            pass

        # load sectors
        for _, sector in level.sectors.items():
            for s in range(sector['surfaces'][0], sector['surfaces'][1]):
                surface = level.surfaces[s]
                if surface['geo'] != 4:
                    continue

                try:
                    material_name = level.materials[surface['material']]
                except:
                    continue  # if there's no material, don't render the surface

                surface_data = {
                    'vertices': [_add_light(v, sector.get('extra_light', 0)) for v in surface['vertices']],
                    'material': texcache.load(material_name)
                }

                if surface['surfflags'] & 0x600:  # horizon or ceiling
                    sky_surfaces.append(surface_data)  # horizon
                else:
                    surfaces.append(surface_data)

        # load models and instantiate them in the scene
        models = {}
        model_surfaces = []
        for instance in level.models:
            filename = instance['model']
            if not filename in models:
                full_filename = b'3do/' + filename
                try:
                    models[filename] = threedo.read_from_bytes(
                        vfs.read(full_filename))
                except:
                    continue  # model not found

            sector = level.sectors[instance['sector']]
            model = _instantiate_model(
                models[filename], instance['pos'], instance['rot'], sector, level.lights, texcache)
            model_surfaces.extend(model)

        return surfaces, model_surfaces, sky_surfaces, texcache.materials, level.spawn_points


def load_level_from_gob(levelname, gob_path):
    OFFICIAL = ['Res1hi.gob', 'Res2.gob', 'JKMRES.GOO']
    # Order matters: let level specific gobs override official resources.
    # This is relevant, for example, for the Blue Rain level (375): it has its own 3do/tree.3do.
    gobs = OFFICIAL + [gob_path]
    return _load_level(b'jkl/' + levelname, gobs=gobs, official=OFFICIAL)
