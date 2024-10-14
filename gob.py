import os
import struct
import sys
import zipfile

OFFICIAL = ['Res1hi.gob', 'Res2.gob', 'JKMRES.GOO']


class GobFile:
    def __init__(self, file_handle):
        self.file_handle = file_handle

        ident, offsetFirstFileSize, offsetCout, numberFiles = struct.unpack(
            'Iiii', file_handle.read(16))
        if ident != 541216583 or offsetFirstFileSize != 20 or offsetCout != 12:
            raise Exception("Invalid file!")

        self.toc = {}
        for _ in range(numberFiles):
            offset, length, name = struct.unpack(
                'ii128s', file_handle.read(136))
            idx = name.find(b'\0')
            if idx != -1:
                name = name[:idx]
            name = name.lower()  # CASE INSENSITIVE
            name = name.replace(b'\\', b'/')  # use forward slashes
            self.toc[name] = (offset, length)

            # WORK AROUND FOR SOME MAPS
            # the JK engine doesn't support spaces in filenames
            # if encountered, anything from the space on is ignored
            # so give this file a second name; this fixes loading of massassi map #753
            if b' ' in name:
                name = name.split(b' ')[0]
                if not name in self.toc:
                    self.toc[name] = (offset, length)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.file_handle.close()

    def ls(self):
        return self.toc.keys()

    def contains(self, name):
        name = name.lower()  # CASE INSENSITIVE
        return name in self.toc

    def read(self, name):
        name = name.lower()  # CASE INSENSITIVE
        offset, length = self.toc[name]
        self.file_handle.seek(offset)
        return self.file_handle.read(length)


def open_gob_file(filename):
    f = open(filename, 'rb')
    try:
        return GobFile(f)
    except:
        f.close()
        raise


class MultiGob:
    def __init__(self, gobs):
        toc = {}
        for gob in gobs:
            for filename in gob.ls():
                toc[filename] = gob
        self.toc = toc

    def ls(self):
        return self.toc.keys()

    def contains(self, name):
        name = name.lower()  # CASE INSENSITIVE
        return name in self.toc

    def read(self, name):
        name = name.lower()  # CASE INSENSITIVE
        return self.toc[name].read(name)


class VirtualFileSystem:
    def __init__(self, zip_handle, zip_gobs, official_gobs):
        self.zip_handle = zip_handle
        self.zip_gobs = MultiGob(zip_gobs)
        # Order matters: let level specific gobs override official resources.
        # This is relevant, for example, for the Blue Rain level (375): it has its own 3do/tree.3do.
        self.gobs = official_gobs + zip_gobs
        self.multi_gob = MultiGob(self.gobs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        for gob in self.gobs:
            try:
                gob.close()
            except:
                pass
        try:
            self.zip_handle.close()
        except:
            pass

    def ls(self):
        return self.multi_gob.ls()

    def contains(self, name):
        return self.multi_gob.contains(name)

    def read(self, name):
        return self.multi_gob.read(name)


def _open_gobs_in_zip(zip_filename):
    zip_file = zipfile.ZipFile(zip_filename)
    try:
        open_files = []
        try:
            # locate gob and goo (MotS) files and open them
            gobs = []
            for info in zip_file.infolist():
                # case insensitive extension check:
                filename = info.filename.lower()
                if filename.endswith('.gob') or filename.endswith('.goo'):
                    gob_file_handle = zip_file.open(info)
                    open_files.append(gob_file_handle)
                    gobs.append(GobFile(gob_file_handle))
            if len(gobs) == 0:
                raise Exception("No GOBs in archive!")
            return zip_file, gobs
        except:
            for f in open_files:
                f.close()
            raise
    except:
        zip_file.close()
        raise


def open_game_gobs_and_zip(zip_filename):
    zip_handle, zip_gobs = _open_gobs_in_zip(zip_filename)

    official_gobs = []
    for filename in OFFICIAL:
        try:
            official_gobs.append(open_gob_file(filename))
        except:
            pass  # if not found, ignore it

    return VirtualFileSystem(zip_handle, zip_gobs, official_gobs)


if __name__ == "__main__":
    filename = sys.argv[1]

    if os.path.splitext(filename)[1] == ".zip":
        _, gobs = _open_gobs_in_zip(filename)
    else:
        gobs = [open_gob_file(filename)]

    for gobfile in gobs:
        for name in gobfile.ls():
            data = gobfile.read(name)
            print(name, len(data), file=sys.stderr)
        os.write(1, gobfile.read(sys.argv[2].encode()))  # write to stdout
