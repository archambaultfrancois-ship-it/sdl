"""Export immutable per-component deployments; no Kubernetes client dependency."""
import json
from pathlib import Path
import re
from ose.execution.distributed import check_plan


def dns_name(value):
    result = value.replace("_", "-").lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", result) or len(result) > 50:
        raise ValueError("component names must map to DNS names of at most 50 characters")
    return result


def resources(plan, images, namespace, image_pull_secrets=()):
    check_plan(plan)
    if dns_name(namespace) != namespace:
        raise ValueError("namespace must be a lowercase DNS name")
    for secret in image_pull_secrets:
        if dns_name(secret) != secret:
            raise ValueError("image pull secret must be a lowercase DNS name")
    names = {name: dns_name(name) for name in plan["components"]}
    if len(set(names.values())) != len(names) or "coordinator" in names.values():
        raise ValueError("component DNS names collide with each other or the coordinator")
    if set(images) != {*names, "coordinator"}:
        raise ValueError("images must cover coordinator and every component")
    for image in images.values():
        if not isinstance(image, str) or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("images must be pinned by sha256 digest")
    items = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}]
    items += [
        {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease", "metadata": {"name": "ose-coordinator", "namespace": namespace},
         "spec": {"leaseDurationSeconds": 10}},
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "ose-coordinator", "namespace": namespace}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "ose-coordinator", "namespace": namespace},
         "rules": [{"apiGroups": ["coordination.k8s.io"], "resources": ["leases"], "resourceNames": ["ose-coordinator"], "verbs": ["get", "update"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "ose-coordinator", "namespace": namespace},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "ose-coordinator"},
         "subjects": [{"kind": "ServiceAccount", "name": "ose-coordinator", "namespace": namespace}]},
    ]
    for name in ["coordinator", *names]:
        service = "ose-" + ("coordinator" if name == "coordinator" else names[name])
        labels = {"app.kubernetes.io/name": "ose", "ose/component": service}
        metadata = {"name": service, "namespace": namespace, "labels": labels}
        tls = ["--tls-ca", "/run/ose-tls/ca.crt", "--tls-cert", "/run/ose-tls/tls.crt", "--tls-key", "/run/ose-tls/tls.key"]
        if name == "coordinator":
            args = ["serve", "--plan", "/app/build/plan.json", "--host", "0.0.0.0", "--port", "7000",
                    "--kubernetes-lease", "ose-coordinator", "--namespace", namespace, *tls]
            placement = {"resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                                        "limits": {"memory": "512Mi"}}}
        else:
            args = ["worker", "--plan", "/app/build/plan.json", "--node", name,
                    "--host", "ose-coordinator", "--port", "7000", "--listen-host", "0.0.0.0",
                    "--listen-port", "7001", "--advertise-host", service, *tls]
            placement = plan["components"][name].get("placement", {})
            if placement == "local":
                placement = {}
        port = 7000 if name == "coordinator" else 7001
        container = {"name": "ose", "image": images[name], "imagePullPolicy": "IfNotPresent",
                     "command": ["ose-ctl"], "args": args, "ports": [{"containerPort": port}],
                     "volumeMounts": [{"name": "tls", "mountPath": "/run/ose-tls", "readOnly": True}],
                     "resources": placement.get("resources", {"requests": {"cpu": "100m", "memory": "128Mi"}}),
                     "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                     "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 2}}
        pod = {"terminationGracePeriodSeconds": 10, "containers": [container],
               "automountServiceAccountToken": name == "coordinator",
               "tolerations": [{"key": "node.kubernetes.io/" + condition, "operator": "Exists",
                                "effect": "NoExecute", "tolerationSeconds": 10}
                               for condition in ("not-ready", "unreachable")],
               "nodeSelector": {"kubernetes.io/os": "linux", **placement.get("node_selector", {})},
               "volumes": [{"name": "tls", "secret": {"secretName": service + "-tls"}}],
               "topologySpreadConstraints": [{"maxSkew": 1, "topologyKey": "kubernetes.io/hostname",
                   "whenUnsatisfiable": "ScheduleAnyway", "labelSelector": {"matchLabels": {"app.kubernetes.io/name": "ose"}}}]}
        if name == "coordinator":
            pod["serviceAccountName"] = "ose-coordinator"
        if image_pull_secrets:
            pod["imagePullSecrets"] = [{"name": secret} for secret in image_pull_secrets]
        items += [{"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata,
                   "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": labels},
                            "template": {"metadata": {"labels": labels}, "spec": pod}}},
                  {"apiVersion": "v1", "kind": "Service", "metadata": metadata,
                   "spec": {"selector": labels, "ports": [{"name": "ose", "port": port, "targetPort": port}]}}]
    return {"apiVersion": "v1", "kind": "List", "items": items}


def export(plan_path, images_path, namespace, output, image_pull_secrets=()):
    manifest = resources(json.loads(Path(plan_path).read_text()), json.loads(Path(images_path).read_text()), namespace, image_pull_secrets)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2) + "\n")
