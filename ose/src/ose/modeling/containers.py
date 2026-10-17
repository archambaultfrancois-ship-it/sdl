"""Generate Python wrappers without overwriting user callback implementations."""
from pathlib import Path
import shutil
import sys


def generate_python(name, node, handler, sdl_root, schemas, destination):
    """Merge compatible generated modules and emit a typed executable context."""
    destination = Path(destination)
    bindings = destination / "types"
    bindings.mkdir(parents=True, exist_ok=True)
    files, roots = {}, {}
    for directory, types in schemas:
        directory = Path(directory)
        for module, _ in types.values():
            if module.split(".")[0] in sys.stdlib_module_names | {"sdl_runtime", "ose"}:
                raise ValueError(f"Python schema module shadows the standard library or SDK: {module}")
        for source in directory.rglob("*.py"):
            if source.name == "__init__.py":
                continue
            relative = source.relative_to(directory)
            content = source.read_bytes()
            if relative in files and files[relative] != content:
                raise ValueError(f"Python container {name}: conflicting generated module {relative}; use distinct SDL module names")
            files[relative] = content
        for logical, binding in types.items():
            if logical in roots and roots[logical] != binding:
                raise ValueError(f"Python container {name}: conflicting bindings for {logical}")
            roots[logical] = binding
    # Only disposable generated bindings are replaced; the handler is never copied here.
    if bindings.exists():
        shutil.rmtree(bindings)
    bindings.mkdir()
    for relative, content in files.items():
        target = bindings / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        parent = target.parent
        while parent != bindings:
            (parent / "__init__.py").touch()
            parent = parent.parent

    imports, aliases, mapped = [], {}, {"inputs": {}, "outputs": {}}
    for direction in mapped:
        for port, desc in node[direction].items():
            module, symbol = roots[desc["root"]]
            key = (module, symbol)
            if key not in aliases:
                alias = f"_Message{len(aliases)}"
                aliases[key] = alias
                imports.append(f"from {module} import {symbol} as {alias}")
            mapped[direction][port] = aliases[key]
    lines = ["# Generated from the component model. Edit the handler, not this file.",
             "from pathlib import Path", "import sys",
             f"sys.path.insert(0, {str(Path(sdl_root) / 'runtime/python')!r})",
             "sys.path.insert(0, str(Path(__file__).resolve().parent / 'types'))",
             "from ose.execution.callbacks import CallbackContext, run_callbacks",
             *imports, "", "", "class Context(CallbackContext):"]
    if not mapped["outputs"]:
        lines.append("    pass")
    for port, alias in mapped["outputs"].items():
        lines += [f"    def publish_{port}(self, message: {alias}) -> None:",
                  f"        self.publish({port!r}, message)", ""]
    def mapping(direction):
        return "{" + ", ".join(f"{port!r}: {alias}" for port, alias in mapped[direction].items()) + "}"
    lines += ["", "if __name__ == '__main__':",
              f"    run_callbacks(name={name!r}, handler={str(handler)!r}, sdl_root={str(sdl_root)!r},",
              f"                  input_types={mapping('inputs')}, output_types={mapping('outputs')}, context_type=Context)", ""]
    path = destination / "container.py"
    path.write_text("\n".join(lines))
    return path


def scaffold_handler(node, handler, types):
    """Create an editable starting point once; existing files are left untouched."""
    handler = Path(handler)
    if handler.exists():
        if not handler.is_file():
            raise ValueError(f"handler is not a file: {handler}")
        return
    lines = ['"""User callbacks. This file is never overwritten by the builder."""']
    if len(node["inputs"]) == 1:
        port, desc = next(iter(node["inputs"].items()))
        module, symbol = types[desc["root"]]
        annotation = "InputMessage | None" if "timeout" in desc else "InputMessage"
        lines += [f"from {module} import {symbol} as InputMessage", "", "",
                  f"def on_{port}(ctx, message: {annotation}):",
                  '    raise NotImplementedError("Implement the input callback")']
    else:
        args = "ctx, inputs" if node["inputs"] else "ctx"
        lines += ["", "", f"def on_tick({args}):",
                  '    raise NotImplementedError("Implement the tick callback")']
    handler.parent.mkdir(parents=True, exist_ok=True)
    try:
        with handler.open("x") as stream:
            stream.write("\n".join(lines) + "\n")
    except FileExistsError:
        pass
