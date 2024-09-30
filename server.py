from config import *
from flask import Flask, Response, render_template, request, send_file, send_from_directory
from flask_compress import Compress

import base64
import hashlib
import io
import json
import os
import requests
import shutil
import tempfile
import urllib.parse
import zipfile

import episode
import gob
import loader
import render

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


def _get_level_id(zip_url):
    return hashlib.sha1(zip_url.encode("utf-8")).hexdigest()


def _get_cache_filename(zip_url, episode_id, filename):
    level_id = _get_level_id(zip_url)
    return os.path.join('cache', '{0}-{1}-{2}'.format(level_id, episode_id, filename))


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


def _extract_level(zip_url):
    level_id = _get_level_id(zip_url)

    gob_path = os.path.join('downloads', '{}.gob'.format(level_id))
    if not os.path.isfile(gob_path):
        zip_path = os.path.join('downloads', '{}.zip'.format(level_id))
        if not os.path.isfile(zip_path):
            r = requests.get(zip_url)
            with io.BytesIO(r.content) as level_zip:
                _atomically_dump(level_zip, zip_path)

        with zipfile.ZipFile(zip_path) as level_zip:
            # locate the first gob file and write its contents atomically to gob_path
            # note: MotS used goo as the extension: we'll also take that if found!
            # TODO: deal with multiple gobs in zip, e.g. Rebel Agent (level 1900)
            for info in level_zip.infolist():
                # case insensitive extension check:
                filename = info.filename.lower()
                if filename.endswith('.gob') or filename.endswith('.goo'):
                    with level_zip.open(info) as gob_file:
                        _atomically_dump(gob_file, gob_path)
                    break

    level_info = {'version': VERSION, 'title': 'Unknown', 'maps': []}
    try:
        # read the episode.jk file from the target
        with gob.open_gob_file(gob_path) as vfs:
            info = episode.read_from_bytes(vfs.read(b'episode.jk'))

        level_info['title'] = info.title.decode()

        for levelname in info.levels:
            try:
                episode_id = len(level_info['maps'])

                surfaces, model_surfaces, sky_surfaces, materials, spawn_points = loader.load_level_from_gob(
                    levelname, gob_path)

                # divide vertex UVs by texture sizes
                for src in [surfaces, model_surfaces, sky_surfaces]:
                    for surf in src:
                        mat = materials[surf['material']]
                        if mat and 'dims' in mat:
                            sclu = 1.0 / mat['dims'][0]
                            sclv = 1.0 / mat['dims'][1]
                            for v in surf['vertices']:
                                v[1] = (v[1][0] * sclu, v[1][1] * sclv)

                # write censored and uncensored material data to mat.js
                censor_states = [True] if CENSOR_ALWAYS else [True, False]
                for censor in censor_states:
                    material_data = []
                    for mat in materials:
                        if mat:
                            censored = censor and 'lowres' in mat
                            material_imgkey = 'lowres' if censored else 'image'
                            data = _encode_image(mat[material_imgkey], mat['mime']) if (
                                material_imgkey in mat) else ''
                            material_data.append(
                                {'data': data, 'name': mat['name'].decode()})
                        else:
                            material_data.append({'data': '', 'name': ''})

                    matjs_filename = 'mat{0}.json'.format(
                        '' if censor else '-full')
                    _write_cache_atomically(zip_url, episode_id, matjs_filename,
                                            'wt', json.dumps(material_data))

                # assemble map data
                material_colors = [_encode_color(
                    mat['color']) if mat else '#000000' for mat in materials]
                map_data = {'surfaces': surfaces, 'model_surfaces': model_surfaces, 'sky_surfaces': sky_surfaces,
                            'material_colors': material_colors, 'spawn_points': spawn_points}
                _write_cache_atomically(zip_url, episode_id, 'map.json',
                                        'wt', json.dumps(map_data))

                if levelname.endswith(b'.jkl'):
                    levelname = levelname[:-4]  # drop .jkl suffix
                level_info['maps'].append(levelname.decode())
            except:
                continue  # try the other maps in the episode
    except:
        pass  # well ... not much we can do
    finally:
        # always write the file to avoid retrying the extraction
        _write_cache_atomically(zip_url, 'all', 'mapinfo.json',
                                'wt', json.dumps(level_info))

    return level_info


