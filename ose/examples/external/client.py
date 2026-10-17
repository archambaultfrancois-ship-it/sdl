"""Run alongside either native example and the application's broker."""
import argparse
import threading
from ose.client import Client, Latest
from measurement import Measurement

parser = argparse.ArgumentParser()
parser.add_argument('--port',type=int,default=7000)
parser.add_argument('--language',choices=('c','rust'),default='rust')
args = parser.parse_args()
with Client.connect(port=args.port,client_id='external-python') as client:
    publisher=client.publisher('external.input',Measurement)
    print('Start the native client now and wait for its READY message.',flush=True)
    input('Press Enter to continue: ')
    done=threading.Event()
    def receive(message,info):
        print(info.publication_id,message.value,flush=True)
        done.set()
    sub=client.subscribe(f'{args.language}.result',Measurement,receive,qos=Latest())
    print(publisher.publish(Measurement(sequence=1,value=3.5)))
    if not done.wait(5):
        raise TimeoutError('no result received')
