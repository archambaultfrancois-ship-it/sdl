"""Errors reported by the external client API."""
class ClientError(Exception):
    pass

class UnknownTopic(ClientError):
    pass

class SchemaError(ClientError):
    pass

class QoSError(ClientError):
    pass

class ConnectionLost(ClientError):
    pass

class CallbackFailed(ClientError):
    pass

class PublicationRefused(ClientError):
    pass
