import copy
import json
from pathlib import Path
import unittest
from ose.modeling.validation import validate_graph


class ModelTests(unittest.TestCase):
    def setUp(self):
        project = Path(__file__).resolve().parents[2] / "examples/acquisition"
        self.model = json.loads((project / "model.json").read_text())
        self.deployment = json.loads((project / "deployment.json").read_text())

    def test_example_and_graph_rejections(self):
        validate_graph(self.model, self.deployment)
        bad = copy.deepcopy(self.model)
        bad["subscriptions"].append(dict(bad["subscriptions"][0], id="duplicate-input"))
        with self.assertRaisesRegex(ValueError, "one producer"):
            validate_graph(bad, self.deployment)
        bad = copy.deepcopy(self.model)
        bad["components"]["acquisition"]["inputs"] = {"input": {"root": "Measurement"}}
        bad["subscriptions"].append({"id": "cycle", "from": "analyse.result", "to": "acquisition.input"})
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_graph(bad, self.deployment)
        bad = copy.deepcopy(self.model)
        bad["components"]["analyse"]["inputs"]["extra"] = {"root": "Measurement"}
        with self.assertRaisesRegex(ValueError, "unconnected"):
            validate_graph(bad, self.deployment)

    def test_generated_container_validation(self):
        for container, message in (({"language": "go", "handler": "logic.go"}, "language must"),
                                   ({"language": "python", "handler": ""}, "nonempty")):
            bad = copy.deepcopy(self.deployment)
            bad["components"]["observation"]["container"] = container
            with self.assertRaisesRegex(ValueError, message):
                validate_graph(self.model, bad)
        bad = copy.deepcopy(self.deployment)
        bad["components"]["observation"]["command"] = ["python3"]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_graph(self.model, bad)
        bad = copy.deepcopy(self.model)
        bad["components"]["observation"]["inputs"]["tick"] = {"root": "Measurement", "required": False}
        with self.assertRaisesRegex(ValueError, "reserved callback"):
            validate_graph(bad, self.deployment)

    def test_required_input_timeout_validation(self):
        for value in (.5, 1, 1e-6):
            model = copy.deepcopy(self.model)
            model["components"]["analyse"]["inputs"]["input"]["timeout"] = value
            validate_graph(model, self.deployment)
        for value in (0, -1, True, None, "0.5", float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(value=str(value)[:30]):
                model = copy.deepcopy(self.model)
                model["components"]["analyse"]["inputs"]["input"]["timeout"] = value
                with self.assertRaisesRegex(ValueError, "finite positive"):
                    validate_graph(model, self.deployment)
        for direction in ("inputs", "outputs"):
            model = copy.deepcopy(self.model)
            model["components"]["analyse"][direction]["optional"] = dict(root="Measurement", required=False, timeout=.5)
            with self.assertRaisesRegex(ValueError, "only supported on required inputs"):
                validate_graph(model, self.deployment)
        model = copy.deepcopy(self.model)
        model["components"]["analyse"]["outputs"]["result"]["timeout"] = .5
        with self.assertRaisesRegex(ValueError, "only supported on required inputs"):
            validate_graph(model, self.deployment)

    def test_native_container_languages_and_port_identifiers(self):
        for language, suffix in (("c", ".c"), ("rust", ".rs")):
            deployment = copy.deepcopy(self.deployment)
            deployment["components"]["observation"]["container"] = {"language": language, "handler": "logic" + suffix}
            validate_graph(self.model, deployment)
            model = copy.deepcopy(self.model)
            model["components"]["observation"]["inputs"]["match"] = {"root": "Measurement", "required": False}
            with self.assertRaisesRegex(ValueError, "non-reserved ASCII"):
                validate_graph(model, deployment)

    def test_external_topics_and_explicit_input_bindings(self):
        model=copy.deepcopy(self.model)
        sub=model['subscriptions'][0]
        topic='external.measurement'
        node,port=sub['from'].split('.')
        model['external_topics']={topic:{'root':model['components'][node]['outputs'][port]['root']}}
        sub.pop('from');sub['from_topic']=topic
        validate_graph(model,self.deployment)
        for change in ('both','unknown','collision'):
            bad=copy.deepcopy(model)
            if change=='both':bad['subscriptions'][0]['from']='source.output'
            elif change=='unknown':bad['subscriptions'][0]['from_topic']='unknown'
            else:bad['external_topics'][f'{node}.{port}']=bad['external_topics'][topic]
            with self.assertRaises(ValueError):validate_graph(bad,self.deployment)
        for limits in ({'max_capacity':0},{'enabled':'yes'},{'durability':'disk'}):
            deployment=copy.deepcopy(self.deployment);deployment['broker']={'external':limits}
            with self.assertRaises(ValueError):validate_graph(model,deployment)
