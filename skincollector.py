import re
import sys
import urllib.request

DOWNLOAD_LINK_RE = re.compile(
    br'<meta http-equiv="Refresh" content="2; URL=(/media/levels/files/(jkmod|motsmod)/.+.(zip|ZIP))">')

def _find_download_link(trampoline_html):
    match = DOWNLOAD_LINK_RE.search(trampoline_html)
    return match.group(1).decode() if match else ''

with open(sys.argv[1], 'rt') as f:
    for id in f:
        url = 'https://www.massassi.net/levels/download/' + id
        with urllib.request.urlopen(url) as page:
            zip_url = 'https://www.massassi.net' + _find_download_link(page.read())
            print('<p><a href="http://127.0.0.1:8080/skins/?url=' + zip_url + '">' + zip_url + '</a></p>')
