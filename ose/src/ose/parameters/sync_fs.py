"""Recursive directory events integrated with asyncio, without a polling thread."""
import asyncio
import os
from pathlib import Path


class DirectoryEvents:
    def __init__(self, root, callback):
        try:
            from inotify_simple import INotify, flags
        except ImportError as error:
            raise ValueError('parameter sync requires inotify-simple; install OSE with pip install "ose[sync]"') from error
        self.flags, self.root, self.callback = flags, Path(root), callback
        self.io = INotify(nonblocking=True)
        self.watches = {}
        self.loop = asyncio.get_running_loop()
        try:
            self.scan()
            self.loop.add_reader(self.io.fileno(), self.read)
        except BaseException:
            self.io.close()
            raise

    def add(self, directory):
        flags = self.flags
        mask = (flags.CLOSE_WRITE | flags.MOVED_TO | flags.CREATE | flags.ATTRIB |
                flags.DELETE | flags.MOVED_FROM | flags.DELETE_SELF | flags.MOVE_SELF |
                flags.ONLYDIR | flags.DONT_FOLLOW)
        if directory not in self.watches.values():
            descriptor = self.io.add_watch(directory, mask)
            self.watches[descriptor] = directory

    def ensure(self, directory):
        """Watch only the parent chain created by an export, without a tree scan."""
        current = self.root
        self.add(current)
        for part in directory.relative_to(self.root).parts:
            current = current / part
            self.add(current)

    def scan(self):
        """Install watches before examining each directory, including new subtrees."""
        self.root.mkdir(parents=True, exist_ok=True)
        present, files = set(), []
        def walk_error(error):
            raise error
        for folder, directories, names in os.walk(self.root, followlinks=False, onerror=walk_error):
            directory = Path(folder)
            directories[:] = [name for name in directories if not (directory / name).is_symlink()]
            present.add(directory)
            self.add(directory)
            files.extend(directory / name for name in names if name.endswith('.json'))
        for descriptor, directory in list(self.watches.items()):
            if directory not in present:
                self.watches.pop(descriptor)
                try:
                    self.io.rm_watch(descriptor)
                except OSError:
                    pass  # Kernel may already have removed a deleted directory.
        return files

    def read(self):
        try:
            for event in self.io.read(timeout=0):
                flags = self.flags
                if event.mask & flags.Q_OVERFLOW:
                    self.callback(None)
                    continue
                directory = self.watches.get(event.wd)
                if directory is None:
                    continue
                if event.mask & flags.IGNORED:
                    self.watches.pop(event.wd, None)
                if event.mask & (flags.ISDIR | flags.DELETE_SELF | flags.MOVE_SELF | flags.IGNORED):
                    self.callback(None)
                else:
                    self.callback(directory / event.name)
        except OSError as error:
            self.callback(error)

    def close(self):
        self.loop.remove_reader(self.io.fileno())
        self.io.close()
