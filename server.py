from flask import Flask, Response, render_template, request
from flask_caching import Cache
from flask_compress import Compress

import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile

import loader

app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})
Compress(app)

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


@app.route('/level/<level_id>.js')
@cache.cached(timeout=50, query_string=True)
def level_data(level_id):
    level_id = int(level_id)  # sanitize
    censor = request.args.get('ownsgame') != '1'

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
            for info in level_zip.infolist():
                if info.filename.endswith('.gob'):
                    with level_zip.open(info) as gob_file:
                        _atomically_dump(gob_file, gob_path)
                    break

    surfaces, model_surfaces, texture_data = loader.load_level_from_file(
        gob_path, censor=censor)

    texsizes = []
    for tex in texture_data:
        texsizes.append(tex[2])

    result = 'texsizes = ' + json.dumps(texsizes) + ';\n'
    result += 'surfaces = ' + json.dumps(surfaces) + ';\n'
    result += 'model_surfaces = ' + json.dumps(model_surfaces) + ';\n'
    result += 'textures = ' + json.dumps(texture_data) + ';\n'
    return Response(result, mimetype='application/javascript')


@app.route('/level/<level_id>.html')
def level_viewer(level_id):
    js_path = level_id + '.js'
    if request.args.get('ownsgame') == '1':
        js_path += '?ownsgame=1'
    return render_template('viewer.html', level_js=js_path)
