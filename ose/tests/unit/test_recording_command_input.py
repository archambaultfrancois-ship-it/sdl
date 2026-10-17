import asyncio
import io
import os
import tempfile
import termios
import unittest
from unittest.mock import patch

from ose.recording.command_input import CommandInput


class TerminalCommandInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.master, self.slave = os.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.stdin = os.fdopen(os.dup(self.slave), "r", encoding="utf-8")
        self.addCleanup(self.stdin.close)
        stdin_patch = patch("sys.stdin", self.stdin)
        stdin_patch.start()
        self.addCleanup(stdin_patch.stop)
        output_patch = patch("sys.stdout", new_callable=io.StringIO)
        self.output = output_patch.start()
        self.addCleanup(output_patch.stop)
        self.commands = CommandInput()
        self.addCleanup(self.commands.close)
        self.saved = termios.tcgetattr(self.slave)

    async def start_input(self):
        self.output.seek(0)
        self.output.truncate()
        task = asyncio.create_task(self.commands.readline())

        async def ready():
            while not self.output.getvalue():
                await asyncio.sleep(0)

        await asyncio.wait_for(ready(), 2)
        return task

    async def enter(self, keys):
        task = await self.start_input()
        os.write(self.master, keys.encode("utf-8"))
        line = await asyncio.wait_for(task, 2)
        self.assertEqual(termios.tcgetattr(self.slave), self.saved)
        return line

    async def test_history_navigation_and_boundaries(self):
        self.assertEqual(await self.enter("\x1b[A\x1b[Blist\n"), "list\n")
        self.assertEqual(await self.enter("status\n"), "status\n")
        self.assertEqual(await self.enter("\x1b[A\x1b[A\x1b[A\n"), "list\n")
        self.assertEqual(await self.enter("\x1b[A\x1b[A\x1b[B\n"), "list\n")

    async def test_down_restores_unfinished_line_and_application_arrows(self):
        await self.enter("status\n")
        self.assertEqual(await self.enter("select sensor_*\x1bOA\x1bOB\n"), "select sensor_*\n")
        self.assertEqual(await self.enter("\x1b[A\x1b[B\x1b[B\n"), "\n")

    async def test_recalled_commands_can_be_edited_with_unicode(self):
        await self.enter("label été\n")
        self.assertEqual(await self.enter("\x1b[A\x7f\x1b[D\x1b[3~o\x1b[F!\n"), "label éo!\n")
        self.assertEqual(await self.enter("\x1b[A\x01\x0bstatus\n"), "status\n")

    async def test_empty_lines_and_consecutive_duplicates_do_not_fill_history(self):
        for line in ("\n", "  \n", "status\n", "status\n"):
            await self.enter(line)
        self.assertEqual(self.commands.history, ["status"])

    async def test_waiting_for_input_keeps_event_loop_running_and_restores_terminal_on_cancel(self):
        task = await self.start_input()
        self.assertFalse(task.done())
        self.assertFalse(termios.tcgetattr(self.slave)[3] & termios.ICANON)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(termios.tcgetattr(self.slave), self.saved)

    async def test_eof_restores_terminal(self):
        self.assertEqual(await self.enter("\x04"), "")

    async def test_long_recalled_command_scrolls_without_wrapping(self):
        command = "filter sensor_1.signal value > 123456789"
        with patch("shutil.get_terminal_size", return_value=os.terminal_size((24, 24))):
            self.assertEqual(await self.enter(command + "\n"), command + "\n")
            self.assertEqual(await self.enter("\x1b[A\x7f0\n"), command[:-1] + "0\n")
        for redraw in self.output.getvalue().split("\r\x1b[2K")[1:]:
            self.assertLess(len(redraw.rstrip("\n")), 24)


class ScriptCommandInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_command_file(self):
        with tempfile.TemporaryFile(mode="w+") as source:
            source.write("list\nstatus\n")
            source.seek(0)
            with patch("sys.stdin", source), patch("sys.stdout", new_callable=io.StringIO) as output:
                commands = CommandInput()
                self.assertEqual(await commands.readline(), "list\n")
                self.assertEqual(await commands.readline(), "status\n")
                self.assertEqual(await commands.readline(), "")
                self.assertEqual(output.getvalue(), "")
                commands.close()

    async def test_piped_commands(self):
        read, write = os.pipe()
        with os.fdopen(read, "r", encoding="utf-8") as source:
            os.write(write, b"list\nstatus\n")
            os.close(write)
            with patch("sys.stdin", source), patch("sys.stdout", new_callable=io.StringIO) as output:
                commands = CommandInput()
                try:
                    self.assertEqual(await commands.readline(), "list\n")
                    self.assertEqual(await commands.readline(), "status\n")
                    self.assertEqual(await commands.readline(), "")
                    self.assertEqual(output.getvalue(), "")
                finally:
                    commands.close()
