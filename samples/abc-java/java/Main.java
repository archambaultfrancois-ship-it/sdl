import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.channels.Channels;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Scanner;

public final class Main {
   private static final int MAX_FRAME_SIZE = 1024 * 1024;

   private static void send(DataOutputStream out, byte[] frame) throws IOException {
      if (frame.length == 0 || frame.length > MAX_FRAME_SIZE) throw new IOException("SDL frame is too large");
      out.writeInt(frame.length);
      out.write(frame);
      out.flush();
   }

   private static byte[] receive(DataInputStream in) throws IOException {
      int length = in.readInt();
      if (length <= 0 || length > MAX_FRAME_SIZE) throw new IOException("invalid SDL frame length");
      byte[] frame = new byte[length];
      in.readFully(frame);
      return frame;
   }

   public static void main(String[] args) throws Exception {
      final UnixPair inputPair = UnixPair.create();
      final UnixPair resultPair = UnixPair.create();
      final Throwable[] failure = new Throwable[3];

      Thread display = new Thread(new Runnable() {
         public void run() {
            try {
               abc.EquationResult result = abc.EquationResult.decode(receive(new DataInputStream(resultPair.rightIn)));
               switch (result.kind) {
                  case TWO_REAL:
                     System.out.printf("Two real roots: x1 = %.12g, x2 = %.12g%n", result.x1, result.x2); break;
                  case ONE_REAL:
                     System.out.printf("One real root: x = %.12g%n", result.x1); break;
                  case COMPLEX:
                     System.out.printf("Complex roots: x = %.12g +/- %.12gi%n", result.real_part, result.imaginary_part); break;
                  case INFINITE_SOLUTIONS:
                     System.out.println("Every real number is a solution."); break;
                  case NO_SOLUTION:
                     System.out.println("There is no solution."); break;
                  default: throw new IOException("Received an unknown equation result.");
               }
            } catch (Exception e) { failure[2] = e; }
         }
      }, "display");

      Thread solver = new Thread(new Runnable() {
         public void run() {
            try {
               abc.EquationInput input = abc.EquationInput.decode(receive(new DataInputStream(inputPair.rightIn)));
               System.out.println("Solver received:\n" + input);
               abc.EquationResult result = new abc.EquationResult();
               if (input.a == 0.0) {
                  if (input.b == 0.0) result.kind = input.c == 0.0 ? abc.EquationKind.INFINITE_SOLUTIONS : abc.EquationKind.NO_SOLUTION;
                  else { result.kind = abc.EquationKind.ONE_REAL; result.x1 = Double.valueOf(-input.c / input.b); }
               } else {
                  double d = input.b * input.b - 4.0 * input.a * input.c;
                  if (d > 0.0) {
                     double root = Math.sqrt(d); result.kind = abc.EquationKind.TWO_REAL;
                     result.x1 = Double.valueOf((-input.b - root) / (2.0 * input.a));
                     result.x2 = Double.valueOf((-input.b + root) / (2.0 * input.a));
                  } else if (d == 0.0) {
                     result.kind = abc.EquationKind.ONE_REAL; result.x1 = Double.valueOf(-input.b / (2.0 * input.a));
                  } else {
                     result.kind = abc.EquationKind.COMPLEX;
                     result.real_part = Double.valueOf(-input.b / (2.0 * input.a));
                     result.imaginary_part = Double.valueOf(Math.sqrt(-d) / Math.abs(2.0 * input.a));
                  }
               }
               System.out.println("Solver sending:\n" + result);
               send(new DataOutputStream(resultPair.leftOut), result.encode());
               resultPair.left.close();
            } catch (Exception e) { failure[1] = e; }
         }
      }, "solver");

      Thread input = new Thread(new Runnable() {
         public void run() {
            try {
               Scanner scanner = new Scanner(System.in);
               System.out.print("Enter coefficients a, b, c for a*x^2 + b*x + c = 0: ");
               System.out.flush();
               double a = scanner.nextDouble(), b = scanner.nextDouble(), c = scanner.nextDouble();
               if (!Double.isFinite(a) || !Double.isFinite(b) || !Double.isFinite(c)) throw new IOException("Please enter three finite real numbers.");
               abc.EquationInput message = new abc.EquationInput();
               message.a = a; message.b = b; message.c = c;
               send(new DataOutputStream(inputPair.leftOut), message.encode());
               inputPair.left.close();
            } catch (Exception e) { failure[0] = e; }
         }
      }, "input");

      display.start(); solver.start(); input.start();
      input.join(); solver.join(); display.join();
      for (Throwable error : failure) if (error != null) throw new Exception(error);
   }

   private static final class UnixPair {
      final SocketChannel left;
      final SocketChannel right;
      final OutputStream leftOut;
      final InputStream rightIn;
      private UnixPair(SocketChannel left, SocketChannel right) throws IOException {
         this.left = left; this.right = right;
         this.leftOut = Channels.newOutputStream(left);
         this.rightIn = Channels.newInputStream(right);
      }

      static UnixPair create() throws IOException {
         Path path = Files.createTempFile("sdl-abc-", ".sock");
         Files.delete(path);
         UnixDomainSocketAddress address = UnixDomainSocketAddress.of(path);
         try (ServerSocketChannel server = ServerSocketChannel.open(StandardProtocolFamily.UNIX)) {
            server.bind(address);
            SocketChannel left = SocketChannel.open(StandardProtocolFamily.UNIX);
            left.connect(address);
            SocketChannel right = server.accept();
            Files.deleteIfExists(path);
            return new UnixPair(left, right);
         } finally {
            Files.deleteIfExists(path);
         }
      }
   }
}
