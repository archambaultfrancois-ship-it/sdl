"""Mutually authenticated transport for distributed OSE processes."""
import asyncio
import ipaddress
import json
import ssl
from chain_env.transport.tcp import Client, TCP


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def contexts(ca=None, cert=None, key=None, *, insecure=False, hosts=()):
    if insecure:
        if any((ca, cert, key)) or not hosts or not all(map(loopback, hosts)):
            raise ValueError("insecure mode requires loopback endpoints and no TLS options")
        return None, None
    if not all((ca, cert, key)):
        raise ValueError("distributed execution requires --tls-ca, --tls-cert and --tls-key")
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=ca)
    server.verify_mode = ssl.CERT_REQUIRED
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cafile=ca)
    # OSE identities are exact certificate common names, independent of pod IPs.
    client.check_hostname = False
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    client.load_cert_chain(cert, key)
    return server, client


def identity(writer):
    certificate = writer.get_extra_info("peercert")
    if not certificate:
        return None
    names = [value for group in certificate.get("subject", ()) for key, value in group if key == "commonName"]
    if len(names) != 1:
        raise ValueError("peer certificate requires one OSE identity common name")
    return names[0]


async def connect(host, port, limit, context=None, expected=None, timeout=10):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(
        host, port, ssl=context, server_hostname=None if context is None else host), timeout)
    try:
        if context is not None and identity(writer) != expected:
            raise ValueError("unexpected OSE certificate identity")
        return Client(TCP(reader, writer, limit), request_timeout=timeout)
    except BaseException:
        writer.close()
        await writer.wait_closed()
        raise


async def finish_cleanup(coroutine):
    """A second cancellation must not interrupt child/socket cleanup in finally."""
    task = asyncio.create_task(coroutine)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class RPCServer:
    """Concurrent bounded RPCs: a waiting admission never blocks heartbeats."""
    def __init__(self, limit, handler, context=None, disconnected=None):
        self.limit, self.handler, self.context = limit, handler, context
        self.disconnected = disconnected
        self.connections = set()

    async def start(self, host, port):
        self.server = await asyncio.start_server(self._connection, host, port, ssl=self.context)
        return self.server.sockets[0].getsockname()[1]

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        peer = TCP(reader, writer, self.limit)
        pending = set()

        async def reply(header, body):
            try:
                result, binary = await self.handler(peer, header, body)
                packet = {"op": "result", "request": header["request"]}
                if binary is None:
                    packet["content"] = "json"
                    binary = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
                else:
                    packet.update(result)
                await peer.send(packet, binary)
            except (ValueError, KeyError, TypeError) as error:
                await peer.send({"op": "result", "request": header.get("request"),
                                 "error": str(error), "error_code": "distributed"})
            except (ConnectionError, asyncio.IncompleteReadError):
                pass

        try:
            while True:
                header, body = await peer.receive()
                if type(header.get("request")) is not int:
                    raise ValueError("RPC request identity required")
                if len(pending) >= 128:
                    raise ValueError("too many pending RPCs")
                request = asyncio.create_task(reply(header, body))
                pending.add(request)
                def completed(request):
                    pending.discard(request)
                    if not request.cancelled():
                        # Consume connection-write errors; the owner closes the connection.
                        request.exception()
                request.add_done_callback(completed)
        except (ValueError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            async def cleanup():
                for request in pending:
                    request.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if self.disconnected:
                    self.disconnected(peer)
                try:
                    await peer.close()
                finally:
                    self.connections.discard(task)
            await finish_cleanup(cleanup())

    async def close(self):
        if not hasattr(self, "server"):
            return
        self.server.close()
        for task in list(self.connections):
            task.cancel()
        await asyncio.gather(*self.connections, return_exceptions=True)
        await self.server.wait_closed()
