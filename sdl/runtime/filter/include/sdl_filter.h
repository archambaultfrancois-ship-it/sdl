#ifndef SDL_FILTER_H
#define SDL_FILTER_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct SdlFilter SdlFilter;
typedef struct { const uint8_t *data; size_t size; } SdlFilterBytes;
typedef struct { uint32_t kind, id; size_t index; SdlFilterBytes owner, name; } SdlFilterStep;
/* Virtual complex components use owner "$complex", id 1 (real) or 2 (imag). */
/* Missing=0, Bool=1, Signed=2, Unsigned=3, Float=4, UTF-8=5,
 * Array length=6, Message=7, Complex=8. Borrowed strings live for the evaluation. */
typedef struct {
    uint32_t kind, reserved;
    int64_t signed_value;
    uint64_t unsigned_value;
    double real, imag;
    SdlFilterBytes bytes;
    size_t count;
} SdlFilterValue;
typedef int (*SdlFilterRead)(void *, const SdlFilterStep *, size_t, uint32_t slot, SdlFilterValue *);
/* Optional strided column; wildcard index SIZE_MAX selects the collection.
 * kind=2/3/4, width=1/2/4/8; flags: 1 big endian, 2 quantum, 4 fl32 quantum. */
typedef struct {
    const uint8_t *data;
    size_t count, stride;
    uint32_t kind, width, flags, reserved;
    double quantum;
} SdlFilterSpan;
typedef int (*SdlFilterReadSpan)(void *, const SdlFilterStep *, size_t, uint32_t slot, SdlFilterSpan *);
/* Results: Match=1, NoMatch=0, Indeterminate=2, Error=-1. */
uint32_t sdl_filter_abi_version(void);
const char *sdl_filter_error(void);
/* Adapter validation failure: sets this thread's diagnostic and returns Error. */
int sdl_filter_failure(SdlFilterBytes diagnostic);
SdlFilter *sdl_filter_prepare(SdlFilterBytes catalogue, SdlFilterBytes root, SdlFilterBytes expression);
void sdl_filter_free(SdlFilter *);
SdlFilterBytes sdl_filter_root(const SdlFilter *);
int sdl_filter_evaluate_wire(const SdlFilter *, SdlFilterBytes);
int sdl_filter_evaluate_object(const SdlFilter *, SdlFilterRead, void *);
int sdl_filter_evaluate_object_spans(const SdlFilter *, SdlFilterRead, SdlFilterReadSpan, void *);
#ifdef __cplusplus
}
#endif
#endif
