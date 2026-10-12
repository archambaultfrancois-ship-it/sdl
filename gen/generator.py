# ============================================================================
# AUTOMATIC AUTO-DESCRIPTIVE CODE GENERATOR FOR C TARGET
# ============================================================================

import re

def fnv1a_32(string_data):
   """ Computes a fast 32-bit FNV-1a hash signature for type matching """
   h = 2166136261
   for char in string_data:
      h = h ^ ord(char)
      h = (h * 16777619) & 0xFFFFFFFF
   return h

class Field:
   def __init__(self, index, modifier, type_name, name):
      self.index = int(index)
      self.modifier = modifier
      self.type_name = type_name
      self.name = name

class Message:
   def __init__(self, name):
      self.name = name
      self.fields = []

class Enum:
   def __init__(self, name):
      self.name = name
      self.pairs = []

class MsgParser:
   def __init__(self):
      self.enums = {}
      self.messages = {}
      self.message_order = []

   def parse_text(self, text):
      lines = [re.sub(r'//.*', '', l).strip() for l in text.split('\n')]
      lines = [l for l in lines if l]

      current_enum = None
      current_msg = None

      for line in lines:
         if line.startswith('enum'):
            name = line.split()[1].replace('{', '').strip()
            current_enum = Enum(name)
            self.enums[name] = current_enum
            continue
         elif line.startswith('message'):
            name = line.split()[1].replace('{', '').strip()
            current_msg = Message(name)
            self.messages[name] = current_msg
            self.message_order.append(name)
            continue
         elif line == '}':
            current_enum = None
            current_msg = None
            continue

         if current_enum:
            match = re.match(r'(\w+)\s*=\s*(\d+)\s*;', line)
            if match: current_enum.pairs.append((match.group(1), match.group(2)))

         if current_msg:
            match = re.match(r'(\d+):\s+(optional|required|repeated)\s+(\w+)\s+(\w+)\s*;', line)
            if match:
               field = Field(match.group(1), match.group(2), match.group(3), match.group(4))
               current_msg.fields.append(field)

   def _to_c_type(self, type_name):
      mapping = {
         'int8': 'int8_t', 'int16': 'int16_t', 'int32': 'int32_t', 'int64': 'int64_t',
         'fl32': 'float', 'fl64': 'double', 'string': 'char*', 
         'c32': 'float complex', 'c64': 'double complex'
      }
      return mapping.get(type_name, type_name)

   def _is_dynamic_type(self, type_name, known_formats):
      if type_name == 'string': return True
      if type_name in self.messages:
         sub_fmt = known_formats.get(type_name, "")
         return 's' in sub_fmt or 'a' in sub_fmt
      return False

   def _get_format_char(self, field, known_formats):
      t = field.type_name
      req = field.modifier == 'required'
      
      if field.modifier == 'repeated':
         if t in self.messages and self._is_dynamic_type(t, known_formats):
            return f"a({known_formats[t]})"
         return 'a'
         
      if t == 'string': return 's'
      elif t == 'int8': return 'B' if req else 'b'
      elif t == 'int16': return 'H' if req else 'h'
      elif t == 'int32': return 'I' if req else 'i'
      elif t == 'int64': return 'L' if req else 'l'
      elif t == 'fl32': return 'F' if req else 'f'
      elif t == 'fl64': return 'D' if req else 'd'
      elif t == 'c32': return 'C' if req else 'c'
      elif t == 'c64': return 'Z' if req else 'z'
      elif t in self.enums: return 'e'
      elif t in known_formats: return f"({known_formats[t]})"
      return '?'

   def generate_c_code(self):
      output = [
         "/* Automatically generated - Do not modify */\n\n",
         "#ifndef GENERATED_MESSAGES_H\n#define GENERATED_MESSAGES_H\n\n",
         "#include <stdint.h>\n#include <stdbool.h>\n#include <complex.h>\n#include \"type_engine.h\"\n\n"
      ]

      for enum_name, enum in self.enums.items():
         output.append(f"typedef enum {{\n")
         for key, val in enum.pairs: output.append(f"   {enum_name.upper()}_{key} = {val},\n")
         output.append(f"}} {enum_name};\n\n")

      known_formats = {}
      for msg_name in self.message_order:
         msg = self.messages[msg_name]
         fmt_str = ""
         for field in sorted(msg.fields, key=lambda f: f.index):
            fmt_str += self._get_format_char(field, known_formats)
         known_formats[msg_name] = fmt_str

      output.append("#pragma pack(push, 1)\n")
      for msg_name in self.message_order:
         msg = self.messages[msg_name]
         output.append(f"typedef struct {{\n")
         
         for field in sorted(msg.fields, key=lambda f: f.index):
            c_base_type = self._to_c_type(field.type_name)
            
            if field.modifier == 'optional' and field.type_name == 'string':
               output.append(f"   bool has_{field.name};\n")
               output.append(f"   const char* {field.name};\n")
            elif field.modifier == 'repeated':
               output.append(f"   uint32_t {field.name}_count;\n")
               output.append(f"   const {c_base_type}* {field.name};\n")
            else:
               output.append(f"   {c_base_type} {field.name};\n")
               
         output.append(f"}} {msg_name};\n\n")
      output.append("#pragma pack(pop)\n\n")

      for msg_name in self.message_order:
         msg_hash = fnv1a_32(msg_name)
         output.append(f'#define {msg_name.upper()}_FORMAT "{known_formats[msg_name]}"\n')
         output.append(f'#define {msg_name.upper()}_HASH 0x{msg_hash:08X}U\n')

      output.append("\n/* Runtime setup routine to map all schemas at boot */\n")
      output.append("static inline void register_all_types() {\n")
      for msg_name in self.message_order:
         output.append(f'   type_register("{msg_name}", {msg_name.upper()}_HASH, {msg_name.upper()}_FORMAT, sizeof({msg_name}));\n')
      output.append("}\n")

      output.append("\n#endif /* GENERATED_MESSAGES_H */\n")
      return "".join(output)

if __name__ == "__main__":
   # Load DSL schema descriptor with dual array usage styles
   schema_dsl = """
   message FixedItem {
      1: required fl32 x;
      2: required fl32 y;
   }

   message VarItem {
      1: optional string name;
      2: required int64 id;
   }

   message RootPayload {
      1: optional string header;
      2: repeated FixedItem fixed_array;
      3: repeated VarItem var_array;
   }
   """
   parser = MsgParser()
   parser.parse_text(schema_dsl)
   with open("generated_messages.h", "w", encoding="utf-8") as f:
      f.write(parser.generate_c_code())
   print("File 'generated_messages.h' built successfully.")
