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
import subprocess
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


def _run_gltfpack(input_file_name, output_file_name):
    # extra flags:
    # -cc ... produce compressed gltf/glb files
    # -kn ... keep named nodes and meshes attached to named nodes (sky, individual skins)
    # -tc ... create KTX2 compressed textures
    subprocess.run([GLTFPACK_PATH, '-i', input_file_name, '-o',
                   output_file_name, '-cc', '-kn', '-tc'], check=True, timeout=60)


def _write_optimized_glb_to_cache(zip_url, episode_id, filename, data):
    # Write data to a temporary named file, invoke gltfpack to generate another
    # named temporary file. Then move that file atomically to the cache directory
    target_path = _get_cache_filename(zip_url, episode_id, filename)
    with tempfile.NamedTemporaryFile(suffix='.glb', delete=True) as tmp_input_file:
        with open(tmp_input_file.name, 'wb') as f:
            f.write(data)
        with tempfile.NamedTemporaryFile(suffix='.glb', delete=False) as tmp_file:
            try:
                _run_gltfpack(tmp_input_file.name, tmp_file.name)
                shutil.move(tmp_file.name, target_path)
            except:
                os.remove(tmp_file.name)
                raise


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


def _add_surfaces_to_gltf(gltf, *surface_sources, **kwargs):
    mesh = pygltflib.Mesh()
    skip_color = kwargs.get('skip_color', False)

    material_to_surfaces = {}
    for surfaces in surface_sources:
        for surf in surfaces:
            if not surf['material'] in material_to_surfaces:
                material_to_surfaces[surf['material']] = []
            material_to_surfaces[surf['material']].append(surf)

    vertex_data = bytearray()
    vertex_data_buffer_view = len(gltf.bufferViews)
    total_vertex_count = 0
    vertexByteLength = 4 * (3 + 2) + (0 if skip_color else 4 * 3)

    index_data = bytearray()
    index_data_buffer_view_index = len(gltf.bufferViews) + 1
    total_index_count = 0

    for material, surfaces in material_to_surfaces.items():
        bounds = None
        index_count = 0
        vertex_count = 0
        vertex_data_buffer_offset = total_vertex_count * vertexByteLength
        index_data_buffer_offset = total_index_count * 4

        for surf in surfaces:
            for v in surf['vertices']:
                if bounds is None:
                    bounds = [v[0], v[0]]
                else:
                    bounds[0] = [min(bounds[0][i], v[0][i]) for i in range(3)]
                    bounds[1] = [max(bounds[1][i], v[0][i]) for i in range(3)]

                vertex_data.extend(struct.pack('fff', *v[0]))
                vertex_data.extend(struct.pack(
                    'ff', v[1][0], -v[1][1]))  # flipY
                if not skip_color:
                    for i in range(3):
                        vertex_data.extend(struct.pack(
                            'f', max(0, min(1, v[2][i]))))
            local_vertex_count = len(surf['vertices'])

            for i in range(2, local_vertex_count):
                index_data.extend(struct.pack(
                    'III', vertex_count, vertex_count + i - 1, vertex_count + i))
            local_index_count = 3 * (local_vertex_count - 2)

            vertex_count += local_vertex_count
            index_count += local_index_count

        pos_accessor = pygltflib.Accessor(bufferView=vertex_data_buffer_view, byteOffset=vertex_data_buffer_offset, count=vertex_count,
                                          componentType=pygltflib.FLOAT, type=pygltflib.VEC3, min=bounds[0], max=bounds[1])
        uv_accessor = pygltflib.Accessor(bufferView=vertex_data_buffer_view, byteOffset=4*3+vertex_data_buffer_offset,
                                         count=vertex_count, componentType=pygltflib.FLOAT, type=pygltflib.VEC2)
        accessors = [len(gltf.accessors), len(gltf.accessors) + 1]
        gltf.accessors.append(pos_accessor)
        gltf.accessors.append(uv_accessor)
        if not skip_color:
            color_accessor = pygltflib.Accessor(bufferView=vertex_data_buffer_view, byteOffset=4*(
                3+2)+vertex_data_buffer_offset, count=vertex_count, componentType=pygltflib.FLOAT, type=pygltflib.VEC3)
            accessors.append(len(gltf.accessors))
            gltf.accessors.append(color_accessor)

        index_accessor = pygltflib.Accessor(bufferView=index_data_buffer_view_index, byteOffset=index_data_buffer_offset,
                                            count=index_count, componentType=pygltflib.UNSIGNED_INT, type=pygltflib.SCALAR)
        index_accessor_index = len(gltf.accessors)
        gltf.accessors.append(index_accessor)

        primitive = pygltflib.Primitive(
            material=material, indices=index_accessor_index)
        primitive.attributes.POSITION = accessors[0]
        primitive.attributes.TEXCOORD_0 = accessors[1]
        if not skip_color:
            primitive.attributes.COLOR_0 = accessors[2]
        mesh.primitives.append(primitive)

        total_vertex_count += vertex_count
        total_index_count += index_count

    vertex_data_buffer = pygltflib.Buffer(byteLength=len(vertex_data))
    vertex_data_buffer.uri = 'data:application/octet-stream;base64,' + \
        base64.b64encode(vertex_data).decode()
    vertex_data_buffer_view = pygltflib.BufferView(buffer=len(
        gltf.buffers), byteOffset=0, byteStride=vertexByteLength, byteLength=vertex_data_buffer.byteLength, target=pygltflib.ARRAY_BUFFER)
    gltf.buffers.append(vertex_data_buffer)
    gltf.bufferViews.append(vertex_data_buffer_view)

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

                    mesh = _add_surfaces_to_gltf(
                        gltf, surfaces, model_surfaces)
                    node = pygltflib.Node(mesh=len(gltf.meshes))
                    node.name = mesh.name = 'map'
                    nodes = [len(gltf.nodes)]
                    gltf.meshes.append(mesh)
                    gltf.nodes.append(node)

                    if sky_surfaces:
                        mesh = _add_surfaces_to_gltf(gltf, sky_surfaces)
                        sky_node = pygltflib.Node(mesh=len(gltf.meshes))
                        sky_node.name = mesh.name = 'sky'
                        nodes.append(len(gltf.nodes))
                        gltf.meshes.append(mesh)
                        gltf.nodes.append(sky_node)

                    scene = pygltflib.Scene(nodes=nodes)
                    gltf.scenes.append(scene)

                    if GLTFPACK_PATH is None:
                        _write_cache_atomically(
                            zip_url, episode_id, 'map.glb', 'wb', b"".join(gltf.save_to_bytes()))
                    else:
                        _write_optimized_glb_to_cache(
                            zip_url, episode_id, 'map.glb', b"".join(gltf.save_to_bytes()))

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
                mesh = _add_surfaces_to_gltf(
                    gltf, surfaces[i], skip_color=True)
                node = pygltflib.Node(mesh=len(gltf.meshes))
                node.name = mesh.name = f'skin_{i}'
                node_index = len(gltf.nodes)
                gltf.meshes.append(mesh)
                gltf.nodes.append(node)

                scene = pygltflib.Scene(nodes=[node_index])
                gltf.scenes.append(scene)

            if GLTFPACK_PATH is None:
                _write_cache_atomically(
                    zip_url, 0, 'skins.glb', 'wb', b"".join(gltf.save_to_bytes()))
            else:
                _write_optimized_glb_to_cache(
                    zip_url, 0, 'skins.glb', b"".join(gltf.save_to_bytes()))
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

    gltfpacked = GLTFPACK_PATH is not None
    return render_template('viewer.html', title=map_info['title'], maps=json.dumps(maps),
                           spawn_points=json.dumps(spawn_points), map_glb=map_glb, gltfpacked=gltfpacked)


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
    gltfpacked = GLTFPACK_PATH is not None
    return render_template('skinviewer.html', skins=json.dumps(skin_info['skins']), skins_glb=skins_glb, gltfpacked=gltfpacked)
