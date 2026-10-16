"""Exercise lease takeover and fencing without relying on synchronized wall clocks."""
import copy
import asyncio
import threading
import unittest
import urllib.error
from chain_env.execution.leadership import KubernetesLease


class Store:
    def __init__(self):
        self.document = {"metadata": {"resourceVersion": "1"}, "spec": {"leaseDurationSeconds": 10}}
        self.lock = threading.Lock()

    def request(self, method, body=None):
        with self.lock:
            if method == "PUT":
                if body["metadata"]["resourceVersion"] != self.document["metadata"]["resourceVersion"]:
                    raise urllib.error.HTTPError("https://cluster/lease", 409, "conflict", {}, None)
                version = str(int(self.document["metadata"]["resourceVersion"]) + 1)
                self.document = copy.deepcopy(body)
                self.document["metadata"]["resourceVersion"] = version
            return copy.deepcopy(self.document)


class LeadershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_initial_acquisition_has_one_winner(self):
        store = Store()
        barrier = threading.Barrier(2)
        def request(method, body=None):
            result = store.request(method, body)
            if method == "GET":
                barrier.wait(timeout=3)
            return result
        leases = [KubernetesLease("lease", "test", request=request) for _ in range(2)]
        results = await asyncio.gather(*(lease.attempt() for lease in leases), return_exceptions=True)
        self.assertEqual(sum(result is True for result in results), 1)
        self.assertEqual(sum(isinstance(result, urllib.error.HTTPError) for result in results), 1)
        for lease, result in zip(leases, results):
            if result is True:
                lease.check()
            else:
                with self.assertRaises(ValueError):
                    lease.check()

    async def test_takeover_waits_full_observation_period_and_old_authority_expires_first(self):
        store, now = Store(), [0.0]
        first = KubernetesLease("lease", "test", request=store.request, clock=lambda: now[0])
        second = KubernetesLease("lease", "test", request=store.request, clock=lambda: now[0])
        self.assertTrue(await first.attempt())
        first.check()
        self.assertFalse(await second.attempt())
        now[0] = 8.1
        with self.assertRaises(ValueError):
            first.check()
        self.assertFalse(await second.attempt())
        now[0] = 10.1
        self.assertTrue(await second.attempt())
        second.check()
        self.assertFalse(await first.attempt())
        with self.assertRaises(ValueError):
            first.check()

    async def test_observed_renewal_restarts_takeover_wait(self):
        store, now = Store(), [0.0]
        first = KubernetesLease("lease", "test", request=store.request, clock=lambda: now[0])
        second = KubernetesLease("lease", "test", request=store.request, clock=lambda: now[0])
        await first.attempt()
        await second.attempt()
        now[0] = 5
        await first.attempt()
        self.assertFalse(await second.attempt())
        now[0] = 11
        self.assertFalse(await second.attempt())
        now[0] = 15.1
        self.assertTrue(await second.attempt())
        with self.assertRaises(ValueError):
            first.check()

    async def test_slow_api_response_does_not_extend_authorization(self):
        store, now = Store(), [0.0]
        def slow(method, body=None):
            result = store.request(method, body)
            if method == "PUT":
                now[0] = 9
            return result
        lease = KubernetesLease("lease", "test", request=slow, clock=lambda: now[0])
        with self.assertRaises(ValueError):
            await lease.attempt()
        with self.assertRaises(ValueError):
            lease.check()
