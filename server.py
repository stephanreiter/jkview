from config import *
from flask import Flask, Response, render_template, request, send_file, send_from_directory
from flask_compress import Compress

import base64
import hashlib
import io
import json
import os
import pygltflib
import re
import requests
import shutil
import struct
import tempfile
import urllib.parse

import episode
import gob
import loader
import models

app = Flask(__name__)
Compress(app)


def _atomically_dump(f, target_path):
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        try:
            shutil.copyfileobj(f, tmp_file)
            shutil.move(tmp_file.name, target_path)
        except:
            os.remove(tmp_file.name)
            raise


def _get_cache_key(zip_url):
    return hashlib.sha1(zip_url.encode("utf-8")).hexdigest()


def _get_cache_filename(zip_url, episode_id, filename):
    cache_key = _get_cache_key(zip_url)
    return os.path.join('cache', '{0}-{1}-{2}'.format(cache_key, episode_id, filename))


def _write_cache_atomically(zip_url, episode_id, filename, mode, data):
    target_path = _get_cache_filename(zip_url, episode_id, filename)
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        try:
            with open(tmp_file.name, mode) as f:
                f.write(data)
            shutil.move(tmp_file.name, target_path)
        except:
            os.remove(tmp_file.name)
            raise


def _encode_image(data, mime):
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode()


def _encode_color(tuple):
    return '#%02x%02x%02x' % (tuple[0], tuple[1], tuple[2])


def _fetch_zip(zip_url):
    cache_key = _get_cache_key(zip_url)

    zip_path = os.path.join('downloads', '{}.zip'.format(cache_key))
    if not os.path.isfile(zip_path):
        r = requests.get(zip_url)
        with io.BytesIO(r.content) as zip_data:
            _atomically_dump(zip_data, zip_path)
    return zip_path


# divide vertex UVs by texture sizes
def _normalize_uvs(surfaces, materials):
    for surf in surfaces:
        mat = materials[surf['material']]
        if mat and 'dims' in mat:
            sclu = 1.0 / mat['dims'][0]
            sclv = 1.0 / mat['dims'][1]
            for v in surf['vertices']:
                v[1] = (v[1][0] * sclu, v[1][1] * sclv)


def _make_materials_for_translucent_surfaces(surfaces, materials):
    variants = {}
    for surf in surfaces:
        mat = surf['material']
        if mat not in variants:
            if surf['translucent']:
                materials[mat]['translucent'] = True
                variants[mat] = {'translucent': mat}
            else:
                variants[mat] = {'normal': mat}
        else:
            if surf['translucent']:
                if 'translucent' not in variants[mat]:
                    copy = materials[mat].copy()
                    copy['translucent'] = True
                    variants[mat]['translucent'] = len(materials)
                    materials.append(copy)
                surf['material'] = variants[mat]['translucent']
            else:
                if 'normal' not in variants[mat]:
                    copy = materials[mat].copy()
                    del copy['translucent']
                    variants[mat]['normal'] = len(materials)
                    materials.append(copy)
                surf['material'] = variants[mat]['normal']


def _add_materials_to_gltf(gltf, materials):
    gltf.extensionsUsed.append('KHR_materials_unlit')
    for mat in materials:
        material = pygltflib.Material()
        if mat and 'image' in mat:
            buffer = pygltflib.Buffer(byteLength=len(mat['image']))
            buffer.uri = 'data:application/octet-stream;base64,' + \
                base64.b64encode(mat['image']).decode()
            buffer_view = pygltflib.BufferView(buffer=len(
                gltf.buffers), byteOffset=0, byteLength=buffer.byteLength)
            image = pygltflib.Image(
                mimeType=mat['mime'], bufferView=len(gltf.bufferViews))
            texture = pygltflib.Texture(source=len(gltf.images))
            material.pbrMetallicRoughness = pygltflib.PbrMetallicRoughness()
            material.pbrMetallicRoughness.baseColorTexture = pygltflib.TextureInfo(
                index=len(gltf.textures))
            if 'translucent' in mat:
                material.alphaMode = pygltflib.BLEND
                material.pbrMetallicRoughness.baseColorFactor = [
                    1.0, 1.0, 1.0, 90.0 / 255.0]
                material.alphaCutoff = None
            else:
                material.alphaMode = pygltflib.MASK
                material.alphaCutoff = 1.0 / 255.0
            material.extensions['KHR_materials_unlit'] = {}
            gltf.buffers.append(buffer)
            gltf.bufferViews.append(buffer_view)
            gltf.images.append(image)
            gltf.textures.append(texture)
        gltf.materials.append(material)


