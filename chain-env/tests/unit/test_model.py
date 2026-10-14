import copy
import json
from pathlib import Path
import unittest
from chain_env.modeling.validation import validate_graph


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

    def test_native_container_languages_and_port_identifiers(self):
        for language, suffix in (("c", ".c"), ("rust", ".rs")):
            deployment = copy.deepcopy(self.deployment)
            deployment["components"]["observation"]["container"] = {"language": language, "handler": "logic" + suffix}
            validate_graph(self.model, deployment)
            model = copy.deepcopy(self.model)
            model["components"]["observation"]["inputs"]["match"] = {"root": "Measurement", "required": False}
            with self.assertRaisesRegex(ValueError, "non-reserved ASCII"):
                validate_graph(model, deployment)
