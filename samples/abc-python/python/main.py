import math
import socket
import struct
import sys
import threading

from sdl_runtime import decode, encode
from equation import EquationInput, EquationKind, EquationResult


MAX_FRAME_SIZE = 1024 * 1024


def send_message(stream, message):
   frame = encode(message)
   if not frame or len(frame) > MAX_FRAME_SIZE:
      raise ValueError('SDL frame is too large')
   stream.sendall(struct.pack('>I', len(frame)) + frame)


def read_exact(stream, length):
   chunks = bytearray()
   while len(chunks) < length:
      block = stream.recv(length - len(chunks))
      if not block:
         raise EOFError('connection closed during SDL frame')
      chunks.extend(block)
   return bytes(chunks)


def receive_message(stream, message_type):
   length = struct.unpack('>I', read_exact(stream, 4))[0]
   if length == 0 or length > MAX_FRAME_SIZE:
      raise ValueError('invalid SDL frame length')
   return decode(read_exact(stream, length), message_type)


def input_thread(sock):
   try:
      raw = input('Enter coefficients a, b, c for a*x^2 + b*x + c = 0: ')
      values = [float(item) for item in raw.split()]
      if len(values) != 3 or not all(math.isfinite(item) for item in values):
         raise ValueError('Please enter three finite real numbers.')
      message = EquationInput(a=values[0], b=values[1], c=values[2])
      send_message(sock, message)
   except Exception as error:
      print(str(error), file=sys.stderr)
      raise
   finally:
      sock.close()


def solver_thread(input_sock, result_sock):
   try:
      message = receive_message(input_sock, EquationInput)
      print('Solver received:')
      print(message.display(3))
      result = EquationResult()
      if message.a == 0.0:
         if message.b == 0.0:
            result.kind = (EquationKind.INFINITE_SOLUTIONS if message.c == 0.0
               else EquationKind.NO_SOLUTION)
         else:
            result.kind = EquationKind.ONE_REAL
            result.x1 = -message.c / message.b
      else:
         discriminant = message.b * message.b - 4.0 * message.a * message.c
         if discriminant > 0.0:
            root = math.sqrt(discriminant)
            result.kind = EquationKind.TWO_REAL
            result.x1 = (-message.b - root) / (2.0 * message.a)
            result.x2 = (-message.b + root) / (2.0 * message.a)
         elif discriminant == 0.0:
            result.kind = EquationKind.ONE_REAL
            result.x1 = -message.b / (2.0 * message.a)
         else:
            result.kind = EquationKind.COMPLEX
            result.real_part = -message.b / (2.0 * message.a)
            result.imaginary_part = math.sqrt(-discriminant) / abs(2.0 * message.a)
      print('Solver sending:')
      print(result.display(3))
      send_message(result_sock, result)
   except Exception as error:
      print('Could not process the equation message: {}'.format(error), file=sys.stderr)
      raise
   finally:
      input_sock.close()
      result_sock.close()


def display_thread(sock):
   try:
      result = receive_message(sock, EquationResult)
      if result.kind == EquationKind.TWO_REAL:
         print('Two real roots: x1 = {:.12g}, x2 = {:.12g}'.format(result.x1, result.x2))
      elif result.kind == EquationKind.ONE_REAL:
         print('One real root: x = {:.12g}'.format(result.x1))
      elif result.kind == EquationKind.COMPLEX:
         print('Complex roots: x = {:.12g} +/- {:.12g}i'.format(result.real_part, result.imaginary_part))
      elif result.kind == EquationKind.INFINITE_SOLUTIONS:
         print('Every real number is a solution.')
      elif result.kind == EquationKind.NO_SOLUTION:
         print('There is no solution.')
      else:
         raise ValueError('Received an unknown equation result.')
   except Exception as error:
      print('Could not receive or decode the result message: {}'.format(error), file=sys.stderr)
      raise
   finally:
      sock.close()


def main():
   input_pair = socket.socketpair()
   result_pair = socket.socketpair()
   errors = []

   def run(target, *args):
      try:
         target(*args)
      except Exception as error:
         errors.append(error)

   display = threading.Thread(target=run, args=(display_thread, result_pair[1]), name='display')
   solver = threading.Thread(target=run, args=(solver_thread, input_pair[1], result_pair[0]), name='solver')
   input_worker = threading.Thread(target=run, args=(input_thread, input_pair[0]), name='input')
   display.start()
   solver.start()
   input_worker.start()
   input_worker.join()
   solver.join()
   display.join()
   if errors:
      raise RuntimeError('abc-python pipeline failed') from errors[0]


if __name__ == '__main__':
   main()
