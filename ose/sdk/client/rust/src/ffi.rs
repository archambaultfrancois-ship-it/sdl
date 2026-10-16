//! Stable C ABI. All returned strings are owned; callback values are borrowed.
use super::*;
use std::{
    cell::RefCell,
    ffi::{c_char, c_int, c_void, CStr, CString},
};
thread_local! {static ERROR:RefCell<CString>=RefCell::new(CString::new("").unwrap());}
fn cstring(s: impl AsRef<str>) -> CString {
    CString::new(s.as_ref().replace('\0', "\\0")).unwrap()
}
fn guard<T: Default>(f: impl FnOnce() -> Result<T>) -> T {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)) {
        Ok(Ok(v)) => v,
        other => {
            let msg = match other {
                Ok(Err(e)) => e.to_string(),
                _ => "internal: SDK panic".into(),
            };
            ERROR.with(|e| *e.borrow_mut() = cstring(msg));
            T::default()
        }
    }
}
unsafe fn string<'a>(p: *const c_char) -> Result<&'a str> {
    if p.is_null() {
        return Err(Error::new("argument", "NULL string"));
    }
    CStr::from_ptr(p)
        .to_str()
        .map_err(|_| Error::new("argument", "UTF8 string"))
}
unsafe fn bytes<'a>(p: *const u8, n: usize) -> Result<&'a [u8]> {
    if n == 0 {
        return Ok(&[]);
    }
    if p.is_null() || n > isize::MAX as usize {
        return Err(Error::new("argument", "invalid bytes"));
    }
    Ok(std::slice::from_raw_parts(p, n))
}
#[no_mangle]
pub extern "C" fn cenv_last_error() -> *const c_char {
    ERROR.with(|e| e.borrow().as_ptr())
}
#[no_mangle]
pub unsafe extern "C" fn cenv_set_error(code: *const c_char, message: *const c_char) {
    guard(|| {
        let message = format!("{}: {}", string(code)?, string(message)?);
        ERROR.with(|e| *e.borrow_mut() = cstring(message));
        Ok(1)
    });
}
/// Copy UTF-8 into an owned ABI string for language bridges.
#[no_mangle]
pub unsafe extern "C" fn cenv_copy_string(p: *const c_char) -> *mut c_char {
    guard(|| Ok(cstring(string(p)?).into_raw()))
}
#[no_mangle]
pub unsafe extern "C" fn cenv_string_free(p: *mut c_char) {
    if !p.is_null() {
        drop(CString::from_raw(p));
    }
}
#[no_mangle]
pub unsafe extern "C" fn cenv_connect(
    host: *const c_char,
    port: u16,
    id: *const c_char,
) -> *mut Client {
    guard(|| {
        Ok(Box::into_raw(Box::new(Client::connect(
            string(host)?,
            port,
            string(id)?,
        )?)))
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_close(p: *mut Client) {
    if let Some(c) = p.as_ref() {
        c.close();
    }
}
#[no_mangle]
pub unsafe extern "C" fn cenv_client_free(p: *mut Client) {
    if !p.is_null() {
        drop(Box::from_raw(p));
    }
}
#[no_mangle]
pub unsafe extern "C" fn cenv_run(p: *mut Client) -> c_int {
    guard(|| {
        p.as_ref()
            .ok_or_else(|| Error::new("argument", "NULL client"))?
            .run()?;
        Ok(1)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_list_topics(p: *mut Client) -> *mut c_char {
    guard(|| {
        Ok(cstring(
            p.as_ref()
                .ok_or_else(|| Error::new("argument", "NULL client"))?
                .list_topics()?
                .stringify(),
        )
        .into_raw())
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_declare(
    p: *mut Client,
    topic: *const c_char,
    root: *const c_char,
    cat: *const c_char,
) -> c_int {
    guard(|| {
        p.as_ref()
            .ok_or_else(|| Error::new("argument", "NULL client"))?
            .declare(string(topic)?, string(root)?, string(cat)?)?;
        Ok(1)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_release_publisher(p: *mut Client, topic: *const c_char) -> c_int {
    guard(|| {
        p.as_ref()
            .ok_or_else(|| Error::new("argument", "NULL client"))?
            .inner
            .rpc(
                "release_publisher",
                object([("topic", string(topic)?.into())]),
                &[],
            )?;
        Ok(1)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_publish_envelope(
    p: *mut Client,
    topic: *const c_char,
    data: *const u8,
    size: usize,
) -> *mut c_char {
    guard(|| {
        let r = p
            .as_ref()
            .ok_or_else(|| Error::new("argument", "NULL client"))?
            .publish_envelope(string(topic)?, bytes(data, size)?)?;
        Ok(cstring(
            object([
                ("publication_id", r.publication_id.into()),
                ("epoch", r.epoch.into()),
                ("sequence", r.sequence.into()),
                (
                    "delivery",
                    object(r.delivery.into_iter().map(|(k, v)| (k, v.into()))),
                ),
            ])
            .stringify(),
        )
        .into_raw())
    })
}
#[repr(C)]
pub struct Info {
    publication_id: *const c_char,
    topic: *const c_char,
    epoch: *const c_char,
    sequence: u64,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Hooks {
    user: *mut c_void,
    decode:
        Option<unsafe extern "C" fn(*mut c_void, *const c_char, *const u8, usize) -> *mut c_void>,
    free_value: Option<unsafe extern "C" fn(*mut c_void)>,
    on_message: Option<unsafe extern "C" fn(*mut c_void, *const c_void, *const Info) -> c_int>,
    destroy: Option<unsafe extern "C" fn(*mut c_void)>,
}
struct Foreign(Hooks);
unsafe impl Send for Foreign {}
unsafe impl Sync for Foreign {}
impl Drop for Foreign {
    fn drop(&mut self) {
        if let Some(f) = self.0.destroy {
            unsafe { f(self.0.user) }
        }
    }
}
struct ForeignValue {
    pointer: *mut c_void,
    free: unsafe extern "C" fn(*mut c_void),
}
unsafe impl Send for ForeignValue {}
impl Drop for ForeignValue {
    fn drop(&mut self) {
        unsafe { (self.free)(self.pointer) }
    }
}
#[no_mangle]
pub unsafe extern "C" fn cenv_subscribe_raw(
    p: *mut Client,
    topic: *const c_char,
    root: *const c_char,
    cat: *const c_char,
    policy: *const c_char,
    hooks: Hooks,
) -> *mut Subscription {
    let state = Arc::new(Foreign(hooks)); // Takes ownership, including on failure.
    guard(|| {
        let c = p
            .as_ref()
            .ok_or_else(|| Error::new("argument", "NULL client"))?;
        let q = json::parse(string(policy)?.as_bytes())?;
        let options = Options {
            latest: text(&q, "buffer")? == "latest",
            capacity: number(&q, "capacity")? as usize,
            overflow: text(&q, "overflow")?.into(),
            ack_received: text(&q, "ack")? == "received",
            retry: Some(Retry {
                max_retries: number(&q, "max_retries")?
                    .try_into()
                    .map_err(|_| Error::new("qos", "retry count"))?,
                ack_timeout_ms: number(&q, "ack_timeout_ms")?
                    .try_into()
                    .map_err(|_| Error::new("qos", "ack timeout"))?,
                backoff_multiplier: number(&q, "backoff_multiplier")?
                    .try_into()
                    .map_err(|_| Error::new("qos", "backoff"))?,
            }),
            wait_timeout_ms: number(&q, "wait_timeout_ms")?
                .try_into()
                .map_err(|_| Error::new("qos", "wait timeout"))?,
            udp: text(&q, "transport")? == "udp",
        };
        if !matches!(text(&q, "buffer")?, "fifo" | "latest")
            || !matches!(text(&q, "ack")?, "none" | "received")
            || !matches!(text(&q, "transport")?, "tcp" | "udp")
        {
            return Err(Error::new("qos", "unsupported policy"));
        }
        let decode = hooks
            .decode
            .ok_or_else(|| Error::new("argument", "decode hook required"))?;
        let free = hooks
            .free_value
            .ok_or_else(|| Error::new("argument", "free hook required"))?;
        let call = hooks
            .on_message
            .ok_or_else(|| Error::new("argument", "callback required"))?;
        let d = state.clone();
        let cb = state.clone();
        let sub = c.subscribe_raw(
            string(topic)?,
            string(root)?,
            string(cat)?,
            options,
            Box::new(move |cat, b| {
                let value = decode(d.0.user, cstring(cat).as_ptr(), b.as_ptr(), b.len());
                if value.is_null() {
                    return Err(Error::new("schema", "C decode failed"));
                }
                Ok(Box::new(ForeignValue {
                    pointer: value,
                    free,
                }))
            }),
            Box::new(move |value, i| {
                let value = value
                    .downcast::<ForeignValue>()
                    .map_err(|_| Error::new("schema", "C value mismatch"))?;
                let id = cstring(&i.publication_id);
                let topic = cstring(&i.topic);
                let epoch = cstring(&i.epoch);
                let info = Info {
                    publication_id: id.as_ptr(),
                    topic: topic.as_ptr(),
                    epoch: epoch.as_ptr(),
                    sequence: i.sequence,
                };
                if call(cb.0.user, value.pointer, &info) == 0 {
                    return Err(Error::new("callback_failed", "C callback returned zero"));
                }
                Ok(())
            }),
        )?;
        Ok(Box::into_raw(Box::new(sub)))
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_unsubscribe(s: *mut Subscription) -> c_int {
    guard(|| {
        s.as_ref()
            .ok_or_else(|| Error::new("argument", "NULL subscription"))?
            .close()?;
        Ok(1)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_wait_closed(s: *mut Subscription, timeout_ms: u32) -> c_int {
    guard(|| {
        Ok(s.as_ref()
            .ok_or_else(|| Error::new("argument", "NULL subscription"))?
            .wait_closed(Duration::from_millis(timeout_ms as u64)) as c_int)
    })
}
#[no_mangle]
pub unsafe extern "C" fn cenv_subscription_free(s: *mut Subscription) {
    if !s.is_null() {
        drop(Box::from_raw(s));
    }
}
#[no_mangle]
pub unsafe extern "C" fn cenv_subscription_stats(s: *mut Subscription) -> *mut c_char {
    guard(|| {
        Ok(cstring(
            s.as_ref()
                .ok_or_else(|| Error::new("argument", "NULL subscription"))?
                .stats()?
                .stringify(),
        )
        .into_raw())
    })
}
