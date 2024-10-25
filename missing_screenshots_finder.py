from bs4 import BeautifulSoup

import io
import requests

URL = 'https://www.massassi.net/levels/motsmp/?sortby=name&num=9999'

r = requests.get(URL)
with io.BytesIO(r.content) as fp:
    soup = BeautifulSoup(fp, 'html.parser')

for tag in soup.find_all("div", {"class": "no-screenshot-available"}):
    link = tag.parent
    print(link['href'])
