"""Generate Ada 2012 typed callback containers; runtime codecs stay owned by SDL."""
import os
from pathlib import Path
import shlex
from ose.modeling.native_containers import sdk_root, merge_schemas
from ose.modeling.managed_containers import tool


def literal(text):
    # Ada source is UTF-8; catalogue strings must be the original UTF-8 octets.
    chunks = []
    for i in range(0, len(text), 160):
        part = text[i:i + 160]
        pieces = []
        for line in part.splitlines(keepends=True):
            raw = line.rstrip('\r\n')
            pieces.append('"' + raw.replace('"', '""') + '"')
            for c in line[len(raw):]:
                pieces.append(f'Character\'Val ({ord(c)})')
        chunks.append(' & '.join(pieces) or '""')
    return ' &\n      '.join(chunks) or '""'


def generate(name, node, handler, sdl_root, schemas, destination, identity):
    destination = Path(destination); sdl_root = Path(sdl_root)
    types = merge_schemas(name, 'ada', schemas, destination / 'types')
    api = 'Ose_' + name; stem = api.lower(); packages = sorted({p for p, _ in types.values()})
    if any(p.lower() in (api.lower(), identity.lower(), 'ose_component', 'ose_recording') for p in packages):
        raise ValueError('Ada schema package conflicts with component API')
    if handler.name.lower() != identity.lower().replace('.', '-') + '.adb':
        raise ValueError('Ada handler filename must follow GNAT unit naming')
    def typ(desc): return '.'.join(types[desc['root']])
    spec = ['-- Generated typed component API.', 'with Ada.Strings.Unbounded;', 'with Ada.Task_Identification;',
            'with Interfaces;', 'with SDL_Runtime;', 'with Ose_Recording;', *(f'with {p};' for p in packages), f'package {api} is',
            '   type Context is limited private;', '   function Name (Ctx : Context) return String;',
            '   function Config_JSON (Ctx : Context) return String;',
            '   function Time_Ns (Ctx : Context) return Interfaces.Unsigned_64;',
            '   function Tick_Index (Ctx : Context) return Interfaces.Unsigned_64;',
            '   function Now (Ctx : Context) return Long_Float;']
    for port, desc in node['inputs'].items():
        spec += [f'   type Input_{port} is access constant {typ(desc)};']
    spec += ['   type Inputs is ' + ('record' if node['inputs'] else 'null record;')]
    for port in node['inputs']:
        spec += [f'      {port} : Input_{port} := null;']
    if node['inputs']: spec += ['   end record;']
    spec += ['   type Handler is tagged limited null record;',
             '   procedure On_Start (Self : in out Handler; Ctx : in out Context);',
             '   procedure On_Stop (Self : in out Handler; Ctx : in out Context);',
             '   procedure On_Tick (Self : in out Handler; Ctx : in out Context; Selected : Inputs);']
    for port, desc in node['inputs'].items():
        nullable = '' if 'timeout' in desc else 'not null '
        spec += [f'   procedure On_{port} (Self : in out Handler; Ctx : in out Context; Message : {nullable}Input_{port});']
    for port, desc in node['outputs'].items():
        spec += [f'   procedure Publish_{port} (Ctx : in out Context; Message : {typ(desc)});']
    recording_symbols = {}
    for root in node.get('recording_catalogues', {}):
        symbol = types[root][1].removeprefix('T_')
        if symbol.lower() in {value.lower() for value in recording_symbols.values()}:
            raise ValueError('Ada recording helper name collision')
        recording_symbols[root] = symbol
        spec += [f'   procedure Declare_Recording_{symbol} (Ctx : in out Context; Name : String; Description : String := "");',
                 f'   function Record_{symbol} (Ctx : in out Context; Name : String; Value : {".".join(types[root])}) return Ose_Recording.Emit_Result;']
    spec += ['   function Recording_Enabled (Ctx : Context; Name : String) return Boolean;']
    for alias, desc in node.get('parameter_bindings', {}).items():
        if 'read' in desc['access']:
            spec += [f'   function Parameters_{alias} (Ctx : Context) return {typ(desc)};']
        if 'write' in desc['access']:
            spec += [f'   procedure Set_Parameters_{alias} (Ctx : in out Context; Value : {typ(desc)});']
    spec += ['   procedure Run (Self : in out Handler\'Class);', 'private', '   type Context is limited record',
             '      Config : Ada.Strings.Unbounded.Unbounded_String;',
             '      Recordings : Ose_Recording.Registry;',
             '      Time, Index : Interfaces.Unsigned_64 := 0;',
             '      Active : Boolean := False;', '      Owner : Ada.Task_Identification.Task_Id := Ada.Task_Identification.Null_Task_Id;']
    for port in node['outputs']:
        spec += [f'      Output_{port} : SDL_Runtime.Context;']
    for alias, desc in node.get('parameter_bindings', {}).items():
        spec += [f'      Parameter_Codec_{alias} : SDL_Runtime.Context;']
        if 'read' in desc['access']:
            spec += [f'      Parameter_Value_{alias} : {typ(desc)};', f'      Parameter_Present_{alias} : Boolean := False;']
    if node.get('parameter_bindings'):
        spec += ['      Parameter_Writes : Boolean := False;']
    spec += ['   end record;', f'end {api};', '']
    (destination / (stem + '.ads')).write_text('\n'.join(spec))
    body = ['with Ada.Unchecked_Deallocation;', 'with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;',
            'with Ose_Component; use Ose_Component;', f'package body {api} is',
            '   use type Ada.Task_Identification.Task_Id;']
    recording_catalogues = {text: index for index, text in enumerate(dict.fromkeys(node.get('recording_catalogues', {}).values()))}
    for text, index in recording_catalogues.items():
        body += [f'   Recording_Catalogue_{index} : constant String := {literal(text)};']
    body += [
            f'   function Name (Ctx : Context) return String is ({literal(name)});',
            '   function Config_JSON (Ctx : Context) return String is (To_String (Ctx.Config));',
            '   function Time_Ns (Ctx : Context) return Interfaces.Unsigned_64 is (Ctx.Time);',
            '   function Tick_Index (Ctx : Context) return Interfaces.Unsigned_64 is (Ctx.Index);',
            '   function Now (Ctx : Context) return Long_Float is (Long_Float (Ctx.Time) / 1.0E9);',
            '   procedure On_Start (Self : in out Handler; Ctx : in out Context) is begin null; end;',
            '   procedure On_Stop (Self : in out Handler; Ctx : in out Context) is begin null; end;']
    for port, desc in node['inputs'].items():
        nullable = '' if 'timeout' in desc else 'not null '
        body += [f'   procedure On_{port} (Self : in out Handler; Ctx : in out Context; Message : {nullable}Input_{port}) is',
                 f'   begin raise Program_Error with "missing On_{port}"; end;']
    body += ['   procedure On_Tick (Self : in out Handler; Ctx : in out Context; Selected : Inputs) is', '   begin']
    for port, desc in node['inputs'].items():
        if 'timeout' not in desc: body += [f'      if Selected.{port} /= null then']
        body += [f'         On_{port} (Handler\'Class (Self), Ctx, Selected.{port});']
        if 'timeout' not in desc: body += ['      end if;']
    if not node['inputs']: body += ['      raise Program_Error with "missing On_Tick";']
    body += ['   end On_Tick;']
    for port, desc in node['outputs'].items():
        package = types[desc['root']][0]
        body += [f'   procedure Publish_{port} (Ctx : in out Context; Message : {typ(desc)}) is', '   begin',
                 '      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task then',
                 '         raise Program_Error with "publication requires the active tick callback";', '      end if;',
                 f'      Publish ({literal(port)}, {package}.Encode (Ctx.Output_{port}, Message));', '   end;']
    body += ['   function Recording_Enabled (Ctx : Context; Name : String) return Boolean is',
             '     (Ose_Recording.Is_Enabled (Ctx.Recordings, Name));']
    for root, symbol in recording_symbols.items():
        package, value_type = types[root]
        text = node['recording_catalogues'][root]
        body += [f'   procedure Declare_Recording_{symbol} (Ctx : in out Context; Name : String; Description : String := "") is',
                 '   begin',
                 '      if Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "recording declaration requires on_start"; end if;',
                 f'      Ose_Recording.Declare_Point (Ctx.Recordings, Name, {literal(root)}, Recording_Catalogue_{recording_catalogues[text]}, Description);',
                 '   end;',
                 f'   function Record_{symbol} (Ctx : in out Context; Name : String; Value : {package}.{value_type}) return Ose_Recording.Emit_Result is',
                 '   begin',
                 '      if not Ctx.Active or else Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "recording requires the active tick callback"; end if;',
                 f'      Ose_Recording.Check_Type (Ctx.Recordings, Name, {literal(root)});',
                 '      if not Recording_Enabled (Ctx, Name) then return Ose_Recording.Disabled; end if;',
                 "      if Ctx.Recordings.Tick_Bytes + Name'Length + 2 >= 256 * 1024 then return Ose_Recording.Dropped; end if;",
                 f'      return Ose_Recording.Emit (Ctx.Recordings, Name, {package}.Encode (Ctx.Recordings.Points.Constant_Reference (Name).Encoder, Value));',
                 '   end;']
    for alias, desc in node.get('parameter_bindings', {}).items():
        package = types[desc['root']][0]
        if 'read' in desc['access']:
            body += [f'   function Parameters_{alias} (Ctx : Context) return {typ(desc)} is (Ctx.Parameter_Value_{alias});']
        if 'write' in desc['access']:
            body += [f'   procedure Set_Parameters_{alias} (Ctx : in out Context; Value : {typ(desc)}) is', '   begin',
                     '      if not Ctx.Parameter_Writes or else Ctx.Owner /= Ada.Task_Identification.Current_Task then raise Program_Error with "parameter writes require an active callback"; end if;',
                     f'      Set_Parameters ({literal(alias)}, {package}.Encode (Ctx.Parameter_Codec_{alias}, Value));', '   end;']
    body += ['   procedure Run (Self : in out Handler\'Class) is', '      Ctx : Context;', '      E : Event;',
             '      Selected : Inputs;', '      Started : Boolean := False;']
    for port, desc in node['inputs'].items():
        body += [f'      Present_{port} : Boolean := False;', f'      type Owned_{port} is access all {typ(desc)};', f'      Value_{port} : Owned_{port};', f'      procedure Free_{port} is new Ada.Unchecked_Deallocation ({typ(desc)}, Owned_{port});',
                 f'      Remote_{port} : Unbounded_String;', f'      Decoder_{port} : SDL_Runtime.Context;']
    if node.get('parameter_bindings'):
        body += ['      procedure Parameter_Update (Event_Value : Event; Initial : Boolean := False) is',
                 '         Port, Cat : Unbounded_String;', '         Data : SDL_Runtime.Buffer;', '      begin',
                 '         if Op (Event_Value) /= \'U\' then raise Protocol_Error with "expected parameter update"; end if;',
                 '         Input (Event_Value, Port, Cat, Data);']
        readable = [(a, d) for a, d in node['parameter_bindings'].items() if 'read' in d['access']]
        for i, (alias, desc) in enumerate(readable):
            package = types[desc['root']][0]
            body += [f'         {"if" if i == 0 else "elsif"} To_String (Port) = {literal(alias)} then',
                     f'            if To_String (Cat) /= {literal(desc["catalogue"])} or else (Initial and Ctx.Parameter_Present_{alias}) then',
                     '               raise Protocol_Error with "invalid parameter catalogue or duplicate initial value";', '            end if;',
                     f'            Ctx.Parameter_Value_{alias} := {package}.Decode (Ctx.Parameter_Codec_{alias}, SDL_Runtime.To_Bytes (Data));',
                     f'            Ctx.Parameter_Present_{alias} := True;']
        if readable: body += ['         else']
        body += ['            raise Protocol_Error with "unknown readable parameter";']
        if readable: body += ['         end if;']
        body += ['      end Parameter_Update;']
    body += ['      procedure Clear is' , '      begin']
    for port in node['inputs']:
        body += [f'         Selected.{port} := null; Free_{port} (Value_{port}); Present_{port} := False;']
    if not node['inputs']: body += ['         null;']
    body += ['      end Clear;', '   begin', '      Read_Event (E);',
             '      if Op (E) /= \'I\' then raise Protocol_Error with "expected INIT"; end if;',
             '      Ctx.Config := To_Unbounded_String (Body_Text (E));']
    for port, desc in node['outputs'].items():
        body += [f'      Ctx.Output_{port} := {types[desc["root"]][0]}.Prepare ({literal(desc["catalogue"])});']
    for alias, desc in node.get('parameter_bindings', {}).items():
        body += [f'      Ctx.Parameter_Codec_{alias} := {types[desc["root"]][0]}.Prepare ({literal(desc["catalogue"])});']
    for alias, desc in node.get('parameter_bindings', {}).items():
        if 'read' in desc['access']:
            body += ['      Read_Event (E); Parameter_Update (E, True);']
    if node.get('parameter_bindings'): body += ['      Ctx.Parameter_Writes := True; Ctx.Owner := Ada.Task_Identification.Current_Task;']
    body += ['      Ctx.Owner := Ada.Task_Identification.Current_Task;',
             '      Started := True; On_Start (Self, Ctx);',
             '      Ctx.Recordings.Frozen := True; Ctx.Owner := Ada.Task_Identification.Null_Task_Id;']
    if node.get('parameter_bindings'): body += ['      Ctx.Parameter_Writes := False;']
    body += ['      Ready (' + ('True' if node.get('parameter_bindings') else 'False') + ');' , '      loop', '         Read_Event (E);', '         case Op (E) is',
             "         when 'X' =>", '            if Body_Size (E) /= 0 then raise Protocol_Error with "invalid STOP"; end if;', '            exit;',
             *(["         when 'U' => Parameter_Update (E);"] if node.get('parameter_bindings') else []),
             "         when 'E' => Ose_Recording.Update (Ctx.Recordings, Body_Text (E));",
             "         when 'D' =>", '            declare', '               Port, Cat : Unbounded_String;', '               Data : SDL_Runtime.Buffer;',
             '            begin', '               Input (E, Port, Cat, Data);']
    for i, (port, desc) in enumerate(node['inputs'].items()):
        package = types[desc['root']][0]
        body += [f'               {"if" if i == 0 else "elsif"} To_String (Port) = {literal(port)} then',
                 f'                  if Present_{port} then raise Protocol_Error with "duplicate input"; end if;',
                 '                  if Length (Cat) = 0 and Data.Is_Empty then']
        body += ['                     null;' if 'timeout' in desc else '                     raise Protocol_Error with "unexpected null input";']
        body += ['                  else', '                     if Length (Cat) = 0 or Data.Is_Empty then raise Protocol_Error with "incomplete input"; end if;',
                 f'                     if Cat /= Remote_{port} then',
                 f'                        if not SDL_Runtime.Is_Compatible (SDL_Runtime.Compare_Catalogues (To_String (Cat), {literal(desc["catalogue"])}, {literal(desc["root"])}, True)) then',
                 '                           raise Protocol_Error with "incompatible input catalogue";', '                        end if;',
                 f'                        Decoder_{port} := {package}.Prepare (To_String (Cat)); Remote_{port} := Cat;', '                     end if;',
                 f'                     Value_{port} := new {typ(desc)}\'({package}.Decode (Decoder_{port}, SDL_Runtime.To_Bytes (Data)));', f"                     Selected.{port} := Value_{port}.all'Unchecked_Access;",
                 '                  end if;', f'                  Present_{port} := True;']
    if node['inputs']: body += ['               else']
    body += ['                  raise Protocol_Error with "unknown input port";']
    if node['inputs']: body += ['               end if;']
    body += ['            end;', "         when 'T' =>"]
    for port, desc in node['inputs'].items():
        if desc.get('required', True): body += [f'            if not Present_{port} then raise Protocol_Error with "missing required input"; end if;']
    if node.get('parameter_bindings'): body += ['            Ctx.Parameter_Writes := True;']
    body += ['            Tick (E, Ctx.Time, Ctx.Index); Ctx.Active := True; Ctx.Owner := Ada.Task_Identification.Current_Task;',
             '            Ctx.Recordings.Tick_Bytes := 0;',
             '            On_Tick (Self, Ctx, Selected); Ctx.Active := False;',
             *(['            Ctx.Parameter_Writes := False;'] if node.get('parameter_bindings') else []),
             '            Clear; Finish;',
             '         when others => raise Protocol_Error with "unexpected opcode";', '         end case;', '      end loop;',
             '      Clear; Started := False; On_Stop (Self, Ctx);', '   exception when others =>',
             '      Ctx.Active := False; Ctx.Recordings.Frozen := True; Clear;', *(['      Ctx.Parameter_Writes := False;'] if node.get('parameter_bindings') else []), '      if Started then',
             '         begin On_Stop (Self, Ctx); exception when others => null; end;', '      end if;', '      raise;',
             '   end Run;', f'end {api};', '']
    (destination / (stem + '.adb')).write_text('\n'.join(body))
    (destination / 'main.adb').write_text(f'''with {api};
with {identity};
with Ada.Exceptions;
with Ada.Text_IO;
with Ada.Command_Line;
procedure Main is
   Self : {identity}.Object;
begin
   {api}.Run (Self);
exception when Error : others =>
   Ada.Text_IO.Put_Line (Ada.Text_IO.Standard_Error, Ada.Exceptions.Exception_Information (Error));
   Ada.Command_Line.Set_Exit_Status (Ada.Command_Line.Failure);
end Main;
''')
    objects = destination / 'obj'; objects.mkdir(exist_ok=True)
    sdk = sdk_root(); runtime = sdl_root / 'runtime'
    c_sources = [runtime / 'c' / (n + '.c') for n in ('type_engine', 'type_registry', 'type_codec', 'sdl_wire', 'sdl_dynamic', 'type_display')]
    c_sources += [runtime / 'ada/sdl_ada_bridge.c', sdk / 'c/ose_component.c']
    commands, linked = [], []
    for i, src in enumerate(c_sources):
        obj = objects / f'bridge_{i}.o'; linked.append(str(obj))
        commands.append(tool('CC', 'cc') + ['-std=c99', '-O2', '-I' + str(runtime / 'c'), '-I' + str(sdk / 'c'), '-c', str(src), '-o', str(obj)])
    flags = shlex.split(os.environ.get('ADA_FLAGS', '-gnat2012 -gnata -O2'))
    rts = ['--RTS=' + os.environ['ADA_RTS']] if os.environ.get('ADA_RTS') else []
    # GNAT timestamps have second precision: rapid regeneration can otherwise
    # reuse a body compiled against an older spec and leave callbacks undefined.
    commands.append(tool('GNATMAKE', 'gnatmake') + flags + rts + ['-f', '-D', str(objects), '-I' + str(destination), '-I' + str(destination / 'types'),
                    '-I' + str(handler.parent), '-I' + str(runtime / 'ada'), '-I' + str(sdk / 'ada'), str(destination / 'main.adb'), '-o', str(destination / 'component'), '-largs'] + linked + ['-lm'])
    return {'types': types, 'command': [str(destination / 'component')], 'build': commands}


