"""Generate readonly filter accessors without linking the native evaluator."""
from ada_backend import typ, stem, quote

def declaration(name):
   return ('   function Filter_Read (M : ' + typ(name) + '; P : SDL_Filter_Values.Path; '
      'Slot : Natural; Work : in out SDL_Filter_Values.Scratch) return SDL_Filter_Values.Value;\n')

def read_value(backend, type_name, dims, value, position, indent):
   out = []
   if dims:
      out.append(indent + 'if ' + position + " > P'Last then return SDL_Filter_Values.Array_Value (" + str(dims[0]) + '); end if;\n')
      out.append(indent + 'if P (' + position + ').Kind /= 1 then return SDL_Filter_Values.Invalid; end if;\n')
      out.append(indent + 'if P (' + position + ').Index >= ' + str(dims[0]) + ' then return SDL_Filter_Values.Missing; end if;\n')
      out.append(read_value(backend, type_name, dims[1:], value + ' (Positive (P (' + position + ').Index + 1))',
         '(' + position + ' + 1)', indent))
      return ''.join(out)
   if type_name in backend.schema.messages:
      owner = backend.external_package(type_name)
      function = (owner + '.' if owner else '') + 'Filter_Read'
      return indent + 'return ' + function + ' (' + value + ', P (' + position + " .. P'Last), Slot, Work);\n"
   if type_name in ('c32', 'c64'):
      out.append(indent + 'if ' + position + " <= P'Last then\n")
      out.append(indent + '   if ' + position + " /= P'Last or else P (" + position + ').Kind /= 0 then return SDL_Filter_Values.Invalid; end if;\n')
      out.append(indent + '   case P (' + position + ').Id is\n')
      out.append(indent + '      when 1 => return SDL_Filter_Values.Floating (Float64 (' + value + '.Re));\n')
      out.append(indent + '      when 2 => return SDL_Filter_Values.Floating (Float64 (' + value + '.Im));\n')
      out.append(indent + '      when others => return SDL_Filter_Values.Invalid;\n' + indent + '   end case;\n' + indent + 'end if;\n')
      out.append(indent + 'return SDL_Filter_Values.Complex (Float64 (' + value + '.Re), Float64 (' + value + '.Im));\n')
      return ''.join(out)
   out.append(indent + 'if ' + position + " <= P'Last then return SDL_Filter_Values.Invalid; end if;\n")
   if type_name == 'bool':
      result = 'Bool_Value (' + value + ')'
   elif type_name == 'string':
      result = 'Text_Value (Work, Slot, ' + value + ')'
   elif type_name in ('fl32', 'fl64'):
      result = 'Floating (Float64 (' + value + '))'
   elif type_name.startswith('uint'):
      result = 'Unsigned (Unsigned_64 (' + value + '))'
   else:
      result = 'Signed (Int64 (' + value + '))'
   out.append(indent + 'return SDL_Filter_Values.' + result + ';\n')
   return ''.join(out)

def body(backend, name):
   message = backend.schema.messages[name]
   owner = backend.external_package(name)
   out = [declaration(name).rstrip(';\n') + ' is\n',
      "      Pos : constant Positive := P'First;\n", '   begin\n']
   if owner:
      out.append('      return ' + owner + '.Filter_Read (M, P, Slot, Work);\n')
   else:
      out.append('      if P\'Length = 0 then return SDL_Filter_Values.Message_Value; end if;\n')
      out.append('      if P (Pos).Kind /= 0 or else not SDL_Filter_Values.Owner_Is (P (Pos), ' + quote(name) + ') then return SDL_Filter_Values.Invalid; end if;\n')
      out.append('      case P (Pos).Id is\n')
      for field in message.fields:
         value = 'M.F_' + stem(field.name)
         out.append('         when ' + str(field.index) + ' =>\n')
         if field.modifier == 'optional':
            out.append('            if not M.H_' + stem(field.name) + ' then return SDL_Filter_Values.Missing; end if;\n')
         if field.modifier in ('repeated', 'packed'):
            package = 'V_' + stem(name) + '_' + stem(field.name)
            out.append("            if Pos + 1 > P'Last then return SDL_Filter_Values.Array_Value (Interfaces.C.size_t (" + value + '.Length)); end if;\n')
            out.append('            if P (Pos + 1).Kind /= 1 then return SDL_Filter_Values.Invalid; end if;\n')
            out.append('            if P (Pos + 1).Index >= Interfaces.C.size_t (' + value + '.Length) then return SDL_Filter_Values.Missing; end if;\n')
            out.append('            declare\n               Ref : constant ' + package + '.Constant_Reference_Type := ' + value + '.Constant_Reference (Positive (P (Pos + 1).Index + 1));\n            begin\n')
            out.append(read_value(backend, field.type_name, field.array_dimensions, 'Ref.Element.all', '(Pos + 2)', '               '))
            out.append('            end;\n')
         else:
            out.append(read_value(backend, field.type_name, field.array_dimensions, value, '(Pos + 1)', '            '))
      out.append('         when others => return SDL_Filter_Values.Missing;\n      end case;\n')
   out.append('   exception when others => return SDL_Filter_Values.Invalid;\n   end Filter_Read;\n')
   return ''.join(out)
