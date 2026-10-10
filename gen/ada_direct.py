"""Direct positional Ada codecs, selected only for identical recursive layouts."""
from math import prod
from ada_backend import typ, stem, quote

WIDTHS = {'bool': 1, 'int8': 1, 'int16': 2, 'int32': 4, 'int64': 8,
          'fl32': 4, 'fl64': 8, 'c32': 8, 'c64': 16}


class DirectCodecs:
   def __init__(self, backend):
      self.b = backend
      self.schema = backend.schema
      self.fixed = {}
      self.packed = {}
      self.minimum = {}
      for name in self.schema.message_order:
         fields = self.schema.messages[name].fields
         sizes = [self.width(f.type_name, f.array_dimensions) for f in fields]
         self.fixed[name] = sum(sizes) if all(f.modifier == 'required' and n is not None
            for f, n in zip(fields, sizes)) else None
         self.minimum[name] = sum(1 if f.modifier != 'required' else self.min_width(f.type_name, f.array_dimensions) for f in fields)
         self.packed[name] = any(f.modifier == 'packed' or self.packed.get(f.type_name, False) for f in fields)

   def min_width(self, name, dims):
      return (4 if name in self.schema.enums else WIDTHS.get(name, self.minimum.get(name, 1))) * prod(dims)

   def width(self, name, dims):
      n = 4 if name in self.schema.enums else WIDTHS.get(name, self.fixed.get(name))
      return None if n is None else n * prod(dims)

   def size(self, name, dims, value, indent='      ', depth=0):
      width = self.width(name, dims)
      if width is not None:
         return indent + 'N := N + ' + str(width) + ';\n'
      if dims:
         i = 'I_' + str(depth)
         return indent + 'for ' + i + ' in ' + value + "'Range loop\n" + self.size(name, dims[1:], value+'('+i+')', indent+'   ', depth+1) + indent+'end loop;\n'
      expr = 'Size_' + typ(name) + '(' + value + ')' if name in self.schema.messages else 'Count_Size (Unsigned_32 (Length ('+value+'))) + Length ('+value+')'
      return indent + 'N := N + ' + expr + ';\n'

   def scalar(self, operation, name, dims, value, indent='      ', depth=0):
      if dims:
         i = 'I_' + str(depth)
         return indent + 'for '+i+' in '+value+"'Range loop\n"+self.scalar(operation, name, dims[1:], value+'('+i+')', indent+'   ', depth+1)+indent+'end loop;\n'
      if name in self.schema.messages:
         if operation == 'Put':
            return indent + 'Emit_'+typ(name)+' (Data, Position, '+value+');\n'
         return indent + value + ' := Parse_'+typ(name)+' (Data, Position);\n'
      code = indent + operation + ' (Data, Position, '+value+');\n'
      if name in self.schema.enums:
         check = indent+'if not Valid_'+stem(name)+' ('+value+') then raise Codec_Error with "invalid enum"; end if;\n'
         code = check+code if operation == 'Put' else code+check
      return code

   def helpers(self):
      out = []
      for name in self.schema.message_order:
         t = typ(name)
         out += ['   function Size_'+t+' (M : '+t+') return Natural;\n',
            '   procedure Emit_'+t+' (Data : in out Bytes; Position : in out Natural; M : '+t+');\n',
            '   function Parse_'+t+' (Data : Bytes; Position : in out Natural) return '+t+';\n']
         if self.fixed[name] is not None:
            out += ['   pragma Inline_Always (Emit_'+t+', Parse_'+t+');\n']
      for name in self.schema.message_order:
         t, msg = typ(name), self.schema.messages[name]
         if self.fixed[name] is not None:
            out += ['   function Size_'+t+' (M : '+t+') return Natural is\n      pragma Unreferenced (M);\n   begin return '+str(self.fixed[name])+'; end;\n']
         else:
            out += ['   function Size_'+t+' (M : '+t+') return Natural is\n      N : Natural := 0;\n   begin\n']
            for f in sorted(msg.fields, key=lambda f:f.index):
               value = 'M.F_'+stem(f.name)
               if f.modifier == 'optional':
                  out += ['      N := N + 1;\n      if M.H_'+stem(f.name)+' then\n', self.size(f.type_name,f.array_dimensions,value,'         '), '      end if;\n']
               elif f.modifier in ('packed','repeated'):
                  out += ['      N := N + Count_Size (Unsigned_32 ('+value+'.Length));\n']
                  width = self.width(f.type_name,f.array_dimensions)
                  if not width:
                     out += ['      if Natural ('+value+'.Length) > 1048576 then raise Codec_Error with "sequence limit"; end if;\n']
                  if width is not None:
                     out += ['      N := N + Natural ('+value+'.Length) * '+str(width)+';\n']
                  else:
                     out += ['      for I in 1 .. Natural ('+value+'.Length) loop\n',self.size(f.type_name,f.array_dimensions,value+'.Element (I)','         '),'      end loop;\n']
               else:
                  out += [self.size(f.type_name,f.array_dimensions,value)]
            out += ['      return N;\n   end;\n']
         out += ['   procedure Emit_'+t+' (Data : in out Bytes; Position : in out Natural; M : '+t+') is\n   begin\n']
         if not msg.fields:out += ['      null;\n']
         for f in sorted(msg.fields,key=lambda f:f.index):
            value = 'M.F_'+stem(f.name)
            if f.modifier == 'optional':
               out += ['      Put_Count (Data, Position, (if M.H_'+stem(f.name)+' then 1 else 0));\n      if M.H_'+stem(f.name)+' then\n',self.scalar('Put',f.type_name,f.array_dimensions,value,'         '),'      end if;\n']
            elif f.modifier in ('packed','repeated'):
               out += ['      Put_Count (Data, Position, Unsigned_32 ('+value+'.Length));\n      for I in 1 .. Natural ('+value+'.Length) loop\n']
               # Element returns a value, avoiding controlled reference objects.
               out += ['         declare Element : '+self.b.field_type(name,f)+' := '+value+'.Element (I); begin\n', self.scalar('Put',f.type_name,f.array_dimensions,'Element','            '), '         end;\n      end loop;\n']
            else:out += [self.scalar('Put',f.type_name,f.array_dimensions,value)]
         out += ['   end;\n','   function Parse_'+t+' (Data : Bytes; Position : in out Natural) return '+t+' is\n   begin\n      return Result : '+t+' do\n']
         if not msg.fields:out += ['         null;\n']
         for f in sorted(msg.fields,key=lambda f:f.index):
            value = 'Result.F_'+stem(f.name)
            if f.modifier == 'optional':
               out += ['         declare N : constant Unsigned_32 := Get_Count (Data, Position); begin\n            if N > 1 then raise Codec_Error with "invalid optional flag"; end if;\n            Result.H_'+stem(f.name)+' := N = 1;\n            if N = 1 then\n', self.scalar('Get',f.type_name,f.array_dimensions,value,'               '),'            end if;\n         end;\n']
            elif f.modifier in ('packed','repeated'):
               out += ['         declare N : constant Natural := Natural (Get_Count (Data, Position)); begin\n']
               width = self.width(f.type_name,f.array_dimensions)
               minimum = self.min_width(f.type_name,f.array_dimensions)
               if minimum:
                  out += ['            if N > (Data\'Length - (Position - Data\'First)) / '+str(minimum)+' then raise Codec_Error with "truncated sequence"; end if;\n']
               if not width:
                  out += ['            if N > 1048576 then raise Codec_Error with "sequence limit"; end if;\n']
               out += ['            '+value+'.Reserve_Capacity (Ada.Containers.Count_Type (N));\n            for I in 1 .. N loop\n               declare Element : '+self.b.field_type(name,f)+'; begin\n',self.scalar('Get',f.type_name,f.array_dimensions,'Element','                  '),'                  '+value+'.Append (Element);\n               end;\n            end loop;\n         end;\n']
            else:out += [self.scalar('Get',f.type_name,f.array_dimensions,value,'         ')]
         out += ['      end return;\n   end;\n']
      return ''.join(out)

   def encode(self, name):
      if not self.packed[name]:return ''
      return '''      if Direct_Layout (C, %s) then
         declare
            Id : constant Unsigned_32 := Message_Id (C, %s);
            Data : Bytes (1 .. Count_Size (Id) + Size_%s (M));
            Position : Natural := Data'First;
         begin
            Put_Count (Data, Position, Id);
            Emit_%s (Data, Position, M);
            if Position /= Data'Last + 1 then raise Codec_Error with "encoding length mismatch"; end if;
            return Data;
         end;
      end if;
''' % (quote(name),quote(name),typ(name),typ(name))

   def decode(self, name):
      if not self.packed[name]:return ''
      return '''      if Direct_Layout (C, %s) then
         declare Position : Natural := Data'First; begin
            if Get_Count (Data, Position) /= Message_Id (C, %s) then
               raise Codec_Error with "wrong message type";
            end if;
            return Result : %s := Parse_%s (Data, Position) do
               if Position /= Data'Last + 1 then raise Codec_Error with "trailing bytes"; end if;
            end return;
         end;
      end if;
''' % (quote(name),quote(name),typ(name),typ(name))
