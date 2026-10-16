"""All Python SDL integration is confined to public runtime APIs here."""
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


from chain_env.codec.runtime import Runtime


class SDL2(Runtime):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._bindings = {}
        runtime = self.root / "runtime/python"
        if not (runtime / "sdl_runtime.py").is_file():
            raise ValueError(f"not an SDL checkout: {self.root}")
        sys.path.insert(0, str(runtime))
        self.api = importlib.import_module("sdl_runtime")
        origin = Path(self.api.__file__).resolve()
        if origin != runtime / "sdl_runtime.py":
            raise ValueError("a different SDL runtime is already loaded")

    def generate(self, schema, destination, languages=()):
        subprocess.run([sys.executable, str(self.root / "gen/generator.py"), "-python", "-c", "-rust",
                        *("-" + lang for lang in sorted(set(languages) & {"java", "ada"})), str(schema), str(destination)], check=True)
        # A separate interpreter prevents class/module collisions between schema revisions.
        program = '''import importlib.util, json, pathlib, sys
from sdl_runtime import description, message_name, SdlMessage
base=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(base))
types=[]; modules=[]
for p in sorted(base.rglob('*.py')):
 if p.name == '__init__.py': continue
 name='.'.join(p.relative_to(base).with_suffix('').parts)
 spec=importlib.util.spec_from_file_location(name,p)
 m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
 modules.append((name,m))
# Register after execution so filenames do not shadow imports during initialization.
for name,m in modules: sys.modules[name]=m
for name,m in modules:
 for name, value in vars(m).items():
  if isinstance(value,type) and issubclass(value,SdlMessage) and value is not SdlMessage and value.__module__==m.__name__: types.append(value)
print(json.dumps({'catalogue':description(types), 'types':{message_name(t):[t.__module__,t.__name__] for t in types}}))
'''
        env = dict(os.environ, PYTHONPATH=str(self.root / "runtime/python"))
        text = subprocess.check_output([sys.executable, "-c", program, str(Path(destination) / "python")], env=env, text=True)
        result = json.loads(text)
        self._bindings[Path(destination).resolve()] = result["types"]
        return result["catalogue"]

    def python_bindings(self, destination):
        """Logical names and imports discovered through generated public message classes."""
        return self._bindings[Path(destination).resolve()]

    def native_bindings(self, destination, language):
        """Resolve public generated header/module names and verify their symbols."""
        if language in ("java", "ada"):
            from chain_env.modeling.managed_containers import discover_bindings
            return discover_bindings(destination, language, self.python_bindings(destination))
        result = {}
        directory = Path(destination) / language
        for logical, (module, _) in self.python_bindings(destination).items():
            if language == "c":
                if "." in logical:
                    symbol = "sdlq_" + "_".join(f"{len(part.encode())}_{part.encode().hex()}" for part in logical.split("."))
                else:
                    symbol = re.sub(r"\W", "_", logical)
                package, _, base = module.rpartition(".")
                candidates = [p for p in (directory / package.replace(".", "/")).glob("*.h")
                              if re.sub(r"\W", "_", p.stem).lower() == base]
                if len(candidates) != 1 or not re.search(r"\b" + re.escape(symbol) + r"\b", candidates[0].read_text()):
                    raise ValueError(f"cannot resolve generated C binding for {logical}")
                result[logical] = [candidates[0].relative_to(directory).as_posix(), symbol]
            else:
                local = logical.rsplit(".", 1)[-1]
                symbol = re.sub(r"\W", "_", local) if "$" not in local else local.replace("$", "")
                source = directory / (module.replace(".", "/") + ".rs")
                if not source.is_file() or f"pub struct {symbol} " not in source.read_text():
                    raise ValueError(f"cannot resolve generated Rust binding for {logical}")
                result[logical] = [module.replace(".", "::"), symbol]
        return result