def _add_vertices_to_gltf(gltf, *surface_sources, **kwargs):
    material_to_surfaces = {}
    skip_color = kwargs.get('skip_color', False)

    vertex_data = bytearray()
    total_vertex_count = 0
    bounds = None
    for surfaces in surface_sources:
        for surf in surfaces:
            for v in surf['vertices']:
                if bounds is None:
                    bounds = [v[0], v[0]]
                else:
                    bounds[0] = [min(bounds[0][i], v[0][i]) for i in range(3)]
                    bounds[1] = [max(bounds[1][i], v[0][i]) for i in range(3)]

                vertex_data.extend(struct.pack('fff', *v[0]))
                vertex_data.extend(struct.pack('ff', v[1][0], -v[1][1]))  # flipY
                if not skip_color:
                    for i in range(3):
                        vertex_data.extend(struct.pack(
                            'f', max(0, min(1, v[2][i]))))

            if not surf['material'] in material_to_surfaces:
                material_to_surfaces[surf['material']] = []
            material_to_surfaces[surf['material']].append(
                (total_vertex_count, len(surf['vertices'])))
            total_vertex_count += len(surf['vertices'])

    vertex_data_buffer = pygltflib.Buffer(byteLength=len(vertex_data))
    vertex_data_buffer.uri = 'data:application/octet-stream;base64,' + \
        base64.b64encode(vertex_data).decode()
    vertexByteLength = 4 * (3 + 2) + (0 if skip_color else 4 * 3)
    vertex_data_buffer_view = pygltflib.BufferView(buffer=len(
        gltf.buffers), byteOffset=0, byteStride=vertexByteLength, byteLength=vertex_data_buffer.byteLength, target=pygltflib.ARRAY_BUFFER)
    pos_accessor = pygltflib.Accessor(bufferView=len(gltf.bufferViews), byteOffset=0, count=total_vertex_count,
                                      componentType=pygltflib.FLOAT, type=pygltflib.VEC3, min=bounds[0], max=bounds[1])
    uv_accessor = pygltflib.Accessor(bufferView=len(gltf.bufferViews), byteOffset=4*3,
                                     count=total_vertex_count, componentType=pygltflib.FLOAT, type=pygltflib.VEC2)
    accessors = [len(gltf.accessors), len(gltf.accessors) + 1]
    if not skip_color:
        color_accessor = pygltflib.Accessor(bufferView=len(gltf.bufferViews), byteOffset=4*(
            3+2), count=total_vertex_count, componentType=pygltflib.FLOAT, type=pygltflib.VEC3)
        accessors.append(len(gltf.accessors) + 2)
    gltf.buffers.append(vertex_data_buffer)
    gltf.bufferViews.append(vertex_data_buffer_view)
    gltf.accessors.append(pos_accessor)
    gltf.accessors.append(uv_accessor)
    if not skip_color:
        gltf.accessors.append(color_accessor)

    return material_to_surfaces, accessors


def _add_triangles_to_gltf(gltf, material_to_surfaces, accessors, skip_color=False):
    mesh = pygltflib.Mesh()

    index_data = bytearray()
    total_index_count = 0
    index_data_buffer_view_index = len(gltf.bufferViews)
    for material, surfaces in material_to_surfaces.items():
        local_index_count = 0
        for surface in surfaces:
            base_vertex, local_vertex_count = surface
            for i in range(2, local_vertex_count):
                index_data.extend(struct.pack(
                    'III', base_vertex, base_vertex + i - 1, base_vertex + i))
                local_index_count += 3

        index_accessor_index = len(gltf.accessors)
        index_accessor = pygltflib.Accessor(bufferView=index_data_buffer_view_index, byteOffset=4*total_index_count,
                                            count=local_index_count, componentType=pygltflib.UNSIGNED_INT, type=pygltflib.SCALAR)
        total_index_count += local_index_count
        gltf.accessors.append(index_accessor)

        primitive = pygltflib.Primitive(
            material=material, indices=index_accessor_index)
        primitive.attributes.POSITION = accessors[0]
        primitive.attributes.TEXCOORD_0 = accessors[1]
        if not skip_color:
            primitive.attributes.COLOR_0 = accessors[2]
        mesh.primitives.append(primitive)

    index_data_buffer = pygltflib.Buffer(byteLength=len(index_data))
    index_data_buffer.uri = 'data:application/octet-stream;base64,' + \
        base64.b64encode(index_data).decode()
    index_data_buffer_view = pygltflib.BufferView(buffer=len(
        gltf.buffers), byteOffset=0, byteLength=index_data_buffer.byteLength, target=pygltflib.ELEMENT_ARRAY_BUFFER)
    gltf.buffers.append(index_data_buffer)
    gltf.bufferViews.append(index_data_buffer_view)

    return mesh


