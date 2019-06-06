import struct
import sys


class GobFile:
    def __init__(self, filename, toc):
        self.filename = filename
        self.toc = toc

    def open(self):
        self.f = open(self.filename, 'rb')

    def close(self):
        self.f.close()
        del self.f

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()

    def ls(self):
        return self.toc.keys()

    def read(self, name):
        name = name.lower()  # CASE INSENSITIVE
        offset, length = self.toc[name]
        self.f.seek(offset)
        return self.f.read(length)


def open_gob_file(filename):
    with open(filename, 'rb') as f:
        ident, offsetFirstFileSize, offsetCout, numberFiles = struct.unpack(
            'Iiii', f.read(16))
        if ident != 541216583 or offsetFirstFileSize != 20 or offsetCout != 12:
            raise Exception("Invalid file!")

        toc = {}
        for i in range(numberFiles):
            offset, length, name = struct.unpack(
                'ii128s', f.read(136))
            idx = name.find(b'\0')
            if idx != -1:
                name = name[:idx]
            name = name.lower()  # CASE INSENSITIVE
            name = name.replace(b'\\', b'/')  # use forward slashes
            toc[name] = (offset, length)

            # WORK AROUND FOR SOME MAPS
            # the JK engine doesn't support spaces in filenames
            # if encountered, anything from the space on is ignored
            # so give this file a second name; this fixes loading of massassi map #753
            if b' ' in name:
                name = name.split(b' ')[0]
                if not name in toc:
                    toc[name] = (offset, length)

        return GobFile(filename, toc)


class MultiGob:
    def __init__(self, gobs, filenames):
        self.gobs = gobs
        self.filenames = filenames
        toc = {}
        for i in range(len(gobs)):
            for k in gobs[i].ls():
                toc[k] = (gobs[i], filenames[i])
        self.toc = toc

    def __enter__(self):
        opened = []
        try:
            for gob in self.gobs:
                gob.open()
                opened.append(gob)
        except:
            for gob in opened:
                gob.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        for gob in self.gobs:
            try:
                gob.close()
            except:
                pass

    def ls(self):
        return self.toc.keys()

    def src(self, name):
        name = name.lower()  # CASE INSENSITIVE
        return self.toc[name][1]

    def read(self, name):
        name = name.lower()  # CASE INSENSITIVE
        gob = self.toc[name][0]
        return gob.read(name)


def open_gob_files(filenames):
    gobs = [open_gob_file(f) for f in filenames]
    return MultiGob(gobs, filenames)


if __name__ == "__main__":
    with open_gob_file(sys.argv[1]) as gobfile:
        for name in gobfile.ls():
            data = gobfile.read(name)
            print(name, len(data), file=sys.stderr)
        print(gobfile.read(sys.argv[2].encode()).decode())
