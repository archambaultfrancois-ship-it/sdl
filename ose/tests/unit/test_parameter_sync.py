import copy
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from ose.codec.sdl2 import SDL2
from ose.parameters.cli import read_directory
from ose.parameters.files import MAX_JSON_SIZE, MAX_TOTAL_JSON_SIZE, read_value
from ose.parameters.sync import DirectorySync, Entry
from ose.parameters.values import Values

SDL_ROOT = Path(__file__).resolve().parents[3] / 'sdl'


class JSONLimitsTests(unittest.TestCase):
    def test_extended_defaults_and_configurable_bounded_reads(self):
        self.assertEqual(MAX_JSON_SIZE, 16 * 1024 * 1024)
        self.assertEqual(MAX_TOTAL_JSON_SIZE, 64 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'a.json'
            file.write_bytes(b' ' * (1024 * 1024) + b'{"a": {}}')
            self.assertEqual(read_value(file), {'a': {}})
            with self.assertRaisesRegex(ValueError, 'exceeds 1024 bytes'):
                read_value(file, 1024)
            (root / 'b.json').write_text('{"b": {}}')
            with self.assertRaisesRegex(ValueError, 'in total'):
                read_directory(root, max_total=file.stat().st_size)

    def test_default_directory_cap_is_independent_from_file_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            padding = b' ' * (11 * 1024 * 1024)
            for index in range(6):
                (root / f'{index}.json').write_bytes(padding + b'{}')
            with self.assertRaisesRegex(ValueError, 'exceeds 67108864 bytes in total'):
                read_directory(root)


class Block:
    def __init__(self, values):
        self.values = values
        self.value = {'threshold': 80.}
        self.writes = self.reads = 0

    async def get(self):
        self.reads += 1
        return copy.deepcopy(self.value)

    async def set(self, value):
        self.writes += 1
        self.value = self.values.decode(self.values.encode(value))


class MemoryEvents:
    def __init__(self, root=None, callback=None):
        self.root = root

    def ensure(self, directory):
        pass

    def scan(self):
        return list(self.root.rglob('*.json'))

    def close(self):
        pass


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = 'machines/press_1/detection'
        self.logs = []
        self.sync = DirectorySync(self.root, None, report=self.logs.append)
        codec = SDL2(SDL_ROOT)
        self.block = Block(Values(codec.api, 'SDL2|Settings{1:fl32:threshold;}', 'Settings'))
        self.entry = Entry(self.block, self.sync.safe_file(self.path))
        self.sync.entries[self.path] = self.entry
        self.sync.watcher = MemoryEvents()

    def edit(self, value):
        self.entry.file.parent.mkdir(parents=True, exist_ok=True)
        self.entry.file.write_text(json.dumps({self.path: value}))
        # Controlled stamps make these tests independent of filesystem resolution.
        stamp = (self.entry.mtime or 1_000_000_000) + 1000
        os.utime(self.entry.file, ns=(stamp, stamp))

    async def test_local_publication_normalization_and_no_own_echo(self):
        self.edit({'threshold': 1.23456789})
        await self.sync.local(self.path)
        self.assertEqual(self.block.writes, 1)
        self.assertEqual(self.entry.value, self.block.value)
        await self.sync.apply_remote(self.path, self.block.value)
        await self.sync.local(self.path)
        self.assertEqual(self.block.writes, 1)
        self.assertEqual(len(self.logs), 1)
        self.assertEqual(self.block.reads, 0)

    async def test_remote_update_and_missing_file_do_not_publish(self):
        await self.sync.local(self.path)
        self.assertEqual(read_value(self.entry.file), {self.path: {'threshold': 80.}})
        await self.sync.apply_remote(self.path, {'threshold': 90.})
        await self.sync.local(self.path)
        self.assertEqual(self.block.writes, 0)
        self.assertEqual(read_value(self.entry.file)[self.path]['threshold'], 90.)
        self.entry.file.unlink()
        await self.sync.local(self.path)
        self.assertEqual(read_value(self.entry.file)[self.path]['threshold'], 80.)

    async def test_invalid_file_is_preserved_until_correction(self):
        self.edit({'unknown': 2})
        await self.sync.local(self.path)
        self.assertTrue(self.entry.blocked)
        await self.sync.apply_remote(self.path, {'threshold': 99.})
        self.assertEqual(read_value(self.entry.file)[self.path], {'unknown': 2})
        self.assertEqual(self.block.writes, 0)
        self.edit({'threshold': 42.})
        await self.sync.local(self.path)
        self.assertFalse(self.entry.blocked)
        self.assertEqual(self.block.value['threshold'], 42.)

    async def test_older_mtime_triggers_publication_and_identical_mtime_does_not(self):
        self.sync.write(self.path, self.block.value)
        stamp = self.entry.mtime
        self.entry.file.write_text(json.dumps({self.path: {'threshold': 50.}}))
        os.utime(self.entry.file, ns=(stamp, stamp))
        await self.sync.local(self.path)
        self.assertEqual(self.block.writes, 0)
        os.utime(self.entry.file, ns=(stamp - 1000, stamp - 1000))
        await self.sync.local(self.path)
        self.assertEqual(self.block.value['threshold'], 50.)

    async def test_path_mismatch_size_limit_and_symlinks_are_rejected(self):
        self.edit({'threshold': 80.})
        self.entry.file.write_text('{"another/path": {"threshold": 4}}')
        await self.sync.local(self.path)
        self.assertTrue(self.entry.blocked)
        self.assertEqual(self.block.writes, 0)
        self.edit({'threshold': 4.})
        self.sync.max_json_size = 8
        await self.sync.local(self.path)
        self.assertEqual(self.block.writes, 0)
        self.entry.file.unlink()
        target = self.root / 'other'
        target.write_text('{}')
        self.entry.file.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.sync.safe_file(self.path)
        for path in ('../outside', 'a//b', '/absolute'):
            with self.assertRaises(ValueError):
                self.sync.safe_file(path)

    async def test_initial_app_retries_export_without_importing_old_file(self):
        self.edit({'threshold': 5.})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.list.return_value = [self.path]
        client.block.return_value = self.block
        client.blocks = {}
        async def wait_notification():
            await asyncio.Future()
        client.next.side_effect = wait_notification
        sync = DirectorySync(self.root, AsyncMock(return_value=client), initial='app', debounce=.001,
                             retry_delay=.01, report=self.logs.append, watcher_factory=MemoryEvents)
        original = sync.write
        attempts = []
        def write(path, value):
            attempts.append(value)
            if len(attempts) == 1:
                raise PermissionError('temporary disk failure')
            original(path, value)
        with patch.object(sync, 'write', side_effect=write):
            task = asyncio.create_task(sync.session())
            try:
                async def exported():
                    while read_value(self.entry.file)[self.path]['threshold'] != 80.:
                        if task.done():
                            task.result()
                        await asyncio.sleep(.005)
                await asyncio.wait_for(exported(), 1)
                self.assertEqual(self.block.writes, 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
