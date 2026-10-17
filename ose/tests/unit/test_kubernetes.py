import copy
import unittest
from ose.modeling.kubernetes import resources


class KubernetesTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"execution": "distributed", "components": {"source_1": {}}, "subscriptions": []}
        self.images = dict(coordinator="registry.lan/app/coordinator@sha256:" + "a" * 64,
                           source_1="registry.lan/app/source@sha256:" + "b" * 64)

    def test_pinned_images_tls_secrets_and_replacement(self):
        manifest = resources(self.plan, self.images, "test-app", ["registry-auth"])
        deployments = [item for item in manifest["items"] if item["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 2)
        for item in deployments:
            self.assertEqual(item["spec"]["strategy"]["type"], "Recreate")
            pod = item["spec"]["template"]["spec"]
            self.assertEqual(pod["imagePullSecrets"], [{"name": "registry-auth"}])
            if item["metadata"]["name"] == "ose-coordinator":
                self.assertTrue(pod["automountServiceAccountToken"])
                self.assertIn("--kubernetes-lease", pod["containers"][0]["args"])
            else:
                self.assertFalse(pod["automountServiceAccountToken"])
            self.assertIn("--tls-ca", pod["containers"][0]["args"])
            self.assertNotIn("--insecure", pod["containers"][0]["args"])
            self.assertIn("@sha256:", pod["containers"][0]["image"])

    def test_rejects_unpinned_images_and_ambiguous_names(self):
        images = dict(self.images, source_1="registry.lan/app:latest")
        with self.assertRaises(ValueError):
            resources(self.plan, images, "test-app")
        plan = copy.deepcopy(self.plan)
        plan["components"]["source-1"] = {}
        with self.assertRaises(ValueError):
            resources(plan, self.images, "test-app")