def scaffold(node, container):
    handler = Path(container['handler']); spec = handler.with_suffix('.ads')
    if handler.exists() and spec.exists(): return
    if handler.exists() or spec.exists(): raise ValueError('Ada handler requires both .ads and .adb; refusing to overwrite a partial pair')
    name = Path(node['command'][0]).parent.name; api = 'Ose_' + name; unit = container['unit']
    lines = [f'with {api};', f'package {unit} is', f'   type Object is new {api}.Handler with null record;']
    bodies = [f'package body {unit} is']
    for port, desc in node['inputs'].items():
        nullable = '' if 'timeout' in desc else 'not null '
        signature = f'procedure On_{port} (Self : in out Object; Ctx : in out {api}.Context; Message : {nullable}{api}.Input_{port})'
        lines += ['   overriding ' + signature + ';']; bodies += ['   overriding ' + signature + ' is', '   begin', '      -- Implement message processing here.', '      null;', '   end;']
    if not node['inputs']:
        signature = f'procedure On_Tick (Self : in out Object; Ctx : in out {api}.Context; Selected : {api}.Inputs)'
        lines += ['   overriding ' + signature + ';']; bodies += ['   overriding ' + signature + ' is', '   begin', '      -- Construct a typed message and call Publish_<port>.', '      null;', '   end;']
    lines += [f'end {unit};', '']; bodies += [f'end {unit};', '']
    handler.parent.mkdir(parents=True, exist_ok=True)
    # GNAT requires parent specs for child handler units.
    parts = unit.split('.')
    for i in range(1, len(parts)):
        parent = '.'.join(parts[:i]); path = handler.parent / (parent.lower().replace('.', '-') + '.ads')
        if not path.exists(): path.write_text(f'package {parent} is\nend {parent};\n')
    spec.write_text('\n'.join(lines)); handler.write_text('\n'.join(bodies))
