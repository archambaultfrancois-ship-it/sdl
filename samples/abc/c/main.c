#define _POSIX_C_SOURCE 200112L

#include "abc.h"
#include "sdl_registry.h"
#include "type_engine.h"

#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define MAX_FRAME_SIZE (1024U * 1024U)

typedef struct {
   int input_socket;
   int result_socket;
} ThreadSockets;

static char thread_failure;

static int write_all(int socket_fd, const void *buffer, size_t size) {
   const uint8_t *bytes = (const uint8_t *)buffer;
   size_t offset = 0;
   while (offset < size) {
      ssize_t written = write(socket_fd, bytes + offset, size - offset);
      if (written < 0 && errno == EINTR)
         continue;
      if (written <= 0)
         return 0;
      offset += (size_t)written;
   }
   return 1;
}

static int read_all(int socket_fd, void *buffer, size_t size) {
   uint8_t *bytes = (uint8_t *)buffer;
   size_t offset = 0;
   while (offset < size) {
      ssize_t received = read(socket_fd, bytes + offset, size - offset);
      if (received < 0 && errno == EINTR)
         continue;
      if (received <= 0)
         return 0;
      offset += (size_t)received;
   }
   return 1;
}

/* Prefix the SDL frame so the stream socket reader knows its boundary. */
static int send_message(int socket_fd, const char *type, const void *message) {
   uint8_t *frame;
   uint8_t length_header[4];
   size_t frame_size = 0;
   int success;
   frame = (uint8_t *)type_encode(type, message, &frame_size);
   if (frame == NULL || frame_size == 0 || frame_size > MAX_FRAME_SIZE ||
       frame_size > UINT32_MAX) {
      type_free(frame);
      return 0;
   }
   length_header[0] = (uint8_t)(frame_size >> 24);
   length_header[1] = (uint8_t)(frame_size >> 16);
   length_header[2] = (uint8_t)(frame_size >> 8);
   length_header[3] = (uint8_t)frame_size;
   success = write_all(socket_fd, length_header, sizeof(length_header)) &&
      write_all(socket_fd, frame, frame_size);
   type_free(frame);
   return success;
}

static void *receive_message(int socket_fd, size_t *frame_size) {
   uint8_t length_header[4];
   uint32_t length;
   uint8_t *frame;
   void *message;
   if (!read_all(socket_fd, length_header, sizeof(length_header)))
      return NULL;
   length = ((uint32_t)length_header[0] << 24) |
      ((uint32_t)length_header[1] << 16) |
      ((uint32_t)length_header[2] << 8) | (uint32_t)length_header[3];
   if (length == 0 || length > MAX_FRAME_SIZE)
      return NULL;
   frame = (uint8_t *)malloc(length);
   if (frame == NULL)
      return NULL;
   if (!read_all(socket_fd, frame, length)) {
      free(frame);
      return NULL;
   }
   *frame_size = length;
   message = type_decode(frame, frame_size);
   free(frame);
   return message;
}

static int display_sdl_message(const char *label, const char *type,
   const void *message) {
   char *rendered = type_display(type, message, 3);
   if (rendered == NULL) {
      fprintf(stderr, "Could not display SDL message of type %s.\n", type);
      return 0;
   }
   printf("%s:\n", label);
   fputs(rendered, stdout);
   type_free(rendered);
   return 1;
}

static void *input_thread(void *argument) {
   ThreadSockets *sockets = (ThreadSockets *)argument;
   EquationInput input;
   int scanned;
   memset(&input, 0, sizeof(input));
   printf("Enter coefficients a, b, c for a*x^2 + b*x + c = 0: ");
   fflush(stdout);
   scanned = scanf("%lf %lf %lf", &input.a, &input.b, &input.c);
   if (scanned != 3 || !isfinite(input.a) || !isfinite(input.b) ||
       !isfinite(input.c)) {
      fprintf(stderr, "Please enter three finite real numbers.\n");
      close(sockets->input_socket);
      return &thread_failure;
   }
   if (!send_message(sockets->input_socket, "EquationInput", &input)) {
      fprintf(stderr, "Could not send the coefficient message.\n");
      close(sockets->input_socket);
      return &thread_failure;
   }
   close(sockets->input_socket);
   return NULL;
}

