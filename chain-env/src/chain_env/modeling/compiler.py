"""Resolve and build a graph using only the public SDL generator/runtime."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from chain_env.codec.sdl2 import SDL2
from chain_env.delivery.policies import SubscriptionPolicy
from chain_env.modeling.model import load, fields
from chain_env.modeling.validation import validate_graph
from chain_env.modeling.containers import generate_python, scaffold_handler
from chain_env.modeling.native_containers import generate_c, generate_rust, scaffold_native
from chain_env.modeling.managed_containers import generate_java, generate_ada, scaffold_managed

BACKENDS = {"c": generate_c, "rust": generate_rust, "java": generate_java, "ada": generate_ada}


def compile_plan(model_path, deployment_path, sdl_root, destination):
    model_path, deployment_path = Path(model_path).resolve(), Path(deployment_path).resolve()
    model, deployment = load(model_path), load(deployment_path)
    validate_graph(model, deployment)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    codec, schema_cache, generated_paths = SDL2(sdl_root), {}, {}
    schema_languages = {}
    for component, node in model["components"].items():
        language = deployment["components"][component].get("container", {}).get("language")
        for direction in ("inputs", "outputs"):
            for desc in node[direction].values():
                schema_path = (model_path.parent / desc.get("schema", model["schemas"])).resolve()
                if language:
                    schema_languages.setdefault(schema_path, set()).add(language)
    def catalogue(path):
        path = (model_path.parent / path).resolve()
        if path not in schema_cache:
            shared = path == (model_path.parent / model["schemas"]).resolve()
            name = "shared" if shared else hashlib.sha256(str(path).encode()).hexdigest()[:12]
            generated_paths[path] = destination / "generated" / name
            schema_cache[path] = codec.generate(path, generated_paths[path], schema_languages.get(path, ()))
        return schema_cache[path]
    components = {}
    substitutions = {"project": str(model_path.parent), "build": str(destination),
                     "sdl_root": str(codec.root), "generated": str(destination / "generated/shared"),
                     "python": sys.executable}
    def expand(value):
        for key, replacement in substitutions.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    handlers = {}
    for name, execution in deployment["components"].items():
        if "container" in execution:
            handler = (deployment_path.parent / expand(execution["container"]["handler"])).resolve()
            suffix = {"python": ".py", "c": ".c", "rust": ".rs", "java": ".java", "ada": ".adb"}[execution["container"]["language"]]
            if handler.suffix != suffix or (handler.exists() and not handler.is_file()):
                raise ValueError(f"container handler must name a {suffix} file")
            if handler.is_relative_to(destination):
                raise ValueError("container handler must be outside the generated build directory")
            handlers[name] = handler
    catalogue(model["schemas"])
    for name, original in model["components"].items():
        node = json.loads(json.dumps(original))
        for direction in ("inputs", "outputs"):
            for port in node[direction].values():
                port["catalogue"] = catalogue(port.get("schema", model["schemas"]))
                port["catalogue_id"] = codec.identity(port["catalogue"])
                if port["root"] not in codec.api.message_names(codec.api.prepare(port["catalogue"])):
                    raise ValueError(f"unknown root {port['root']}")
        execution = deployment["components"][name]
        generated_build = []
        if "container" in execution:
            handler = handlers[name]
            language = execution["container"]["language"]
            paths = dict.fromkeys((model_path.parent / desc.get("schema", model["schemas"])).resolve()
                                  for direction in ("inputs", "outputs") for desc in node[direction].values())
            schemas = [(generated_paths[path] / language, codec.python_bindings(generated_paths[path]) if language == "python"
                        else codec.native_bindings(generated_paths[path], language)) for path in paths]
            component_dir = destination / "components" / name
            if language == "python":
                wrapper = generate_python(name, node, handler, codec.root, schemas, component_dir)
                types = {logical: binding for _, bindings in schemas for logical, binding in bindings.items()}
                node["command"] = [sys.executable, str(wrapper)]
            else:
                kwargs = {"identity": execution["container"].get("class", execution["container"].get("unit"))} if language in ("java", "ada") else {}
                generated = BACKENDS[language](name, node, handler, codec.root, schemas, component_dir, **kwargs)
                types, node["command"], generated_build = generated["types"], generated["command"], generated["build"]
            node["container"] = {**execution["container"], "handler": str(handler), "types": types}
        else:
            node["command"] = [expand(v) for v in execution["command"]]
        node["build"] = [[expand(v) for v in command] for command in execution.get("build", [])] + generated_build
        node["cwd"] = str((deployment_path.parent / expand(execution.get("cwd", "."))).resolve())
        node["env"] = {k: expand(v) for k, v in execution.get("env", {}).items()}
        components[name] = node
    external_topics = {}
    for topic, desc in model.get("external_topics", {}).items():
        text = catalogue(desc.get("schema", model["schemas"]))
        if desc["root"] not in codec.api.message_names(codec.api.prepare(text)):
            raise ValueError(f"unknown root {desc['root']}")
        external_topics[topic] = {**desc, "catalogue": text, "catalogue_id": codec.identity(text)}
    subscriptions = []
    for sub in model["subscriptions"]:
        if "from" in sub:
            source, output = sub["from"].split(".")
            publisher = components[source]["outputs"][output]
        else:
            publisher = external_topics[sub["from_topic"]]
        target, input_port = sub["to"].split(".")
        consumer = components[target]["inputs"][input_port]
        if publisher["root"] != consumer["root"]:
            raise ValueError("connected port roots must have the same logical name")
        codec.compare(publisher["catalogue"], consumer["catalogue"], publisher["root"])
        binding = deployment.get("subscriptions", {}).get(sub["id"], {})
        fields(binding, ("transport",))
        policy = SubscriptionPolicy.resolve(sub.get("qos"), binding.get("transport", "tcp"))
        subscriptions.append({**sub, "from": sub.get("from", sub.get("from_topic")), "policy": policy.to_dict()})
    for key, default in (("max_frame", 1024 * 1024), ("max_datagram", 1200)):
        value = deployment.get(key, default)
        if type(value) is not int or value < 128 or (key == "max_datagram" and value > 65507):
            raise ValueError(f"invalid {key}")
    return {"version": 1, "sdl_root": str(codec.root), "components": components, "subscriptions": subscriptions,
            "external_topics": external_topics, "broker": deployment.get("broker", {}), "build_dir": str(destination), "max_frame": deployment.get("max_frame", 1024 * 1024),
            "max_datagram": deployment.get("max_datagram", 1200)}


def build(model, deployment, sdl_root, destination):
    plan = compile_plan(model, deployment, sdl_root, destination)
    for node in plan["components"].values():
        if "container" in node:
            container = node["container"]
            if container["language"] == "python":
                scaffold_handler(node, container["handler"], container["types"])
            elif container["language"] in ("java", "ada"):
                scaffold_managed(node, container)
            else:
                scaffold_native(node, container["handler"], container["types"], container["language"])
        env = dict(os.environ, **node["env"])
        for command in node["build"]:
            subprocess.run(command, cwd=node["cwd"], env=env, check=True)
    path = Path(destination).resolve() / "plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n")
    return path


def validate(model, deployment, sdl_root):
    with tempfile.TemporaryDirectory(prefix="sdl-chain-validate-") as directory:
        compile_plan(model, deployment, sdl_root, directory)
