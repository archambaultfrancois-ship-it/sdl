"""Typed C and Rust containers built on the public SDL runtimes and stdio SDKs."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sysconfig


def sdk_root():
    source = Path(__file__).resolve().parents[3] / "sdk"
    target = Path(__file__).resolve().parents[2] / "share/sdl-chain/sdk"
    installed = Path(sysconfig.get_path("data")) / "share/sdl-chain/sdk"
    for candidate in (target, source, installed):
        if (candidate / "c/chain_callbacks.c").is_file() and (candidate / "rust/Cargo.toml").is_file():
            return candidate
    raise ValueError("native container SDK files are missing from this installation")


def c_string(value):
    escapes = {34: '\\"', 92: '\\\\', 63: '\\?'}
    return '"' + "".join(escapes.get(byte, chr(byte) if 32 <= byte < 127 else f"\\{byte:03o}")
                         for byte in value.encode()) + '"'


def rust_string(value):
    escapes = {'"': '\\"', '\\': '\\\\', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    return '"' + "".join(escapes.get(char, f"\\u{{{ord(char):x}}}" if ord(char) < 32 else char) for char in value) + '"'


def merge_schemas(name, language, schemas, destination):
    files, types = {}, {}
    suffixes = (".c", ".h") if language == "c" else (".rs",)
    for directory, bindings in schemas:
        for path in Path(directory).rglob("*"):
            if not path.is_file() or path.suffix not in suffixes or path.name in ("sdl_registry.c", "sdl_registry.h", "mod.rs"):
                continue
            relative = path.relative_to(directory)
            content = path.read_bytes()
            if relative in files and files[relative] != content:
                raise ValueError(f"{language} container {name}: conflicting generated module {relative}")
            files[relative] = content
        for logical, binding in bindings.items():
            if logical in types and types[logical] != binding:
                raise ValueError(f"{language} container {name}: conflicting bindings for {logical}")
            types[logical] = binding
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative, content in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if language == "rust":
        directories = {destination, *(p.parent for p in destination.rglob("*.rs"))}
        directories |= {parent for path in list(directories) for parent in path.parents if parent.is_relative_to(destination)}
        for directory in directories:
            modules = sorted({p.stem for p in directory.glob("*.rs") if p.name != "mod.rs"} |
                             {p.name for p in directories if p.parent == directory})
            (directory / "mod.rs").write_text("// Generated SDL modules.\n" + "".join(f"pub mod {module};\n" for module in modules))
    return types


def generate_c(name, node, handler, sdl_root, schemas, destination):
    destination, sdl_root = Path(destination), Path(sdl_root)
    bindings = destination / "types"
    types = merge_schemas(name, "c", schemas, bindings)
    reserved = {"ChainContext", "ChainPortSpec", "ChainHook", "ComponentCallbacks", "main", "dispatch", "inputs", "outputs"}
    if node["inputs"]:
        reserved.add("ComponentInputs")
    reserved.update("publish_" + port for port in node["outputs"])
    reserved.update("Input_" + port for port in node["inputs"])
    reserved.update("Output_" + port for port in node["outputs"])
    for _, symbol in types.values():
        if symbol in reserved:
            raise ValueError(f"C container {name}: SDL type {symbol} conflicts with the generated component API")
    headers = sorted({binding[0] for binding in types.values()})
    header = ["/* Generated component API. Edit the handler, not this file. */",
              "#ifndef CHAIN_GENERATED_COMPONENT_H", "#define CHAIN_GENERATED_COMPONENT_H",
              '#include "chain_callbacks.h"', *(f'#include "{path}"' for path in headers)]
    for direction, prefix in (("inputs", "Input"), ("outputs", "Output")):
        for port, desc in node[direction].items():
            header.append(f"typedef {types[desc['root']][1]} {prefix}_{port};")
    if node["inputs"]:
        header += ["typedef struct {"] + [f"    const {types[desc['root']][1]} *{port};" for port, desc in node["inputs"].items()]
        header += ["} ComponentInputs;"]
    tick_args = "ChainContext *, const ComponentInputs *" if node["inputs"] else "ChainContext *"
    header += ["typedef struct {", "    ChainHook on_start, on_stop;", f"    int (*on_tick)({tick_args});"]
    for port, desc in node["inputs"].items():
        header.append(f"    int (*on_{port})(ChainContext *, const {types[desc['root']][1]} *);")
    header += ["} ComponentCallbacks;", "extern const ComponentCallbacks component_callbacks;"]
    for port, desc in node["outputs"].items():
        header.append(f"int publish_{port}(ChainContext *, const {types[desc['root']][1]} *);")
    header += ["#endif", ""]
    (destination / "component.h").write_text("\n".join(header))
    source = ['/* Generated from the component model. */', '#include "component.h"', '#include <stdio.h>']
    registrations = set()
    for path in sorted(bindings.rglob("*.h")):
        source.append(f'#include "types/{path.relative_to(bindings).as_posix()}"')
        registrations.update(re.findall(r"bool (register_\w+_types)\(void\);", path.read_text()))
    for direction in ("inputs", "outputs"):
        source.append(f"static const ChainPortSpec {direction}[] = {{")
        for port, desc in node[direction].items():
            source.append("    {" + ", ".join((c_string(port), c_string(desc["root"]), c_string(desc["catalogue"]),
                                               "1" if desc.get("required", True) else "0")) + "},")
        source += ["    {NULL, NULL, NULL, 0}", "};"]
    for index, (port, desc) in enumerate(node["outputs"].items()):
        source += [f"int publish_{port}(ChainContext *ctx, const {types[desc['root']][1]} *message) {{",
                   f"    return chain_publish_value(ctx, {index}, message);", "}"]
    source += ["static int dispatch(ChainContext *ctx) {"]
    if node["inputs"]:
        source += ["    ComponentInputs selected;"]
        for index, port in enumerate(node["inputs"]):
            source.append(f"    selected.{port} = chain_input_value(ctx, {index});")
        source.append("    if (component_callbacks.on_tick) return component_callbacks.on_tick(ctx, &selected);")
        for port in node["inputs"]:
            source.append(f"    if (selected.{port} && !component_callbacks.on_{port}(ctx, selected.{port})) return 0;")
        source.append("    return 1;")
    else:
        source.append("    return component_callbacks.on_tick(ctx);")
    source += ["}", "int main(void) {"]
    checks = " && ".join(f"component_callbacks.on_{port}" for port in node["inputs"])
    condition = f"!component_callbacks.on_tick && !({checks})" if checks else "!component_callbacks.on_tick"
    source += [f"    if ({condition}) {{", '        fputs("missing component callbacks\\n", stderr); return 1;', "    }"]
    for function in sorted(registrations):
        source.append(f"    if (!{function}()) return 1;")
    source += [f"    return chain_run_callbacks({c_string(name)}, inputs, {len(node['inputs'])}, outputs, {len(node['outputs'])},",
               "        component_callbacks.on_start, dispatch, component_callbacks.on_stop);", "}", ""]
    (destination / "container.c").write_text("\n".join(source))
    sdk = sdk_root() / "c"
    runtime = sdl_root / "runtime/c"
    sources = [runtime / filename for filename in ("type_engine.c", "type_registry.c", "type_codec.c", "sdl_wire.c", "sdl_dynamic.c", "type_display.c")]
    binary = destination / "component"
    command = [*shlex.split(os.environ.get("CC", "cc")), "-std=c99", "-Wall", "-Wextra", "-Werror", "-O2",
               "-I" + str(destination), "-I" + str(bindings), "-I" + str(runtime), "-I" + str(sdk),
               *map(str, sources), *map(str, sorted(bindings.rglob("*.c"))), str(sdk / "chain_component.c"),
               str(sdk / "chain_callbacks.c"), str(destination / "container.c"), str(handler), "-lm", "-o", str(binary)]
    return {"types": types, "command": [str(binary)], "build": [command]}


def generate_rust(name, node, handler, sdl_root, schemas, destination):
    destination, sdl_root = Path(destination), Path(sdl_root)
    source = destination / "src"
    types = merge_schemas(name, "rust", schemas, source / "types")
    lines = ["// Generated from the component model.", "#![allow(dead_code, non_snake_case)]",
             "use sdl_runtime::SdlMessage;", "use sdl_chain_component::{receive, send, input, publish};",
             "pub type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;",
             "fn codec<T>(value: std::result::Result<T, sdl_runtime::CodecError>) -> Result<T> {",
             '    value.map_err(|error| format!("SDL codec: {error:?}").into())', "}",
             "pub struct Context {", "    pub name: &'static str,", "    pub config_json: String,", "    active: bool,"]
    for port in node["outputs"]:
        lines.append(f"    output_{port}: sdl_runtime::Context,")
    lines += ["}", "impl Context {"]
    for port, desc in node["outputs"].items():
        module, symbol = types[desc["root"]]
        lines += [f"    pub fn publish_{port}(&mut self, message: &crate::types::{module}::{symbol}) -> Result {{",
                  '        if !self.active { return Err("publication is only allowed inside a tick callback".into()); }',
                  f"        publish({rust_string(port)}, &codec(sdl_runtime::encode(&self.output_{port}, message))?)?;", "        Ok(())", "    }"]
    lines.append("}")
    if node["inputs"]:
        lines.append("pub struct Inputs<'a> {")
        for port, desc in node["inputs"].items():
            module, symbol = types[desc["root"]]
            lines.append(f"    pub {port}: Option<&'a crate::types::{module}::{symbol}>,")
        lines.append("}")
    lines += ["pub trait Callbacks {", "    fn on_start(&mut self, _ctx: &mut Context) -> Result { Ok(()) }",
              "    fn on_stop(&mut self, _ctx: &mut Context) -> Result { Ok(()) }"]
    for port, desc in node["inputs"].items():
        module, symbol = types[desc["root"]]
        lines += [f"    fn on_{port}(&mut self, _ctx: &mut Context, _message: &crate::types::{module}::{symbol}) -> Result {{",
                  f'        Err("implement on_{port} or on_tick".into())', "    }"]
    if node["inputs"]:
        lines.append("    fn on_tick(&mut self, ctx: &mut Context, inputs: &Inputs<'_>) -> Result {")
        for port in node["inputs"]:
            lines.append(f"        if let Some(message) = inputs.{port} {{ self.on_{port}(ctx, message)?; }}")
        lines.append("        Ok(())")
    else:
        lines += ["    fn on_tick(&mut self, _ctx: &mut Context) -> Result {", '        Err("implement on_tick".into())']
    lines += ["    }", "}", "pub fn run<H: Callbacks>(mut handler: H) -> Result {", "    let init = receive()?;",
              '    if init.op != b\'I\' { return Err("expected INIT".into()); }', "    let mut ctx = Context {",
              f"        name: {rust_string(name)}, config_json: String::from_utf8(init.data)?, active: false,"]
    for port, desc in node["outputs"].items():
        module, symbol = types[desc["root"]]
        lines.append(f"        output_{port}: codec(sdl_runtime::prepare({rust_string(desc['catalogue'])}, &[crate::types::{module}::{symbol}::type_info()]))?,")
    lines += ["    };", "    let execution = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result {"]
    for port, desc in node["inputs"].items():
        module, symbol = types[desc["root"]]
        lines += [f"        let mut selected_{port}: Option<crate::types::{module}::{symbol}> = None;",
                  f"        let mut decoder_{port}: Option<(String, sdl_runtime::Context)> = None;"]
    lines += ["        handler.on_start(&mut ctx)?;", "        send(b'R', &[])?;", "        loop {", "            let event = receive()?;",
              "            match event.op {", "                b'D' => {", "                    let data = input(&event)?;",
              "                    match data.port {"]
    for port, desc in node["inputs"].items():
        module, symbol = types[desc["root"]]
        lines += [f"                        {rust_string(port)} => {{",
                  f'                            if selected_{port}.is_some() {{ return Err("duplicate input in tick".into()); }}',
                  f"                            if decoder_{port}.as_ref().map(|(text, _)| text.as_str()) != Some(data.catalogue) {{",
                  f"                                let check = sdl_runtime::compare_catalogues(data.catalogue, {rust_string(desc['catalogue'])}, Some({rust_string(desc['root'])}), true);",
                  '                                if !check.compatible() { return Err(format!("{} at {}", check.code, check.path).into()); }',
                  f"                                let prepared = codec(sdl_runtime::prepare(data.catalogue, &[crate::types::{module}::{symbol}::type_info()]))?;",
                  f"                                decoder_{port} = Some((data.catalogue.to_owned(), prepared));", "                            }",
                  f"                            selected_{port} = Some(codec(sdl_runtime::decode(&decoder_{port}.as_ref().unwrap().1, data.payload))?);", "                        }"]
    lines += ['                        _ => return Err("unknown input port".into()),', "                    }", "                }",
              "                b'T' if event.data.is_empty() => {"]
    for port, desc in node["inputs"].items():
        if desc.get("required", True):
            lines.append(f'                    if selected_{port}.is_none() {{ return Err("missing required input: {port}".into()); }}')
    lines.append("                    ctx.active = true;")
    if node["inputs"]:
        fields = ", ".join(f"{port}: selected_{port}.as_ref()" for port in node["inputs"])
        lines.append(f"                    handler.on_tick(&mut ctx, &Inputs {{ {fields} }})?;")
    else:
        lines.append("                    handler.on_tick(&mut ctx)?;")
    lines += ["                    ctx.active = false;", "                    send(b'F', &[])?;"]
    for port in node["inputs"]:
        lines.append(f"                    selected_{port} = None;")
    lines += ["                }", "                b'X' if event.data.is_empty() => return Ok(()),",
              '                _ => return Err("unexpected component event".into()),', "            }", "        }", "    }));",
              "    ctx.active = false;", "    let stop = handler.on_stop(&mut ctx);", "    match execution {",
              "        Ok(result) => result.and(stop),", "        Err(panic) => std::panic::resume_unwind(panic),", "    }", "}", ""]
    (source / "container.rs").write_text("\n".join(lines))
    (source / "main.rs").write_text("// Generated component entry point.\npub mod types;\npub mod container;\n" +
        f"#[path = {rust_string(str(handler))}]\nmod handler;\n" +
        "fn main() -> container::Result { container::run(handler::Handler::default()) }\n")
    sdk = sdk_root()
    manifest = destination / "Cargo.toml"
    binary = "chain-component-" + name
    manifest.write_text('[package]\nname="chain-container"\nversion="0.1.0"\nedition="2021"\nautobins=false\n' +
        '[[bin]]\nname=' + json.dumps(binary) + '\npath="src/main.rs"\n[workspace]\n[dependencies]\n' +
        'sdl-runtime={path=' + json.dumps(str(sdl_root / 'runtime/rust')) + '}\n' +
        'sdl-chain-component={path=' + json.dumps(str(sdk / 'rust')) + '}\n')
    target = destination.parents[1] / "rust-target"
    command = ["cargo", "build", "--offline", "--release", "--manifest-path", str(manifest), "--target-dir", str(target)]
    return {"types": types, "command": [str(target / "release" / binary)], "build": [command]}


def scaffold_native(node, handler, types, language):
    handler = Path(handler)
    if handler.exists():
        if not handler.is_file():
            raise ValueError(f"handler is not a file: {handler}")
        return
    if language == "c":
        lines = ['/* User callbacks. Never overwritten by the builder. */', '#include "component.h"']
        if len(node["inputs"]) == 1:
            port, desc = next(iter(node["inputs"].items()))
            lines += [f"static int on_{port}(ChainContext *ctx, const Input_{port} *message) {{",
                      "    (void)message;", '    return chain_fail(ctx, "implement the input callback");', "}",
                      f"const ComponentCallbacks component_callbacks = {{ .on_{port} = on_{port} }};", ""]
        else:
            args = "ChainContext *ctx, const ComponentInputs *inputs" if node["inputs"] else "ChainContext *ctx"
            lines += [f"static int on_tick({args}) {{"]
            if node["inputs"]:
                lines.append("    (void)inputs;")
            lines += ['    return chain_fail(ctx, "implement the tick callback");', "}",
                      "const ComponentCallbacks component_callbacks = { .on_tick = on_tick };", ""]
    else:
        lines = ["// User callbacks. Never overwritten by the builder.", "use crate::container::{Callbacks, Context, Result};",
                 "#[derive(Default)]", "pub struct Handler;", "impl Callbacks for Handler {"]
        if len(node["inputs"]) == 1:
            port, desc = next(iter(node["inputs"].items()))
            module, symbol = types[desc["root"]]
            lines += [f"    fn on_{port}(&mut self, _ctx: &mut Context, _message: &crate::types::{module}::{symbol}) -> Result {{",
                      '        Err("implement the input callback".into())']
        else:
            args = "&mut self, _ctx: &mut Context, _inputs: &crate::container::Inputs<'_>" if node["inputs"] else "&mut self, _ctx: &mut Context"
            lines += [f"    fn on_tick({args}) -> Result {{", '        Err("implement the tick callback".into())']
        lines += ["    }", "}", ""]
    handler.parent.mkdir(parents=True, exist_ok=True)
    try:
        with handler.open("x") as stream:
            stream.write("\n".join(lines))
    except FileExistsError:
        pass
