from flask import Flask, Response, render_template, request, send_file, send_from_directory
from flask_compress import Compress

import base64
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile

import episode
import gob
import loader

app = Flask(__name__)
Compress(app)

CENSOR_ALWAYS = False  # enable to avoid extraction of full-resolution assets on server
VERSION = 2  # increment to invalidate client caches

DOWNLOAD_LINK_RE = re.compile(
    br'<meta http-equiv="Refresh" content="2; URL=(https://www.massassi.net/files/levels/.+.zip)">')


def _find_download_link(trampoline_html):
    match = DOWNLOAD_LINK_RE.search(trampoline_html)
    return match.group(1).decode() if match else ''


def _atomically_dump(f, target_path):
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        try:
            shutil.copyfileobj(f, tmp_file)
            os.rename(tmp_file.name, target_path)
        except:
            os.remove(tmp_file.name)
            raise


def _get_cache_filename(level_id, episode_id, filename):
    return os.path.join('cache', '{0}-{1}-{2}'.format(level_id, episode_id, filename))


def _write_cache_atomically(level_id, episode_id, filename, mode, data):
    target_path = _get_cache_filename(level_id, episode_id, filename)
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        try:
            with open(tmp_file.name, mode) as f:
                f.write(data)
            os.rename(tmp_file.name, target_path)
        except:
            os.remove(tmp_file.name)
            raise


def _encode_image(data, mime):
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode()


def _encode_color(tuple):
    return '#%02x%02x%02x' % (tuple[0], tuple[1], tuple[2])


def _extract_level(level_id):
    level_id = int(level_id)  # sanitize

    gob_path = os.path.join('downloads', '{}.gob'.format(level_id))
    if not os.path.isfile(gob_path):
        zip_path = os.path.join('downloads', '{}.zip'.format(level_id))
        if not os.path.isfile(zip_path):
            url = 'https://www.massassi.net/levels/download_level.php?level_id={}'.format(
                level_id)
            with urllib.request.urlopen(url) as page:
                zip_url = _find_download_link(page.read())
            with urllib.request.urlopen(zip_url) as level_zip:
                _atomically_dump(level_zip, zip_path)

        with zipfile.ZipFile(zip_path) as level_zip:
            # locate the first gob file and write its contents atomically to gob_path
            # note: MotS used goo as the extension: we'll also take that if found!
            for info in level_zip.infolist():
                if info.filename.endswith('.gob') or info.filename.endswith('.goo'):
                    with level_zip.open(info) as gob_file:
                        _atomically_dump(gob_file, gob_path)
                    break

    level_info = {'title': 'Unknown', 'maps': []}
    try:
        # read the episode.jk file from the target
        with gob.open_gob_file(gob_path) as vfs:
            info = episode.read_from_bytes(vfs.read(b'episode.jk'))

        level_info['title'] = info.title.decode()

        for levelname in info.levels:
            episode_id = len(level_info['maps'])

            surfaces, model_surfaces, materials = loader.load_level_from_gob(
                levelname, gob_path)

            # devide vertex UVs by texture sizes
            for src in [surfaces, model_surfaces]:
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
                    else:
                        data = ''
                    material_data.append(data)

                matjs_filename = 'mat{0}.json'.format(
                    '' if censor else '-full')
                _write_cache_atomically(level_id, episode_id, matjs_filename,
                                        'wt', json.dumps(material_data))

            # assemble map data
            material_colors = [_encode_color(
                mat['color']) if mat else '#000000' for mat in materials]
            map_data = {'surfaces': surfaces, 'model_surfaces': model_surfaces,
                        'material_colors': material_colors}
            _write_cache_atomically(level_id, episode_id, 'map.json',
                                    'wt', json.dumps(map_data))

            if levelname.endswith(b'.jkl'):
                levelname = levelname[:-4] # drop .jkl suffix
            level_info['maps'].append(levelname.decode())
    finally:
        # always write the file to avoid retrying the extraction
        _write_cache_atomically(level_id, 'all', 'mapinfo.json',
                                'wt', json.dumps(level_info))


def _serve_map_data(level_id, episode_id):
    mapjs_path = _get_cache_filename(level_id, episode_id, 'map.json')
    return send_from_directory('cache', '{0}-{1}-{2}'.format(level_id, episode_id, 'map.json'))


def _serve_material_data(level_id, episode_id):
    censor = CENSOR_ALWAYS or request.args.get('ownsgame') != '1'
    matjs_filename = 'mat{0}.json'.format('' if censor else '-full')
    return send_from_directory('cache', '{0}-{1}-{2}'.format(level_id, episode_id, matjs_filename))


def _serve_map(level_id, url_has_episode):
    level_id = int(level_id)  # sanitize

    mapinfo_path = _get_cache_filename(level_id, 'all', 'mapinfo.json')
    if not os.path.exists(mapinfo_path):
        _extract_level(level_id)

    with open(mapinfo_path, 'rt') as f:
        level_info = json.loads(f.read())

    censor = CENSOR_ALWAYS or request.args.get('ownsgame') != '1'

    mapjs_url = 'map.json?version={0}'.format(VERSION)
    if not censor:
        mapjs_url += '&ownsgame=1'

    matjs_url = 'mat.json?version={0}'.format(VERSION)
    if not censor:
        matjs_url += '&ownsgame=1'

    ownsgame = '' if censor else '?ownsgame=1'
    link_adjust = '../' if url_has_episode else ''

    return render_template('viewer.html', title=level_info['title'], maps=json.dumps(level_info['maps']),
                           map_js=mapjs_url, mat_js=matjs_url, ownsgame=ownsgame, link_adjust=link_adjust)


@app.route('/level/<int:level_id>/<int:episode_id>/map.json')
def level_map_data(level_id, episode_id):
    return _serve_map_data(level_id, episode_id)


@app.route('/level/<int:level_id>/<int:episode_id>/mat.json')
def level_material_data(level_id, episode_id):
    return _serve_material_data(level_id, episode_id)


@app.route('/level/<int:level_id>/<int:episode_id>/index.html')
def level_viewer(level_id, episode_id):
    return _serve_map(level_id, True)


@app.route('/level/<int:level_id>/map.json')
def root_level_map_data(level_id):
    return _serve_map_data(level_id, 0)


@app.route('/level/<int:level_id>/mat.json')
def root_level_material_data(level_id):
    return _serve_material_data(level_id, 0)


@app.route('/level/<int:level_id>/index.html')
def root_level_viewer(level_id):
    return _serve_map(level_id, False)
