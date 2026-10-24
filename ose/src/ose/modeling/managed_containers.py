"""Java and Ada backends using public generated SDL declarations."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from ose.modeling.native_containers import merge_schemas, sdk_root

JAVA_KEYWORDS = set('abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null'.split())
ADA_KEYWORDS = set('abort abs abstract accept access aliased all and array at begin body case constant declare delay delta digits do else elsif end entry exception exit for function generic goto if in interface is limited loop mod new not null of or others out overriding package pragma private procedure protected raise range record rem renames requeue return reverse select separate some subtype synchronized tagged task terminate then type until use when while with xor'.split())


def validate_names(language, name, node, container):
    if language not in ('java', 'ada'):
        if 'class' in container or 'unit' in container:
            raise ValueError('class/unit only apply to Java/Ada containers')
        return
    key = 'class' if language == 'java' else 'unit'
    if set(container) - {'language', 'handler', key}:
        raise ValueError(f'unexpected {language} container identity field')
    identity = container.get(key)
    if not isinstance(identity, str) or not identity:
        raise ValueError(f'{language} container requires {key}')
    def valid(value):
        if language == 'java':
            return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value)) and value not in JAVA_KEYWORDS
        return bool(re.fullmatch(r'[A-Za-z](?:[A-Za-z0-9]|_[A-Za-z0-9])*', value)) and value.lower() not in ADA_KEYWORDS
    if not all(valid(part) for part in identity.split('.')):
        raise ValueError(f'invalid {language} {key}: {identity}')
    root = identity.split('.')[0].lower()
    if language == 'ada' and (root in {'ada', 'interfaces', 'system', 'gnat', 'main'} or
                              any(port.lower() in {'start', 'stop', 'tick'} for port in node['inputs'])):
        raise ValueError('Ada identity conflicts with runtime units or reserved callbacks')
    if language == 'java' and identity.startswith(('java.', 'sun.', 'jdk.')):
        raise ValueError('Java handler package is reserved by the JVM')
    if identity.lower().startswith(('org.sdlchain.', 'sdl.', 'sdl_', 'chain_')):
        raise ValueError('handler identity conflicts with a reserved SDK namespace')
    for names in ([name], list(node['inputs']), list(node['outputs'])):
        if not all(valid(part) for part in names):
            raise ValueError(f'invalid or reserved {language} identifier')
        if language == 'ada' and len(set(p.lower() for p in names)) != len(names):
            raise ValueError('Ada port identifiers collide ignoring case')


def discover_bindings(destination, language, logical_types):
    result = {}
    directory = Path(destination) / language
    if language == 'java':
        for source in directory.rglob('*.java'):
            text = source.read_text()
            package = re.search(r'^package\s+([\w.]+)\s*;', text, re.M)
            if not package:
                continue
            for match in re.finditer(r'public static final class (\w+) implements SdlCodec.Message\s*\{(.*?)(?=public static final class|\Z)', text, re.S):
                logical = re.search(r'public static final String SDL_NAME = ("(?:\\.|[^"\\])*");', match[2])
                if logical:
                    name = json.loads(logical[1])
                    result[name] = [package[1] + '.' + source.stem, match[1]]
    else:
        for source in directory.rglob('*.adb'):
            text = source.read_text()
            package = re.search(r'package body ([\w.]+) is', text)
            if not package:
                continue
            # The generated Encode body contains the authoritative logical name.
            for match in re.finditer(r'function Encode \(C : Context; M : (\w+)\) return Bytes is(.*?)(?=\n   function|\Z)', text, re.S):
                logical = re.search(r'(?:Check_Encode|Message_Id)\s*\(C,\s*"([^"]+)"\)', match[2])
                if logical:
                    result[logical[1]] = [package[1], match[1]]
    missing = set(logical_types) - result.keys()
    if missing:
        raise ValueError(f'cannot resolve generated {language} bindings: {sorted(missing)}')
    return {name: result[name] for name in logical_types}


def tool(name, default):
    if name in os.environ:
        return shlex.split(os.environ[name])
    if name in ('JAVA', 'JAVAC', 'JAR') and os.environ.get('JAVA_HOME'):
        return [str(Path(os.environ['JAVA_HOME']) / 'bin' / default)]
    return [default]


def java_string(value):
    return json.dumps(value, ensure_ascii=True)


def generate_java(name, node, handler, sdl_root, schemas, destination, identity):
    destination = Path(destination)
    types = merge_schemas(name, 'java', schemas, destination / 'types')
    # A component without ports still needs the public runtime.
    runtime = destination / 'types/sdl/runtime/SdlCodec.java'
    if not runtime.exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_bytes((Path(sdl_root) / 'runtime/java/SdlCodec.java').read_bytes())
    package = 'org.sdlchain.generated.' + name
    source = destination / 'src' / Path(package.replace('.', '/'))
    source.mkdir(parents=True, exist_ok=True)
    classes = destination / 'classes'
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir(parents=True, exist_ok=True)
    reserved = {'org.sdlchain.Component', 'org.sdlchain.Json', package + '.API', package + '.Main', identity}
    if any(wrapper in reserved for wrapper, _ in types.values()):
        raise ValueError('Java schema wrapper conflicts with the generated component API or handler')
    if handler.stem != identity.rsplit('.', 1)[-1]:
        raise ValueError('Java handler filename must match class name')
    def typ(desc):
        return '.'.join(types[desc['root']])
    def ports(direction):
        expressions = []
        for index, (port, desc) in enumerate(node.get(direction, {}).items()):
            resource = direction + '_' + str(index) + '.sdl'
            target = classes / package.replace('.', '/') / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(desc['catalogue'], encoding='utf-8')
            expressions.append('new Component.Port(%s, %s, Component.resource(API.class, %s), %s.class, %s, %s)' % (
                java_string(port), java_string(desc['root']), java_string(resource), typ(desc),
                str(desc.get('required', True)).lower(), str('timeout' in desc).lower()))
        # Individual factory methods avoid a large method for registration tables.
        methods = [f'    private static Component.Port {direction}_{i}() throws Exception {{ return {expr}; }}' for i, expr in enumerate(expressions)]
        methods += [f'    static Component.Port[] {direction}() throws Exception {{', f'        Component.Port[] ports = new Component.Port[{len(expressions)}];']
        methods += [f'        ports[{i}] = {direction}_{i}();' for i in range(len(expressions))]
        methods += ['        return ports;', '    }']
        return methods
    lines = [f'package {package};', 'import org.sdlchain.Component;', 'public final class API {'] + ports('inputs') + ports('outputs') + ports('parameter_bindings')
    lines += ['    public static final class Context extends Component.Context {',
              f'        public Context() throws Exception {{ super({java_string(name)}, outputs()); }}']
    if node.get('parameter_bindings'):
        readable = ','.join(str('read' in b['access']).lower() for b in node['parameter_bindings'].values())
        writable = ','.join(str('write' in b['access']).lower() for b in node['parameter_bindings'].values())
        lines[-1] = f'        public Context() throws Exception {{ super({java_string(name)}, outputs()); configureParameters(parameter_bindings(), new boolean[]{{{readable}}}, new boolean[]{{{writable}}}); }}'
        for i, (alias, desc) in enumerate(node['parameter_bindings'].items()):
            if 'read' in desc['access']:
                lines += [f'        public {typ(desc)} parameters_{alias}() throws Exception {{ return ({typ(desc)})parameter({i}); }}']
            if 'write' in desc['access']:
                lines += [f'        public void set_parameters_{alias}({typ(desc)} value) throws Exception {{ setParameter({i}, value); }}']
    for i, (port, desc) in enumerate(node['outputs'].items()):
        lines += [f'        public void publish_{port}({typ(desc)} message) throws Exception {{ publish({i}, message); }}']
    lines += ['    }', '    public static final class Inputs {']
    for port, desc in node['inputs'].items():
        lines += [f'        public final {typ(desc)} {port};']
    lines += ['        Inputs(Object[] values) {']
    for i, (port, desc) in enumerate(node['inputs'].items()):
        lines += [f'            this.{port} = ({typ(desc)})values[{i}];']
    lines += ['        }', '    }', '    public static abstract class Handler {',
              '        public void onStart(Context ctx) throws Exception {}', '        public void onStop(Context ctx) throws Exception {}']
    for port, desc in node['inputs'].items():
        lines += [f'        public void on_{port}(Context ctx, {typ(desc)} message) throws Exception {{ throw new UnsupportedOperationException("missing on_{port}"); }}']
    lines += ['        public void onTick(Context ctx, Inputs inputs) throws Exception {']
    if not node['inputs']:
        lines += ['            throw new UnsupportedOperationException("missing onTick");']
    for port, desc in node['inputs'].items():
        condition = '' if 'timeout' in desc else f'if (inputs.{port} != null) '
        lines += [f'            {condition}on_{port}(ctx, inputs.{port});']
    lines += ['        }', '    }', '}']
    (source / 'API.java').write_text('\n'.join(lines) + '\n')
    (source / 'Main.java').write_text(f'''package {package};
import org.sdlchain.Component;
public final class Main {{
    public static void main(String[] args) {{
        int status = 0;
        try {{
            final API.Context ctx = new API.Context();
            final API.Handler handler = (API.Handler)Class.forName({java_string(identity)}).getDeclaredConstructor().newInstance();
            Component.run(ctx, API.inputs(), new Component.Callbacks() {{
                public void start() throws Exception {{ handler.onStart(ctx); }}
                public void tick(Object[] values) throws Exception {{ handler.onTick(ctx, new API.Inputs(values)); }}
                public void stop() throws Exception {{ handler.onStop(ctx); }}
            }});
        }} catch (Throwable failure) {{ failure.printStackTrace(System.err); status = 1; }}
        System.exit(status);
    }}
}}
''')
    sources = sorted((destination / 'types').rglob('*.java')) + sorted((sdk_root() / 'java').rglob('*.java')) + [source / 'API.java', source / 'Main.java', handler]
    command = [sys.executable, '-m', 'ose.modeling.java_build', str(classes), *map(str, sources)]
    return {'types': types, 'command': tool('JAVA', 'java') + ['-cp', str(classes), package + '.Main'], 'build': [command]}


def generate_ada(*args, **kwargs):
    from ose.modeling.ada_containers import generate
    return generate(*args, **kwargs)


def scaffold_managed(node, container):
    handler = Path(container['handler'])
    if container['language'] == 'ada':
        from ose.modeling.ada_containers import scaffold
        return scaffold(node, container)
    if handler.exists():
        return
    identity = container['class']; package, _, simple = identity.rpartition('.')
    name = next(part for part in node['command'] if part.endswith('.Main')).split('.')[-2]
    lines = ([f'package {package};'] if package else []) + [f'import org.sdlchain.generated.{name}.API;', f'public final class {simple} extends API.Handler {{']
    for port, desc in node['inputs'].items():
        typ = '.'.join(container['types'][desc['root']])
        lines += [f'    @Override public void on_{port}(API.Context ctx, {typ} message) throws Exception {{', '        // Implement message processing here.', '    }']
    if not node['inputs']:
        lines += ['    @Override public void onTick(API.Context ctx, API.Inputs inputs) throws Exception {', '        // Construct a typed message and call ctx.publish_<port>(message).', '    }']
    lines += ['}', '']
    if handler.stem != simple:
        raise ValueError('Java handler filename must match class name')
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text('\n'.join(lines))
