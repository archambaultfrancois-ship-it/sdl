"""Asynchronous command input with terminal editing and session history."""
import asyncio
import codecs
import os
import shutil
import stat
import sys
import unicodedata


class CommandInput:
    def __init__(self, output=None):
        self.transport, self.reader = None, None
        self.output = output if output is not None else sys.stdout
        self.history = []

    async def readline(self, prompt="record> "):
        # Regular command files cannot block on user input and are not selectable.
        if stat.S_ISREG(os.fstat(sys.stdin.fileno()).st_mode):
            return sys.stdin.readline()
        if self.reader is None:
            self.reader = asyncio.StreamReader(limit=128 * 1024)
            self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(self.reader), sys.stdin)
        if sys.stdin.isatty():
            return await self._terminal_line(prompt)
        return (await self.reader.readline()).decode(sys.stdin.encoding or "utf-8")

    async def _key(self, decoder):
        while True:
            value = await self.reader.read(1)
            if not value:
                return ""
            key = decoder.decode(value)
            if key:
                break
        if key == "\x1b":
            # Accept both normal and application cursor-key sequences.
            try:
                prefix = await asyncio.wait_for(self.reader.read(1), .1)
                if prefix not in (b"[", b"O"):
                    return key
                for _ in range(16):
                    value = await asyncio.wait_for(self.reader.read(1), .1)
                    if not value:
                        break
                    key += value.decode("ascii", errors="ignore")
                    if b"@" <= value <= b"~":
                        break
            except asyncio.TimeoutError:
                pass
        return key

    async def _terminal_line(self, prompt):
        import termios

        descriptor = sys.stdin.fileno()
        saved = termios.tcgetattr(descriptor)
        editing = saved.copy()
        editing[3] &= ~(termios.ICANON | termios.ECHO)
        editing[6] = saved[6][:]
        editing[6][termios.VMIN], editing[6][termios.VTIME] = 1, 0
        decoder = codecs.getincrementaldecoder(sys.stdin.encoding or "utf-8")(errors="replace")
        line, draft, cursor, position = "", "", 0, len(self.history)

        def width(char):
            return (0 if unicodedata.combining(char) else
                    2 if unicodedata.east_asian_width(char) in ("W", "F") else 1)

        def redraw():
            # Scroll long filters horizontally, leaving room for the cursor so
            # redrawing never wraps onto another terminal row.
            available = max(1, shutil.get_terminal_size().columns - len(prompt) - 1)
            start, before = cursor, 0
            for char in reversed(line[:cursor]):
                if before + width(char) >= available:
                    break
                start -= 1
                before += width(char)
            end, visible = start, 0
            for char in line[start:]:
                if visible + width(char) > available:
                    break
                end += 1
                visible += width(char)
            tail = visible - before
            print("\r\x1b[2K" + prompt + line[start:end] + (f"\x1b[{tail}D" if tail else ""),
                  end="", flush=True, file=self.output)

        try:
            termios.tcsetattr(descriptor, termios.TCSANOW, editing)
            print(prompt, end="", flush=True, file=self.output)
            while True:
                key = await self._key(decoder)
                if not key or (key == "\x04" and not line):
                    print(flush=True, file=self.output)
                    return ""
                if key in ("\n", "\r"):
                    print(flush=True, file=self.output)
                    if line.strip() and (not self.history or self.history[-1] != line):
                        self.history.append(line)
                        del self.history[:-1000]
                    return line + "\n"
                if key in ("\x1bA", "\x1bB"):
                    if position == len(self.history):
                        draft = line
                    position = max(0, min(len(self.history), position + (-1 if key.endswith("A") else 1)))
                    line = self.history[position] if position < len(self.history) else draft
                    cursor = len(line)
                elif key in ("\x7f", "\b"):
                    if cursor:
                        line = line[:cursor - 1] + line[cursor:]
                        cursor -= 1
                elif key in ("\x1bD", "\x02"):
                    cursor = max(0, cursor - 1)
                elif key in ("\x1bC", "\x06"):
                    cursor = min(len(line), cursor + 1)
                elif key in ("\x1bH", "\x1b1~", "\x1b7~", "\x01"):
                    cursor = 0
                elif key in ("\x1bF", "\x1b4~", "\x1b8~", "\x05"):
                    cursor = len(line)
                elif key in ("\x1b3~", "\x04"):
                    line = line[:cursor] + line[cursor + 1:]
                elif key == "\x15":
                    line, cursor = line[cursor:], 0
                elif key == "\x0b":
                    line = line[:cursor]
                elif key.isprintable():
                    line = line[:cursor] + key + line[cursor:]
                    cursor += len(key)
                redraw()
        finally:
            termios.tcsetattr(descriptor, termios.TCSANOW, saved)

    def close(self):
        if self.transport:
            self.transport.close()