def _extract_map(zip_url):
    zip_path = _fetch_zip(zip_url)
    map_info = {'version': VERSION, 'title': 'Unknown', 'maps': []}
    try:
        # read the episode.jk file from the archive
        with gob.open_game_gobs_and_zip(zip_path) as vfs:
            info = episode.read_from_bytes(vfs.zip_gobs.read(b'episode.jk'))
            map_info['title'] = info.title.decode()

            # then try loading the referenced levels
            for levelname in info.levels:
                try:
                    episode_id = len(map_info['maps'])
                    surfaces, model_surfaces, sky_surfaces, materials, spawn_points = loader.load_level(
                        b'jkl/' + levelname, vfs)

                    for src in [surfaces, model_surfaces, sky_surfaces]:
                        _normalize_uvs(src, materials)
                        _make_materials_for_translucent_surfaces(
                            src, materials)

                    gltf = pygltflib.GLTF2()
                    _add_materials_to_gltf(gltf, materials)

                    material_to_surfaces, accessors = _add_vertices_to_gltf(
                        gltf, surfaces, model_surfaces)
                    mesh = _add_triangles_to_gltf(
                        gltf, material_to_surfaces, accessors)
                    node = pygltflib.Node(mesh=len(gltf.meshes))
                    node_index = len(gltf.nodes)
                    gltf.meshes.append(mesh)
                    gltf.nodes.append(node)

                    material_to_surfaces, accessors = _add_vertices_to_gltf(
                        gltf, sky_surfaces)
                    mesh = _add_triangles_to_gltf(
                        gltf, material_to_surfaces, accessors)
                    sky_node = pygltflib.Node(mesh=len(gltf.meshes))
                    sky_node_index = len(gltf.nodes)
                    gltf.meshes.append(mesh)
                    gltf.nodes.append(sky_node)

                    scene = pygltflib.Scene(nodes=[node_index, sky_node_index])
                    gltf.scenes.append(scene)

                    _write_cache_atomically(
                        zip_url, episode_id, 'map.glb', 'wb', b"".join(gltf.save_to_bytes()))

                    if levelname.endswith(b'.jkl'):
                        levelname = levelname[:-4]  # drop .jkl suffix
                    map_info['maps'].append(
                        {'name': levelname.decode(), 'spawnpoints': spawn_points})
                except:
                    if DEVELOPMENT_MODE:
                        raise
                    pass  # try the other maps in the episode
    except:
        if DEVELOPMENT_MODE:
            raise
        pass  # well ... not much we can do
    finally:
        # always write the file to avoid retrying the extraction
        _write_cache_atomically(zip_url, 'all', 'mapinfo.json',
                                'wt', json.dumps(map_info))

    return map_info


