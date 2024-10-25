import re
import sys
import urllib.request

DOWNLOAD_LINK_RE = re.compile(
    br'<meta http-equiv="Refresh" content="2; URL=(/media/levels/files/.+/.+.(zip|ZIP))">')

def _find_download_link(trampoline_html):
    match = DOWNLOAD_LINK_RE.search(trampoline_html)
    return match.group(1).decode() if match else ''

with open(sys.argv[1], 'rt') as f:
    for id in f:
        id = id.strip()
        url = 'https://www.massassi.net/levels/download/' + id
        with urllib.request.urlopen(url) as page:
            zip_url = 'https://www.massassi.net' + _find_download_link(page.read())
            print(id + ',' + zip_url)
