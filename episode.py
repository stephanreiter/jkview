import io
import re
import struct
import sys

#TYPE_RE = re.compile(br'TYPE\s+(\d+)')
#SEQ_RE = re.compile(br'SEQ\s+(\d+)')
ITEM_RE = re.compile(
    br'(\d+):\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)')


class EpisodeFile:
    def __init__(self, title, levels):
        if title and title[0] == '"' and title[-1] == '"':
            title = title[1:-1]
        self.title = title
        self.levels = levels


def _strip(line):
    start = line.find(b'#')
    if start != -1:
        line = line[:start]
    return line.strip()


def read_from_file(f):
        title = None
        levels = []
        for line in f:
            line = _strip(line)
            if not line:
                continue

            # first line is the title
            if title is None:
                title = line
                continue

            match = ITEM_RE.match(line)
            if match:
                key = int(match.group(1))
                item_type = match.group(4)
                if item_type == b"LEVEL":
                    filename = match.group(5)
                    levels.append(filename)

        return EpisodeFile(title, levels)


def read_from_bytes(b):
    return read_from_file(io.BytesIO(b))


if __name__ == "__main__":
    with open(sys.argv[1], 'rt') as f:
        episode = read_from_file(f)
        print(episode.title, episode.levels)