def _is_zip_url_allowed(url):
    for allowed_prefix in ALLOWED_URL_PREFIXES:
        if url.startswith(allowed_prefix):
            return True
    return False


def _get_zip_url():
    url = urllib.parse.urlparse(request.args.get('url')).geturl()
    if not url or not _is_zip_url_allowed(url):
        raise Exception('url missing or not allowed!')
    return url


@app.route('/level/map.json')
def root_level_map_data():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    level_id = _get_level_id(zip_url)
    return send_from_directory('cache', '{0}-{1}-{2}'.format(level_id, episode_id, 'map.json'))


@app.route('/level/mat.json')
def root_level_material_data():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    level_id = _get_level_id(zip_url)
    censor = CENSOR_ALWAYS or request.args.get('ownsgame') != '1'
    matjs_filename = 'mat{0}.json'.format('' if censor else '-full')
    return send_from_directory('cache', '{0}-{1}-{2}'.format(level_id, episode_id, matjs_filename))


def _get_mapinfo(zip_url):
    if not ALWAYS_REGEN:
        mapinfo_path = _get_cache_filename(zip_url, 'all', 'mapinfo.json')
        if os.path.exists(mapinfo_path):
            with open(mapinfo_path, 'rt') as f:
                level_info = json.loads(f.read())
                if 'version' in level_info and level_info['version'] == VERSION:
                    return level_info  # cached info exists and has correct version. use it!

    return _extract_level(zip_url)


@app.route('/level/')
def root_level_viewer():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    spawn_point_idx = int(request.args.get('spawn', -1))
    level_info = _get_mapinfo(zip_url)

    censor = CENSOR_ALWAYS or request.args.get('ownsgame') != '1'

    mapjs_url = 'map.json?version={0}&url={1}&episode={2}'.format(
        VERSION, zip_url, episode_id)
    if not censor:
        mapjs_url += '&ownsgame=1'

    matjs_url = 'mat.json?version={0}&url={1}&episode={2}'.format(
        VERSION, zip_url, episode_id)
    if not censor:
        matjs_url += '&ownsgame=1'

    maps = []
    for i, map in enumerate(level_info['maps']):
        episode_url = '?url={0}&episode={1}'.format(zip_url, i)
        if not censor:
            episode_url += '&ownsgame=1'
        maps.append([map, episode_url])

    return render_template('viewer.html', title=level_info['title'], maps=json.dumps(maps),
                           map_js=mapjs_url, mat_js=matjs_url, spawn_point_idx=spawn_point_idx)


@app.route('/level/preview.jpg')
def root_level_preview():
    zip_url = _get_zip_url()
    episode_id = int(request.args.get('episode', 0))
    spawn_point_idx = int(request.args.get('spawn', 0))
    _get_mapinfo(zip_url)  # download if needed

    censor = CENSOR_ALWAYS or request.args.get('ownsgame') != '1'

    map_filename = _get_cache_filename(zip_url, episode_id, 'map.json')
    matjs_filename = 'mat{0}.json'.format('' if censor else '-full')
    mat_filename = _get_cache_filename(zip_url, episode_id, matjs_filename)

    with open(map_filename, 'rt') as f:
        map_json = json.loads(f.read())
    with open(mat_filename, 'rt') as f:
        mat_json = json.loads(f.read())
    spawn_point = map_json['spawn_points'][spawn_point_idx]
    image = render.render_level(
        map_json['surfaces'] + map_json['model_surfaces'] + map_json['sky_surfaces'], mat_json, spawn_point)

    output = io.BytesIO()
    image.save(output, format='JPEG')
    output.seek(0)
    return send_file(output, download_name='preview.jpg', mimetype='image/jpeg')
