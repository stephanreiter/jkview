import io
import re
import sys

ITEM_RE = re.compile(br'(\d+):\s+(\S+)\s+(\S+)')


class ModelsFile:
    def __init__(self, models):
        self.models = models


def _strip(line):
    start = line.find(b'#')
    if start != -1:
        line = line[:start]
    return line.strip()


def read_from_file(f):
    models = []
    for line in f:
        stripped_line = _strip(line)
        if not stripped_line:
            continue

        match = ITEM_RE.match(stripped_line)
        if match:
            threedo = match.group(2)
            name_comment_start = line.rfind(b'#')
            if name_comment_start == -1:
                name = "unknown"
            else:
                name = line[name_comment_start + 1:].strip()
                name = name.decode(errors='ignore')
                if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
                    name = name[1:-1]
            models.append((threedo, name))

    return ModelsFile(models)


def read_from_bytes(b):
    return read_from_file(io.BytesIO(b))


if __name__ == "__main__":
    with open(sys.argv[1], 'rt') as f:
        e = read_from_bytes(f.read().encode())
        print(e.models)
