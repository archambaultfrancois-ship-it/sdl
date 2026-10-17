import unittest
from unittest.mock import patch
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.delivery.buffers import Buffer
from ose.delivery.engine import DeliveryEngine
from ose.delivery.stores import MemoryStore


def record(n, epoch="session"):
    return {"id": f"{epoch}:{n}", "epoch": epoch, "sequence": n, "body": b"wire"}


class DeliveryTests(unittest.TestCase):
    def test_fifo_is_arrival_order_and_deduplicates(self):
        buffer = Buffer(Policy(), MemoryStore())
        for n in (3, 1, 2):
            self.assertEqual(buffer.admit(record(n)), "accepted")
        self.assertEqual(buffer.admit(record(1)), "duplicate")
        received = []
        while buffer.take():
            item = buffer.take()
            received.append(item["sequence"])
            buffer.consumed(item["id"])
        self.assertEqual(received, [3, 1, 2])
        self.assertEqual(buffer.admit(record(1)), "duplicate")

    def test_latest_never_rolls_back_after_consumption(self):
        buffer = Buffer(Policy.resolve({"buffer": "latest"}), MemoryStore())
        buffer.admit(record(5))
        buffer.admit(record(6))
        self.assertEqual(buffer.take()["sequence"], 6)
        buffer.consumed("session:6")
        self.assertEqual(buffer.admit(record(4)), "stale")
        self.assertIsNone(buffer.take())

    def test_saturation_is_explicit(self):
        for overflow, expected in (("reject_new", [1]), ("drop_oldest", [2]), ("wait", [1])):
            buffer = Buffer(Policy(capacity=1, overflow=overflow), MemoryStore())
            buffer.admit(record(1))
            result = buffer.admit(record(2))
            self.assertEqual(result, "accepted" if overflow == "drop_oldest" else "full")
            self.assertEqual([r["sequence"] for r in buffer.store.records()], expected)

    def test_drop_oldest_preserves_inflight_snapshot(self):
        buffer = Buffer(Policy(capacity=2, overflow="drop_oldest"), MemoryStore())
        buffer.admit(record(1)); buffer.admit(record(2))
        buffer.reserved.add("session:1")
        buffer.admit(record(3))
        self.assertEqual([r["sequence"] for r in buffer.store.records()], [1, 3])
        buffer.reserved.add("session:3")
        self.assertEqual(buffer.admit(record(4)), "full")

    def test_retry_deadlines_ack_and_exhaustion(self):
        now = [0.0]
        engine = DeliveryEngine(Policy.resolve({"ack": "received", "retry": {"max_retries": 2}}), MemoryStore(), lambda: now[0])
        engine.enqueue(record(1))
        self.assertEqual(len(list(engine.due())), 1)
        engine.sent("session:1")
        now[0] = .099
        self.assertEqual(list(engine.due()), [])
        now[0] = .1
        engine.sent(list(engine.due())[0]["id"])
        now[0] = .301
        engine.sent(list(engine.due())[0]["id"])
        now[0] = .702
        self.assertEqual(list(engine.due()), [])
        self.assertEqual(engine.metrics["retries"], 2)
        self.assertEqual(engine.metrics["failed"], 1)
        engine.enqueue(record(2))
        engine.sent("session:2")
        engine.acknowledge("session:2")
        self.assertEqual(engine.store.records(), [])

    def test_no_retry_does_not_resend_and_latest_cancels_old(self):
        engine = DeliveryEngine(Policy(), MemoryStore())
        engine.enqueue(record(1))
        engine.sent("session:1")
        self.assertEqual(list(engine.due()), [])
        engine = DeliveryEngine(Policy.resolve({"buffer": "latest", "ack": "received", "retry": {}}), MemoryStore())
        engine.enqueue(record(1)); engine.sent("session:1")
        engine.enqueue(record(2))
        self.assertEqual([r["sequence"] for r in engine.store.records()], [2])





    def test_invalid_profiles(self):
        cases = [({"retry": {}}, "tcp"), ({"durability": "disk", "buffer": "latest"}, "tcp"),
                 ({"overflow": "wait"}, "udp"), ({"capacity": True}, "tcp"), ({"buffer": "unknown"}, "tcp")]
        for qos, transport in cases:
            with self.subTest(qos=qos), self.assertRaises(ValueError):
                Policy.resolve(qos, transport)

    def test_disk_profiles_are_rejected_and_memory_profiles_normalized(self):
        with self.assertRaisesRegex(ValueError, "RAM-only"):
            Policy.resolve({"durability": "disk"})
        with self.assertRaisesRegex(ValueError, "RAM-only"):
            Policy.from_dict({**Policy().to_dict(), "durability": "disk"})
        self.assertNotIn("durability", Policy.resolve({"durability": "memory"}).to_dict())
        self.assertEqual(Policy.from_dict({**Policy().to_dict(), "durability": "memory"}), Policy())

    def test_ram_sender_drains_without_queue_scans_or_payload_conversion(self):
        payload = b"\x00" * 8192
        engine = DeliveryEngine(Policy(ack="received"), MemoryStore())
        with patch.object(engine.store, "records", side_effect=AssertionError("unexpected full scan")):
            for number in range(1000):
                engine.enqueue(dict(record(number), body=payload))
            self.assertIs(engine.store.get("session:999")["body"], payload)
            while engine.store.size():
                for pending in list(engine.due(limit=32)):
                    engine.sent(pending["id"])
                    engine.acknowledge(pending["id"])
        self.assertEqual(engine.metrics["acknowledged"], 1000)

    def test_capacity_deduplication_and_timer_memory_are_bounded(self):
        store = MemoryStore(capacity=2, seen_limit=4)
        store.put(dict(record(1), deadline=1))
        store.put(dict(record(2), deadline=2))
        with self.assertRaisesRegex(ValueError, "capacity"):
            store.put(record(3))
        for number in range(2000):
            store.update(dict(store.get("session:1"), deadline=number + 10))
            store.remember(str(number))
        self.assertLessEqual(len(store._deadlines), 64)
        self.assertEqual(len(store.seen), 4)
        self.assertEqual([r["id"] for r in store.due(3)], ["session:2"])
        store.remove("session:1")
        self.assertEqual([r["id"] for r in store.due(10000)], ["session:2"])
        store.close()
        self.assertEqual(store.size(), 0)
        self.assertFalse(store.contains("1999"))
        self.assertIsNone(store.next_deadline())

    def test_new_messages_do_not_starve_expired_retries(self):
        now = [0.0]
        engine = DeliveryEngine(Policy.resolve({"ack": "received", "retry": {}}), MemoryStore(), lambda: now[0])
        engine.enqueue(record(1)); engine.sent("session:1")
        now[0] = .11
        engine.enqueue(record(2))
        self.assertEqual([r["id"] for r in engine.due()], ["session:1", "session:2"])

    def test_fresh_ram_instance_has_no_pending_messages_or_dedup_history(self):
        first = Buffer(Policy(), MemoryStore())
        first.admit(record(1))
        first.store.close()
        fresh = Buffer(Policy(), MemoryStore())
        self.assertIsNone(fresh.take())
        self.assertEqual(fresh.admit(record(1)), "accepted")