def _extract_skin(zip_url):
    zip_path = _fetch_zip(zip_url)

    skin_info = {'version': VERSION, 'skins': []}
    try:
        # read the models.dat from the virtual file system
        with gob.open_game_gobs_and_zip(zip_path) as vfs:
            info = models.read_from_bytes(vfs.read(b'misc/models.dat'))
            # Add single player models for MotS
            info.models.append((b'kk.3do', 'Kyle Katarn'))
            info.models.append((b'mj.3do', 'Mara Jade'))

            # then locate models inside the archive
            model_paths_and_names = [
                m for m in info.models if vfs.zip_gobs.contains(b'3do/' + m[0])]
            if len(model_paths_and_names) == 0:
                # try to discover models in the gob
                model_filename_pattern = re.compile(b'3do/(.+)\.3do')
                for file in vfs.zip_gobs.ls():
                    match = model_filename_pattern.match(file)
                    if match:
                        filename = match.group(1) + b'.3do'
                        model_paths_and_names.append(
                            (filename, filename.decode()))
            if len(model_paths_and_names) == 0:
                # probably just reskins Kyle
                model_paths_and_names.append((b'ky.3do', 'Kyle Katarn'))

            model_paths = [m[0] for m in model_paths_and_names]
            surfaces, materials = loader.load_models(
                model_paths, vfs, throw_on_error=DEVELOPMENT_MODE)

            for i, model in enumerate(model_paths_and_names):
                if surfaces[i] is None:
                    continue

                skin_info['skins'].append(model[1])
                _normalize_uvs(surfaces[i], materials)
                _make_materials_for_translucent_surfaces(
                    surfaces[i], materials)

            gltf = pygltflib.GLTF2()
            _add_materials_to_gltf(gltf, materials)

            for i, model in enumerate(model_paths_and_names):
                material_to_surfaces, accessors = _add_vertices_to_gltf(
                    gltf, surfaces[i], skip_color=True)
                mesh = _add_triangles_to_gltf(
                    gltf, material_to_surfaces, accessors, skip_color=True)
                node = pygltflib.Node(mesh=len(gltf.meshes))
                node_index = len(gltf.nodes)
                gltf.meshes.append(mesh)
                gltf.nodes.append(node)

                scene = pygltflib.Scene(nodes=[node_index])
                gltf.scenes.append(scene)

            _write_cache_atomically(
                zip_url, 0, 'skins.glb', 'wb', b"".join(gltf.save_to_bytes()))
    except:
        if DEVELOPMENT_MODE:
            raise
        pass  # well ... not much we can do
    finally:
        # always write the file to avoid retrying the extraction
        _write_cache_atomically(zip_url, 'all', 'skininfo.json',
                                'wt', json.dumps(skin_info))

    return skin_info


def _is_zip_url_allowed(url):
    for allowed_prefix in ALLOWED_URL_PREFIXES:
        if url.startswith(allowed_prefix):
            return True
    return False


def _get_zip_url():
    parts = urllib.parse.urlparse(request.args.get('url'))
    if not parts or parts.params or parts.query or parts.fragment:
        raise Exception('url missing or not allowed!')
    url = parts.geturl()
    if not url or not _is_zip_url_allowed(url):
        raise Exception('url missing or not allowed!')
    return url


@app.route('/level/map.glb')
def root_level_map_data():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    cache_key = _get_cache_key(zip_url)
    filename = '{0}-{1}-{2}'.format(cache_key, episode_id, 'map.glb')
    return send_from_directory('cache', filename, mimetype="model/gltf-binary")


def _get_mapinfo(zip_url):
    if not DEVELOPMENT_MODE:
        mapinfo_path = _get_cache_filename(zip_url, 'all', 'mapinfo.json')
        if os.path.exists(mapinfo_path):
            with open(mapinfo_path, 'rt') as f:
                map_info = json.loads(f.read())
                if 'version' in map_info and map_info['version'] == VERSION:
                    return map_info  # cached info exists and has correct version. use it!

    return _extract_map(zip_url)


@app.route('/level/')
def root_level_viewer():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    map_info = _get_mapinfo(zip_url)

    map_glb = 'map.glb?version={0}&url={1}&episode={2}'.format(
        VERSION, zip_url, episode_id)

    maps = []
    for i, map in enumerate(map_info['maps']):
        episode_url = '?url={0}&episode={1}'.format(zip_url, i)
        maps.append([map['name'], episode_url])

    try:
        spawn_points = map_info['maps'][episode_id]['spawnpoints']
    except:
        spawn_points = []

    return render_template('viewer.html', title=map_info['title'], maps=json.dumps(maps),
                           spawn_points=json.dumps(spawn_points), map_glb=map_glb)


def _get_skininfo(zip_url):
    if not DEVELOPMENT_MODE:
        skininfo_path = _get_cache_filename(zip_url, 'all', 'skininfo.json')
        if os.path.exists(skininfo_path):
            with open(skininfo_path, 'rt') as f:
                skin_info = json.loads(f.read())
                if 'version' in skin_info and skin_info['version'] == VERSION:
                    return skin_info  # cached info exists and has correct version. use it!

    return _extract_skin(zip_url)


@app.route('/skins/skins.glb')
def root_skin_skins_data():
    zip_url = _get_zip_url()
    cache_key = _get_cache_key(zip_url)
    filename = '{0}-{1}-{2}'.format(cache_key, 0, 'skins.glb')
    return send_from_directory('cache', filename, mimetype="model/gltf-binary")


@app.route('/skins/')
def root_skin_viewer():
    zip_url = _get_zip_url()
    skin_info = _get_skininfo(zip_url)
    skins_glb = 'skins.glb?version={0}&url={1}'.format(VERSION, zip_url)
    return render_template('skinviewer.html', skins=json.dumps(skin_info['skins']), skins_glb=skins_glb)
