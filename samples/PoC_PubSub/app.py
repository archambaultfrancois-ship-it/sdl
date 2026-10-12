"""CLI for the TCP pub/sub proof of concept."""
import argparse
import threading
import time

from contracts import DEFAULT_CONTRACT, load_contract
from pubsub import Broker, BrokerState, Client, PocError, Publisher, Subscriber
from temperature_v1 import Temperature as V1
from temperature_v2 import Temperature as V2
from temperature_bad import Temperature as Bad


def demo(contract=None):
    contract = load_contract() if contract is None else contract
    topic = contract.topic
    with Broker(('127.0.0.1', 0), BrokerState(contract)) as broker:
        worker = threading.Thread(target=broker.serve_forever, daemon=True)
        worker.start()
        try:
            client = Client(broker.server_address)
            print('TCP broker: %s:%s' % broker.server_address)
            print('Topic: ' + topic)
            old = Publisher(client, V1, 'sensor-v1', topic)
            new = Publisher(client, V2, 'sensor-v2', topic)
            print('Catalogue v1: ' + old.identity)
            print('Catalogue v2: ' + new.identity)
            old.publish(V1(sensor='oven-A', celsius=21.5))
            new.publish(V2(sensor='oven-B', value=23.0, unit='C'))
            print('\nV1 subscriber created AFTER publication:')
            subscriber = Subscriber(client, [V1], topic)
            for event, value in subscriber.poll():
                print('  #%s %s → %r' % (event['sequence'], event['producer'], value))
            assert subscriber.catalogue_fetches == 2
            new.publish(V2(sensor='oven-B', value=24.0, unit='C'))
            subscriber.poll()
            assert subscriber.catalogue_fetches == 2
            print('  Cached contexts: 2; no additional catalogue fetch for the next message.')
            print('\nDynamic subscriber without local classes:')
            dynamic = Subscriber(client, topic=topic)
            for event, value in dynamic.poll():
                print('  #%s → %r' % (event['sequence'], value.fields))
            print('\nAttempt to publish with an incompatible schema:')
            try:
                Publisher(client, Bad, 'incompatible-sensor', topic)
            except PocError as error:
                assert error.code == 'INCOMPATIBLE_SCHEMA'
                print('  Rejected: %s — %s' % (error.code, error))
            else:
                raise AssertionError('incompatible schema accepted')
            print('\nPoC passed: TCP, two publishers, late subscriber, caching, and schema evolution.')
        finally:
            broker.shutdown()
            worker.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('demo', 'broker', 'publish', 'subscribe'))
    parser.add_argument('--contract', default=str(DEFAULT_CONTRACT), help='Topic contract manifest')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--revision', choices=('v1', 'v2', 'bad'), default='v1')
    parser.add_argument('--sensor', default='oven-A')
    parser.add_argument('--value', type=float, default=21.5)
    parser.add_argument('--producer', default='sensor')
    parser.add_argument('--dynamic', action='store_true')
    parser.add_argument('--once', action='store_true', help='Read available history and exit')
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
    except PocError as error:
        parser.exit(2, '%s: %s\n' % (error.code, error))
    if args.action == 'demo':
        demo(contract)
        return
    if args.action == 'broker':
        with Broker((args.host, args.port), BrokerState(contract)) as broker:
            print('TCP broker: %s:%s ; topic %s' % (*broker.server_address, contract.topic), flush=True)
            try:
                broker.serve_forever()
            except KeyboardInterrupt:
                pass
        return
    client = Client((args.host, args.port))
    try:
        if args.action == 'publish':
            message_type = {'v1': V1, 'v2': V2, 'bad': Bad}[args.revision]
            publisher = Publisher(client, message_type, args.producer, contract.topic)
            if args.revision == 'v2':
                message = V2(sensor=args.sensor, value=args.value, unit='C')
            elif args.revision == 'bad':
                message = Bad(sensor=args.sensor, celsius=str(args.value))
            else:
                message = V1(sensor=args.sensor, celsius=args.value)
            print('Published #%s; catalogue %s' % (publisher.publish(message), publisher.identity))
        else:
            subscriber = Subscriber(client, () if args.dynamic else [V1], contract.topic)
            while True:
                for event, value in subscriber.poll():
                    print('#%s %s → %r' % (event['sequence'], event['producer'], value), flush=True)
                if args.once:
                    break
                time.sleep(0.25)
    except PocError as error:
        parser.exit(1, '%s: %s\n' % (error.code, error))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
