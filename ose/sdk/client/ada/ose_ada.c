/* Lifetime and foreign-thread entry support for the Ada binding. */
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include "ose_client_raw.h"
extern void *__gnat_register_thread(void);
extern void __gnat_unregister_thread(void);
static _Thread_local unsigned ada_depth;
void ose_ada_enter(void) { ++ada_depth; }
void ose_ada_leave(void) { --ada_depth; }
static int enter_foreign(void) {
    if (ada_depth) { ++ada_depth; return 0; }
    if (!__gnat_register_thread()) abort();
    ada_depth = 1; return 1;
}
static void leave_foreign(int foreign) {
    --ada_depth; if (foreign) __gnat_unregister_thread();
}
typedef struct { void *raw; int kind; size_t references; pthread_mutex_t lock; char *failure; } Handle;
void *ose_ada_wrap(void *raw, int kind) {
    Handle *h = calloc(1, sizeof(*h));
    if (!h) {
        ose_ada_enter();
        if (kind) cenv_subscription_free(raw); else cenv_client_free(raw);
        ose_ada_leave();
        return NULL;
    }
    h->raw = raw; h->kind = kind; h->references = 1; pthread_mutex_init(&h->lock, NULL); return h;
}
void *ose_ada_raw(Handle *h) { return h ? h->raw : NULL; }
void ose_ada_hold(Handle *h) { if (h) { pthread_mutex_lock(&h->lock); ++h->references; pthread_mutex_unlock(&h->lock); } }
void ose_ada_release(Handle *h) {
    if (!h) return;
    pthread_mutex_lock(&h->lock); int last = --h->references == 0; pthread_mutex_unlock(&h->lock);
    if (!last) return;
    ose_ada_enter();
    if (h->kind) cenv_subscription_free(h->raw); else cenv_client_free(h->raw);
    ose_ada_leave(); pthread_mutex_destroy(&h->lock); free(h->failure); free(h);
}
void ose_ada_failure(Handle *h, const char *message) {
    pthread_mutex_lock(&h->lock);
    if (!h->failure) { size_t n = strlen(message) + 1; h->failure = malloc(n); if (h->failure) memcpy(h->failure, message, n); }
    pthread_mutex_unlock(&h->lock);
}
/* Returned through the Rust allocator, matching cenv_string_free. */
extern char *cenv_copy_string(const char *);
char *ose_ada_failure_copy(Handle *h) {
    if (!h) return cenv_copy_string("");
    pthread_mutex_lock(&h->lock); char *p = cenv_copy_string(h->failure ? h->failure : ""); pthread_mutex_unlock(&h->lock); return p;
}
typedef struct { CenvHooks ada; } Hooks;
typedef struct { void *value; void (*free_value)(void *); } Value;
static void *decode(void *user, const char *cat, const uint8_t *data, size_t n) {
    Hooks *h = user; Value *v = malloc(sizeof(*v)); if (!v) return NULL;
    int foreign = enter_foreign(); v->value = h->ada.decode(h->ada.user, cat, data, n); v->free_value = h->ada.free_value; leave_foreign(foreign);
    if (!v->value) { free(v); return NULL; } return v;
}
static void free_value(void *pointer) {
    Value *v = pointer; int foreign = enter_foreign(); v->free_value(v->value); leave_foreign(foreign); free(v);
}
static int on_message(void *user, const void *pointer, const CenvMessageInfo *info) {
    Hooks *h = user; const Value *v = pointer; int foreign = enter_foreign();
    int result = h->ada.on_message(h->ada.user, v->value, info); leave_foreign(foreign); return result;
}
static void destroy(void *user) {
    Hooks *h = user; int foreign = enter_foreign(); h->ada.destroy(h->ada.user); leave_foreign(foreign); free(h);
}
CenvSubscription *ose_ada_subscribe(CenvClient *client, const char *topic, const char *root, const char *cat, const char *policy, CenvHooks hooks) {
    Hooks *h = malloc(sizeof(*h));
    if (!h) { hooks.destroy(hooks.user); cenv_set_error("internal", "allocation failed"); return NULL; }
    h->ada = hooks; CenvHooks outer = {h, decode, free_value, on_message, destroy};
    ose_ada_enter(); CenvSubscription *s = cenv_subscribe_raw(client, topic, root, cat, policy, outer); ose_ada_leave(); return s;
}
