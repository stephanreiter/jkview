from config import *
from flask import Flask, Response, render_template, request, send_file, send_from_directory
from flask_compress import Compress

import base64
import hashlib
import io
import json
import os
import re
import requests
import shutil
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


def _extract_map(zip_url):
    zip_path = _fetch_zip(zip_url)
    map_info = {'version': VERSION, 'title': 'Unknown', 'maps': []}
    try:
        # read the episode.jk file from the archive
        with gob.open_game_gobs_and_zip(zip_path) as vfs:
            info = episode.read_from_bytes(vfs.zip_gobs.read(b'episode.jk'))
            map_info['title'] = info.title.decode()

            # then try loading the references levels
            for levelname in info.levels:
                try:
                    episode_id = len(map_info['maps'])
                    surfaces, model_surfaces, sky_surfaces, materials, spawn_points = loader.load_level(
                        b'jkl/' + levelname, vfs)

                    # divide vertex UVs by texture sizes
                    for src in [surfaces, model_surfaces, sky_surfaces]:
                        for surf in src:
                            mat = materials[surf['material']]
                            if mat and 'dims' in mat:
                                sclu = 1.0 / mat['dims'][0]
                                sclv = 1.0 / mat['dims'][1]
                                for v in surf['vertices']:
                                    v[1] = (v[1][0] * sclu, v[1][1] * sclv)

                    # write material data to mat.js
                    material_data = []
                    for mat in materials:
                        if mat:
                            data = _encode_image(mat['image'], mat['mime'])
                            material_data.append(
                                {'data': data, 'name': mat['name'].decode()})
                        else:
                            material_data.append({'data': '', 'name': ''})
                    _write_cache_atomically(
                        zip_url, episode_id, 'mat.json', 'wt', json.dumps(material_data))

                    # assemble map data
                    material_colors = [_encode_color(
                        mat['color']) if mat else '#000000' for mat in materials]
                    map_data = {'surfaces': surfaces, 'model_surfaces': model_surfaces, 'sky_surfaces': sky_surfaces,
                                'material_colors': material_colors, 'spawn_points': spawn_points}
                    _write_cache_atomically(
                        zip_url, episode_id, 'map.json', 'wt', json.dumps(map_data))

                    if levelname.endswith(b'.jkl'):
                        levelname = levelname[:-4]  # drop .jkl suffix
                    map_info['maps'].append(levelname.decode())
                except:
                    if DEVELOPMENT_MODE: raise
                    pass  # try the other maps in the episode
    except:
        if DEVELOPMENT_MODE: raise
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
            surfaces, materials = loader.load_models(model_paths, vfs, throw_on_error=DEVELOPMENT_MODE)

            for i, model in enumerate(model_paths_and_names):
                if surfaces[i] is None:
                    continue

                skin_info['skins'].append(model[1])

                # divide vertex UVs by texture sizes
                for surf in surfaces[i]:
                    mat = materials[surf['material']]
                    if mat and 'dims' in mat:
                        sclu = 1.0 / mat['dims'][0]
                        sclv = 1.0 / mat['dims'][1]
                        for v in surf['vertices']:
                            v[1] = (v[1][0] * sclu, v[1][1] * sclv)

            # write material data to mat.js
            material_data = []
            for mat in materials:
                if mat:
                    data = _encode_image(mat['image'], mat['mime']) if (
                        'image' in mat) else ''
                    material_data.append(
                        {'data': data, 'name': mat['name'].decode()})
                else:
                    material_data.append({'data': '', 'name': ''})
            _write_cache_atomically(
                zip_url, 0, 'skinmat.json', 'wt', json.dumps(material_data))

            # assemble skins data
            material_colors = [_encode_color(
                mat['color']) if mat else '#000000' for mat in materials]
            skins_data = {'surfaces': [
                s for s in surfaces if s is not None], 'material_colors': material_colors}
            _write_cache_atomically(
                zip_url, 0, 'skins.json', 'wt', json.dumps(skins_data))
    except:
        if DEVELOPMENT_MODE: raise
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


@app.route('/level/map.json')
def root_level_map_data():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    cache_key = _get_cache_key(zip_url)
    return send_from_directory('cache', '{0}-{1}-{2}'.format(cache_key, episode_id, 'map.json'))


@app.route('/level/mat.json')
def root_level_material_data():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    cache_key = _get_cache_key(zip_url)
    return send_from_directory('cache', '{0}-{1}-{2}'.format(cache_key, episode_id, 'mat.json'))


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

    mapjs_url = 'map.json?version={0}&url={1}&episode={2}'.format(
        VERSION, zip_url, episode_id)
    matjs_url = 'mat.json?version={0}&url={1}&episode={2}'.format(
        VERSION, zip_url, episode_id)

    maps = []
    for i, map in enumerate(map_info['maps']):
        episode_url = '?url={0}&episode={1}'.format(zip_url, i)
        maps.append([map, episode_url])

    return render_template('viewer.html', title=map_info['title'], maps=json.dumps(maps),
                           map_js=mapjs_url, mat_js=matjs_url)


def _get_skininfo(zip_url):
    if not DEVELOPMENT_MODE:
        skininfo_path = _get_cache_filename(zip_url, 'all', 'skininfo.json')
        if os.path.exists(skininfo_path):
            with open(skininfo_path, 'rt') as f:
                skin_info = json.loads(f.read())
                if 'version' in skin_info and skin_info['version'] == VERSION:
                    return skin_info  # cached info exists and has correct version. use it!

    return _extract_skin(zip_url)


@app.route('/skins/skins.json')
def root_skin_skins_data():
    zip_url = _get_zip_url()
    cache_key = _get_cache_key(zip_url)
    return send_from_directory('cache', '{0}-{1}-{2}'.format(cache_key, 0, 'skins.json'))


@app.route('/skins/mat.json')
def root_skin_material_data():
    zip_url = _get_zip_url()
    cache_key = _get_cache_key(zip_url)
    return send_from_directory('cache', '{0}-{1}-{2}'.format(cache_key, 0, 'skinmat.json'))


@app.route('/skins/')
def root_skin_viewer():
    zip_url = _get_zip_url()
    skin_info = _get_skininfo(zip_url)
    skinsjs_url = 'skins.json?version={0}&url={1}'.format(VERSION, zip_url)
    matjs_url = 'mat.json?version={0}&url={1}'.format(VERSION, zip_url)
    return render_template('skinviewer.html', skins=json.dumps(skin_info['skins']), skins_js=skinsjs_url, mat_js=matjs_url)
