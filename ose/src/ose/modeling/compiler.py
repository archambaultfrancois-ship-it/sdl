"""Resolve and build a graph using only the public SDL generator/runtime."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy
from ose.modeling.model import load, fields
from ose.modeling.validation import validate_graph
from ose.modeling.containers import generate_python, scaffold_handler
from ose.modeling.native_containers import generate_c, generate_rust, scaffold_native
from ose.modeling.managed_containers import generate_java, generate_ada, scaffold_managed

BACKENDS = {"c": generate_c, "rust": generate_rust, "java": generate_java, "ada": generate_ada}


def compile_plan(model_path, deployment_path, sdl_root, destination):
    model_path, deployment_path = Path(model_path).resolve(), Path(deployment_path).resolve()
    model, deployment = load(model_path), load(deployment_path)
    validate_graph(model, deployment)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    codec, schema_cache, generated_paths = SDL2(sdl_root), {}, {}
    schema_languages = {}
    parameter_defs = model.get("shared_parameters", {})
    for component, node in model["components"].items():
        language = deployment["components"][component].get("container", {}).get("language")
        if language:
            schema_languages.setdefault((model_path.parent / model["schemas"]).resolve(), set()).add(language)
        descriptions = [desc for direction in ("inputs", "outputs", "recording_types") for desc in node.get(direction, {}).values()]
        descriptions += [parameter_defs[b["path"]] for b in node.get("parameter_bindings", {}).values()]
        for desc in descriptions:
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
    from ose.parameters.values import Values
    shared_parameters, parameter_codecs = {}, {}
    for path, desc in parameter_defs.items():
        text = catalogue(desc.get("schema", model["schemas"]))
        key = (text, desc["root"])
        if key not in parameter_codecs:
            parameter_codecs[key] = Values(codec.api, text, desc["root"])
        payload = parameter_codecs[key].encode(desc["initial"])
        shared_parameters[path] = {"root": desc["root"], "catalogue": text,
                                   "initial_payload": base64.b64encode(payload).decode()}

    for name, original in model["components"].items():
        node = json.loads(json.dumps(original))
        for direction in ("inputs", "outputs", "recording_types"):
            for port in node.get(direction, {}).values():
                port["catalogue"] = catalogue(port.get("schema", model["schemas"]))
                port["catalogue_id"] = codec.identity(port["catalogue"])
                if port["root"] not in codec.api.message_names(codec.api.prepare(port["catalogue"])):
                    raise ValueError(f"unknown root {port['root']}")
        for alias, binding in node.get("parameter_bindings", {}).items():
            desc = shared_parameters[binding["path"]]
            binding.update(root=desc["root"], catalogue=desc["catalogue"])
        execution = deployment["components"][name]
        generated_build = []
        if "container" in execution:
            handler = handlers[name]
            language = execution["container"]["language"]
            paths = dict.fromkeys([(model_path.parent / desc.get("schema", model["schemas"])).resolve()
                                  for desc in [d for direction in ("inputs", "outputs", "recording_types") for d in node.get(direction, {}).values()] +
                                  [parameter_defs[b["path"]] for b in node.get("parameter_bindings", {}).values()]])
            if not paths:
                paths = {(model_path.parent / model["schemas"]).resolve(): None}
            node["recording_catalogues"] = {}
            for path in paths:
                text = catalogue(str(path))
                for root in codec.api.message_names(codec.api.prepare(text)):
                    previous = node["recording_catalogues"].get(root)
                    if previous is not None and previous != text:
                        codec.compare(previous, text, root)
                    node["recording_catalogues"][root] = text
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
        if "placement" in execution:
            node["placement"] = execution["placement"]
        node.pop("recording_catalogues", None)  # Generator-only descriptors; do not duplicate them in INIT.
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
        fields(binding, ("transport", "routing"))
        routing = binding.get("routing", "broker")
        if routing not in ("broker", "direct"):
            raise ValueError("routing must be broker or direct")
        if routing == "direct" and deployment.get("execution") != "distributed":
            raise ValueError("direct routes require execution=distributed")
        if "from_topic" in sub and routing != "broker":
            raise ValueError("external inputs require broker routing")
        policy = SubscriptionPolicy.resolve(sub.get("qos"), binding.get("transport", "tcp"))
        if deployment.get("execution") == "distributed" and policy.transport != "tcp":
            raise ValueError("distributed execution currently requires TCP")
        subscriptions.append({**sub, "from": sub.get("from", sub.get("from_topic")), "policy": policy.to_dict(),
                              **({"routing": routing} if deployment.get("execution") == "distributed" else {})})
    for key, default in (("max_frame", 1024 * 1024), ("max_datagram", 1200)):
        value = deployment.get(key, default)
        if type(value) is not int or value < 128 or (key == "max_datagram" and value > 65507):
            raise ValueError(f"invalid {key}")
    from ose.parameters.service import ParameterService
    ParameterService({"shared_parameters": shared_parameters, "max_frame": deployment.get("max_frame", 1024 * 1024)}, codec)
    return {"version": 1, "execution": deployment.get("execution", "local"), "sdl_root": str(codec.root), "components": components, "subscriptions": subscriptions,
            "shared_parameters": shared_parameters, "parameter_clients": deployment.get("parameter_clients", {}),
            "external_topics": external_topics, "broker": deployment.get("broker", {}), "build_dir": str(destination), "max_frame": deployment.get("max_frame", 1024 * 1024),
            "max_datagram": deployment.get("max_datagram", 1200)}


def build(model, deployment, sdl_root, destination, component=None):
    plan = compile_plan(model, deployment, sdl_root, destination)
    if component is not None and component not in plan["components"]:
        raise ValueError("unknown component to build")
    nodes = plan["components"].values() if component is None else [plan["components"][component]]
    for node in nodes:
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
    with tempfile.TemporaryDirectory(prefix="ose-validate-") as directory:
        compile_plan(model, deployment, sdl_root, directory)
