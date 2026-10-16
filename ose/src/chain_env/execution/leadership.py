"""Kubernetes Lease ownership with conservative monotonic expiry and CAS updates.

A new owner observes an unchanged resource version for the complete lease duration
before taking over. It does not compare different machines' wall clocks. Local
authorization expires before that duration, measured from renewal request START.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def lease_clock():
    """Include host suspend time on Linux, so resuming cannot revive a lease."""
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


class KubernetesLease:
    def __init__(self, name, namespace, *, duration=10, request=None, clock=None):
        self.name, self.namespace = name, namespace
        self.duration, self.clock = duration, clock or lease_clock
        self.owner = uuid.uuid4().hex
        self.deadline = 0
        self.observed = None
        self.observed_at = 0
        self.request = request or self._request

    def _request(self, method, body=None):
        directory = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        if ":" in host:
            host = "[" + host + "]"
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        url = f"https://{host}:{port}/apis/coordination.k8s.io/v1/namespaces/" + urllib.parse.quote(self.namespace, safe="") + "/leases/" + urllib.parse.quote(self.name, safe="")
        # Read projected tokens on every operation so rotation is respected.
        headers = {"Authorization": "Bearer " + (directory / "token").read_text().strip(),
                   "Content-Type": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method),
                                    context=context, timeout=2) as response:
            return json.load(response)

    def check(self):
        if self.clock() >= self.deadline:
            raise ValueError("coordinator leadership authorization expired")

    async def attempt(self):
        started = self.clock()
        document = await asyncio.to_thread(self.request, "GET")
        version = document["metadata"]["resourceVersion"]
        spec = document.get("spec", {})
        holder = spec.get("holderIdentity")
        if holder and holder != self.owner:
            if self.observed != version:
                self.observed, self.observed_at = version, self.clock()
            if self.clock() - self.observed_at < max(1, spec.get("leaseDurationSeconds", self.duration)):
                return False
        body = {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease",
                "metadata": {**document["metadata"], "name": self.name, "namespace": self.namespace, "resourceVersion": version},
                "spec": {**spec, "holderIdentity": self.owner, "leaseDurationSeconds": self.duration,
                         "renewTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}}
        updated = await asyncio.to_thread(self.request, "PUT", body)
        self.deadline = started + self.duration * .8
        self.observed, self.observed_at = updated["metadata"]["resourceVersion"], self.clock()
        self.check()
        return True

    async def acquire(self):
        while True:
            try:
                if await self.attempt():
                    self.task = asyncio.create_task(self.maintain())
                    return
            except (OSError, urllib.error.URLError) as error:
                print(f"coordinator: waiting for Kubernetes Lease: {error}", file=sys.stderr, flush=True)
            await asyncio.sleep(1)

    async def maintain(self):
        while True:
            await asyncio.sleep(self.duration / 4)
            self.check()
            try:
                if not await self.attempt():
                    self.deadline = 0
                    raise RuntimeError("coordinator lease belongs to another instance")
            except (OSError, urllib.error.URLError):
                self.check()

    async def close(self):
        self.deadline = 0
        if hasattr(self, "task"):
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # Keep the final lease record. A replacement waits its full observation
        # period, also covering delayed API requests from a terminating owner.
