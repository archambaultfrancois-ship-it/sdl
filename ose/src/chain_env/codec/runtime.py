"""Runtime-only SDL operations; no model, generator or checkout is required."""
import importlib


class Runtime:
    def __init__(self, api=None):
        self.api = api or importlib.import_module("sdl_runtime")

    def identity(self, text):
        return self.api.catalogue_id(text)

    def compare(self, publisher, consumer, root):
        report = self.api.compare_catalogues(publisher, consumer, root=root, require_required=True)
        if not report.compatible:
            raise ValueError(f"SDL2 {report.code} at {report.path}")

    def validate(self, text, root, envelope):
        identity, payload = self.api.unpack_envelope(envelope)
        if identity != self.identity(text):
            raise ValueError("catalogue identity mismatch")
        value = self.api.decode_dynamic(self.api.prepare(text), payload)
        if value.type_name != root:
            raise ValueError("unexpected root message")
        return value

    def wrap(self, text, payload):
        return self.api.pack_envelope(self.identity(text), payload)


class TypedCodec:
    def __init__(self, message_type):
        self.runtime = Runtime()
        self.type = message_type
        self.root = self.runtime.api.message_name(message_type)
        self.catalogue = self.runtime.api.description([message_type])
        self.encoder = self.runtime.api.prepare(self.catalogue, [message_type])
        self.decoder = None

    def encode(self, message):
        if not isinstance(message, self.type):
            raise TypeError(f"expected {self.type.__name__}")
        return self.runtime.wrap(self.catalogue, self.runtime.api.encode(self.encoder, message))

    def decode(self, catalogue, envelope):
        identity, payload = self.runtime.api.unpack_envelope(envelope)
        if identity != self.runtime.identity(catalogue):
            raise ValueError("catalogue identity mismatch")
        if self.decoder is None or self.decoder[0] != catalogue:
            self.runtime.compare(catalogue, self.catalogue, self.root)
            self.decoder = (catalogue, self.runtime.api.prepare(catalogue, [self.type]))
        value = self.runtime.api.decode(self.decoder[1], payload)
        if not isinstance(value, self.type):
            raise ValueError("unexpected root message")
        return value
