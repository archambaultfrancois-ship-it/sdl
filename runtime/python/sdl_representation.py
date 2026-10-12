"""SDL2 field representation: validated options and bounded bitstream conversion.

The ordinary codecs retain their native byte layouts; this layer converts those
bytes at the message boundary, including nested messages and unknown fields.
"""
import re

_KEYS = ('bits', 'encoding', 'align', 'pad_after')
_LIMIT = 64 * 1024 * 1024


def options(text, type_name, enum=False):
   result = {}
   if text is None:
      return result
   for item in text.split(','):
      if item in ('v', 'z'):
         key, value = 'encoding', {'v':'varint', 'z':'zigzag'}[item]
      elif re.fullmatch(r'[bap][0-9]+', item):
         key = {'b':'bits', 'a':'align', 'p':'pad_after'}[item[0]]
         value = int(item[1:])
      else:
         raise ValueError('invalid wire option')
      if key in result:
         raise ValueError('duplicate wire option')
      result[key] = value
   validate(result, type_name, enum)
   return result


def validate(w, type_name, enum=False):
   width = {'bool': 1, 'int8': 8, 'uint8': 8, 'int16': 16, 'uint16': 16,
      'int32': 32, 'uint32': 32, 'int64': 64, 'uint64': 64}.get(type_name, 32 if enum else 0)
   if 'bits' in w and not 1 <= w['bits'] <= width:
      raise ValueError('invalid wire bits')
   if 'encoding' in w and (not width or type_name == 'bool' or 'bits' in w or
         (w['encoding'] == 'zigzag' and type_name.startswith('uint'))):
      raise ValueError('invalid wire encoding type')
   a = w.get('align', 1)
   if not 1 <= a <= 256 or a & (a - 1) or not 0 <= w.get('pad_after', 0) <= 65535:
      raise ValueError('invalid wire alignment or padding')


def suffix(w):
   return '@' + ','.join({'bits':'b','align':'a','pad_after':'p'}[k] + str(w[k]) if k != 'encoding' else {'varint':'v','zigzag':'z'}[w[k]] for k in _KEYS if k in w) if w else ''


class Stream:
   def __init__(self, data=None):
      self.data = memoryview(data).cast('B') if data is not None else bytearray()
      self.reading = data is not None
      self.pos = 0

   def bits(self, width, value=0):
      if self.pos + width > _LIMIT * 8:
         raise ValueError('representation exceeds buffer limit')
      if self.reading:
         if width > len(self.data) * 8 - self.pos:
            raise ValueError('truncated representation')
         value = 0
         for _ in range(width):
            value = (value << 1) | ((self.data[self.pos // 8] >> (7 - self.pos % 8)) & 1)
            self.pos += 1
         return value
      for bit in range(width - 1, -1, -1):
         if self.pos % 8 == 0:
            self.data.append(0)
         self.data[self.pos // 8] |= ((value >> bit) & 1) << (7 - self.pos % 8)
         self.pos += 1
      return value

   def zeros(self, n):
      if self.reading:
         for _ in range(n):
            if self.bits(1):
               raise ValueError('nonzero representation padding')
      else:
         for _ in range(n):
            self.bits(1)

   def align(self):
      self.zeros((-self.pos) % 8)

   def bytes(self, n, data=None):
      if self.pos % 8 or n > _LIMIT or self.pos // 8 + n > _LIMIT:
         raise ValueError('invalid representation byte span')
      start = self.pos // 8
      if self.reading:
         if n > len(self.data) - start:
            raise ValueError('truncated representation')
         result = self.data[start:start + n].tobytes()
      else:
         self.data.extend(data)
         result = data
      self.pos += n * 8
      return result

   def varint(self, value=0, width=64):
      if self.reading:
         value = 0
         for i in range((width + 6) // 7):
            byte = self.bits(8)
            value |= (byte & 127) << (7 * i)
            if value >= 1 << width:
               raise ValueError('varint overflow')
            if byte < 128:
               if i and byte == 0:
                  raise ValueError('noncanonical varint')
               return value
         raise ValueError('varint overflow')
      while value >= 128:
         self.bits(8, (value & 127) | 128)
         value >>= 7
      self.bits(8, value)


def transform(messages, enums, names, data, encoding):
   plain, custom = (Stream(data), Stream()) if encoding else (Stream(), Stream(data))

   def count():
      n = plain.varint(width=32) if encoding else custom.varint(width=32)
      (custom if encoding else plain).varint(n, width=32)
      return n

   def copy(n):
      src, dst = (plain, custom) if encoding else (custom, plain)
      dst.bytes(n, src.bytes(n))

   def value(type_name, w, dimensions, depth):
      if dimensions:
         if dimensions[0] > 1048576:
            raise ValueError('representation array limit')
         for _ in range(dimensions[0]):
            value(type_name, w, dimensions[1:], depth)
         return
      if type_name in messages:
         message(type_name, depth + 1)
         return
      if type_name == 'string':
         copy(count())
         return
      size = {'bool':1,'int8':1,'uint8':1,'int16':2,'uint16':2,'int32':4,'uint32':4,
         'int64':8,'uint64':8,'fl32':4,'fl64':8,'c32':8,'c64':16}.get(type_name, 4)
      if 'bits' not in w and 'encoding' not in w:
         copy(size)
         return
      signed = type_name.startswith('int') or type_name in enums
      width = size * 8
      bits = w.get('bits', width)
      if encoding:
         n = int.from_bytes(plain.bytes(size), 'big', signed=signed)
         low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
         if not low <= n <= high:
            raise ValueError('value outside wire bit range')
         if 'bits' in w:
            custom.bits(bits, n & ((1 << bits) - 1))
         else:
            if w['encoding'] == 'zigzag':
               n = (n << 1) ^ (n >> (width - 1))
            elif n < 0:
               raise ValueError('varint requires a nonnegative value')
            custom.varint(n)
      else:
         n = custom.bits(bits) if 'bits' in w else custom.varint(width=width)
         if 'bits' in w and signed and n & (1 << (bits - 1)):
            n -= 1 << bits
         elif w.get('encoding') == 'zigzag':
            n = (n >> 1) ^ -(n & 1)
         elif signed and n >= 1 << (width - 1):
            raise ValueError('varint exceeds signed range')
         plain.bytes(size, n.to_bytes(size, 'big', signed=signed))

   def message(name, depth):
      if depth > 64:
         raise ValueError('representation nesting limit')
      base = custom.pos
      for f in messages[name]:
         w = f.get('wire', {})
         if 'bits' not in w or f['modifier'] != 'required' or 'align' in w:
            custom.align()
         if 'align' in w:
            custom.zeros((-(custom.pos - base) // 8) % w['align'] * 8)
         n = 1 if f['modifier'] == 'required' else count()
         if (f['modifier'] == 'optional' and n > 1) or n > 1048576:
            raise ValueError('invalid representation count')
         for _ in range(n):
            value(f['type'], w, f['dimensions'], depth)
         if w.get('pad_after'):
            custom.align()
            custom.zeros(w['pad_after'] * 8)
      custom.align()

   root = count()
   if not 1 <= root <= len(names):
      raise ValueError('unknown representation message ID')
   message(names[root - 1], 0)
   src, dst = (plain, custom) if encoding else (custom, plain)
   if src.pos != len(src.data) * 8:
      raise ValueError('trailing representation data')
   return bytes(dst.data)