/* The solver reads one input message and sends one result message. */
static void *solver_thread(void *argument) {
   ThreadSockets *sockets = (ThreadSockets *)argument;
   EquationInput *input;
   EquationResult result;
   size_t frame_size = 0;
   double discriminant;
   input = (EquationInput *)receive_message(sockets->input_socket, &frame_size);
   close(sockets->input_socket);
   if (input == NULL) {
      fprintf(stderr, "Could not receive or decode the coefficient message.\n");
      close(sockets->result_socket);
      return &thread_failure;
   }
   if (!display_sdl_message("Solver received", "EquationInput", input)) {
      type_free(input);
      close(sockets->result_socket);
      return &thread_failure;
   }
   memset(&result, 0, sizeof(result));
   if (input->a == 0.0) {
      if (input->b == 0.0)
         result.kind = input->c == 0.0 ? EQUATIONKIND_INFINITE_SOLUTIONS :
            EQUATIONKIND_NO_SOLUTION;
      else {
         result.kind = EQUATIONKIND_ONE_REAL;
         result.has_x1 = true;
         result.x1 = -input->c / input->b;
      }
   } else {
      /* The discriminant selects two real, one real, or complex roots. */
      discriminant = input->b * input->b - 4.0 * input->a * input->c;
      if (discriminant > 0.0) {
         double root = sqrt(discriminant);
         result.kind = EQUATIONKIND_TWO_REAL;
         result.has_x1 = true;
         result.has_x2 = true;
         result.x1 = (-input->b - root) / (2.0 * input->a);
         result.x2 = (-input->b + root) / (2.0 * input->a);
      } else if (discriminant == 0.0) {
         result.kind = EQUATIONKIND_ONE_REAL;
         result.has_x1 = true;
         result.x1 = -input->b / (2.0 * input->a);
      } else {
         result.kind = EQUATIONKIND_COMPLEX;
         result.has_real_part = true;
         result.has_imaginary_part = true;
         result.real_part = -input->b / (2.0 * input->a);
         result.imaginary_part = sqrt(-discriminant) / fabs(2.0 * input->a);
      }
   }
   type_free(input);
   if (!display_sdl_message("Solver sending", "EquationResult", &result)) {
      close(sockets->result_socket);
      return &thread_failure;
   }
   if (!send_message(sockets->result_socket, "EquationResult", &result)) {
      fprintf(stderr, "Could not send the equation result message.\n");
      close(sockets->result_socket);
      return &thread_failure;
   }
   close(sockets->result_socket);
   return NULL;
}

static void *display_thread(void *argument) {
   ThreadSockets *sockets = (ThreadSockets *)argument;
   EquationResult *result;
   size_t frame_size = 0;
   result = (EquationResult *)receive_message(sockets->result_socket,
      &frame_size);
   close(sockets->result_socket);
   if (result == NULL) {
      fprintf(stderr, "Could not receive or decode the result message.\n");
      return &thread_failure;
   }
   switch (result->kind) {
      case EQUATIONKIND_TWO_REAL:
         printf("Two real roots: x1 = %.12g, x2 = %.12g\n",
            result->x1, result->x2);
         break;
      case EQUATIONKIND_ONE_REAL:
         printf("One real root: x = %.12g\n", result->x1);
         break;
      case EQUATIONKIND_COMPLEX:
         printf("Complex roots: x = %.12g +/- %.12gi\n",
            result->real_part, result->imaginary_part);
         break;
      case EQUATIONKIND_INFINITE_SOLUTIONS:
         puts("Every real number is a solution.");
         break;
      case EQUATIONKIND_NO_SOLUTION:
         puts("There is no solution.");
         break;
      default:
         fprintf(stderr, "Received an unknown equation result.\n");
         type_free(result);
         return &thread_failure;
   }
   type_free(result);
   return NULL;
}

int main(void) {
   int input_pair[2];
   int result_pair[2];
   pthread_t input_id;
   pthread_t solver_id;
   pthread_t display_id;
   ThreadSockets input_sockets;
   ThreadSockets solver_sockets;
   ThreadSockets display_sockets;
   void *input_status = NULL;
   void *solver_status = NULL;
   void *display_status = NULL;
   int input_started = 0;
   int solver_started = 0;
   int display_started = 0;

   register_all_types();
   /* Two local socket pairs form the input -> solver -> display pipeline. */
   if (socketpair(AF_UNIX, SOCK_STREAM, 0, input_pair) != 0) {
      perror("socketpair");
      return 1;
   }
   if (socketpair(AF_UNIX, SOCK_STREAM, 0, result_pair) != 0) {
      perror("socketpair");
      close(input_pair[0]);
      close(input_pair[1]);
      return 1;
   }
   input_sockets.input_socket = input_pair[0];
   input_sockets.result_socket = -1;
   solver_sockets.input_socket = input_pair[1];
   solver_sockets.result_socket = result_pair[0];
   display_sockets.input_socket = -1;
   display_sockets.result_socket = result_pair[1];

   if (pthread_create(&display_id, NULL, display_thread, &display_sockets) != 0)
      goto thread_error;
   display_started = 1;
   if (pthread_create(&solver_id, NULL, solver_thread, &solver_sockets) != 0)
      goto thread_error;
   solver_started = 1;
   if (pthread_create(&input_id, NULL, input_thread, &input_sockets) != 0)
      goto thread_error;
   input_started = 1;

   pthread_join(input_id, &input_status);
   pthread_join(solver_id, &solver_status);
   pthread_join(display_id, &display_status);
   return input_status == NULL && solver_status == NULL &&
      display_status == NULL ? 0 : 1;

thread_error:
   fprintf(stderr, "Could not create worker thread.\n");
   shutdown(input_pair[0], SHUT_RDWR);
   shutdown(input_pair[1], SHUT_RDWR);
   shutdown(result_pair[0], SHUT_RDWR);
   shutdown(result_pair[1], SHUT_RDWR);
   close(input_pair[0]);
   close(input_pair[1]);
   close(result_pair[0]);
   close(result_pair[1]);
   if (input_started) pthread_join(input_id, NULL);
   if (solver_started) pthread_join(solver_id, NULL);
   if (display_started) pthread_join(display_id, NULL);
   return 1;
}
