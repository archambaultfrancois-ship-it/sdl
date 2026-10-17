"""Shared limits for dynamic, RAM-only external clients."""
EXTERNAL_LIMITS = dict(enabled=True, max_clients=32, max_subscriptions=256,
                       max_capacity=4096, max_topics=1024, max_catalogues=4096,
                       max_wait_timeout_ms=1000)
