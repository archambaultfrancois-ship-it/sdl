//! Optional native TLS for the synchronous parameter channel. OpenSSL is loaded
//! only when TLS is requested; existing std-only message clients are unchanged.
use crate::{Error, Result};
use std::{
    ffi::{c_char, c_int, c_long, c_void, CString},
    io::{self, Read, Write},
    net::TcpStream,
    os::fd::AsRawFd,
    ptr,
};

#[link(name = "dl")]
extern "C" {
    fn dlopen(name: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, name: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}
type Ptr = *mut c_void;
struct Library(Ptr);
impl Drop for Library {
    fn drop(&mut self) {
        unsafe {
            dlclose(self.0);
        }
    }
}
struct Api {
    _library: Library,
    method: unsafe extern "C" fn() -> Ptr,
    ctx_new: unsafe extern "C" fn(Ptr) -> Ptr,
    ctx_free: unsafe extern "C" fn(Ptr),
    verify: unsafe extern "C" fn(Ptr, c_int, Ptr),
    ctrl: unsafe extern "C" fn(Ptr, c_int, c_long, Ptr) -> c_long,
    ca: unsafe extern "C" fn(Ptr, *const c_char, *const c_char) -> c_int,
    cert: unsafe extern "C" fn(Ptr, *const c_char) -> c_int,
    key: unsafe extern "C" fn(Ptr, *const c_char, c_int) -> c_int,
    check_key: unsafe extern "C" fn(Ptr) -> c_int,
    new: unsafe extern "C" fn(Ptr) -> Ptr,
    free: unsafe extern "C" fn(Ptr),
    fd: unsafe extern "C" fn(Ptr, c_int) -> c_int,
    connect: unsafe extern "C" fn(Ptr) -> c_int,
    verified: unsafe extern "C" fn(Ptr) -> c_long,
    peer: unsafe extern "C" fn(Ptr) -> Ptr,
    subject: unsafe extern "C" fn(Ptr) -> Ptr,
    name_index: unsafe extern "C" fn(Ptr, c_int, c_int) -> c_int,
    name_text: unsafe extern "C" fn(Ptr, c_int, *mut c_char, c_int) -> c_int,
    cert_free: unsafe extern "C" fn(Ptr),
    read: unsafe extern "C" fn(Ptr, Ptr, c_int) -> c_int,
    write: unsafe extern "C" fn(Ptr, *const c_void, c_int) -> c_int,
}
impl Api {
    unsafe fn load() -> Result<Self> {
        let mut handle = ptr::null_mut();
        for name in ["libssl.so", "libssl.so.3", "libssl.so.1.1", "libssl.dylib"] {
            handle = dlopen(CString::new(name).unwrap().as_ptr(), 2);
            if !handle.is_null() {
                break;
            }
        }
        if handle.is_null() {
            return Err(Error::new(
                "tls",
                "OpenSSL shared library is required for native TLS",
            ));
        }
        let library = Library(handle);
        macro_rules! symbol {
            ($name:literal, $typ:ty) => {{
                let address = dlsym(handle, concat!($name, "\0").as_ptr() as *const c_char);
                if address.is_null() {
                    return Err(Error::new(
                        "tls",
                        concat!("missing OpenSSL symbol: ", $name),
                    ));
                }
                std::mem::transmute::<Ptr, $typ>(address)
            }};
        }
        let peer = dlsym(
            handle,
            b"SSL_get1_peer_certificate\0".as_ptr() as *const c_char,
        );
        let peer = if peer.is_null() {
            dlsym(
                handle,
                b"SSL_get_peer_certificate\0".as_ptr() as *const c_char,
            )
        } else {
            peer
        };
        if peer.is_null() {
            return Err(Error::new("tls", "missing OpenSSL peer certificate API"));
        }
        Ok(Self {
            _library: library,
            method: symbol!("TLS_client_method", unsafe extern "C" fn() -> Ptr),
            ctx_new: symbol!("SSL_CTX_new", unsafe extern "C" fn(Ptr) -> Ptr),
            ctx_free: symbol!("SSL_CTX_free", unsafe extern "C" fn(Ptr)),
            verify: symbol!("SSL_CTX_set_verify", unsafe extern "C" fn(Ptr, c_int, Ptr)),
            ctrl: symbol!(
                "SSL_CTX_ctrl",
                unsafe extern "C" fn(Ptr, c_int, c_long, Ptr) -> c_long
            ),
            ca: symbol!(
                "SSL_CTX_load_verify_locations",
                unsafe extern "C" fn(Ptr, *const c_char, *const c_char) -> c_int
            ),
            cert: symbol!(
                "SSL_CTX_use_certificate_chain_file",
                unsafe extern "C" fn(Ptr, *const c_char) -> c_int
            ),
            key: symbol!(
                "SSL_CTX_use_PrivateKey_file",
                unsafe extern "C" fn(Ptr, *const c_char, c_int) -> c_int
            ),
            check_key: symbol!(
                "SSL_CTX_check_private_key",
                unsafe extern "C" fn(Ptr) -> c_int
            ),
            new: symbol!("SSL_new", unsafe extern "C" fn(Ptr) -> Ptr),
            free: symbol!("SSL_free", unsafe extern "C" fn(Ptr)),
            fd: symbol!("SSL_set_fd", unsafe extern "C" fn(Ptr, c_int) -> c_int),
            connect: symbol!("SSL_connect", unsafe extern "C" fn(Ptr) -> c_int),
            verified: symbol!("SSL_get_verify_result", unsafe extern "C" fn(Ptr) -> c_long),
            peer: std::mem::transmute::<Ptr, unsafe extern "C" fn(Ptr) -> Ptr>(peer),
            subject: symbol!("X509_get_subject_name", unsafe extern "C" fn(Ptr) -> Ptr),
            name_index: symbol!(
                "X509_NAME_get_index_by_NID",
                unsafe extern "C" fn(Ptr, c_int, c_int) -> c_int
            ),
            name_text: symbol!(
                "X509_NAME_get_text_by_NID",
                unsafe extern "C" fn(Ptr, c_int, *mut c_char, c_int) -> c_int
            ),
            cert_free: symbol!("X509_free", unsafe extern "C" fn(Ptr)),
            read: symbol!("SSL_read", unsafe extern "C" fn(Ptr, Ptr, c_int) -> c_int),
            write: symbol!(
                "SSL_write",
                unsafe extern "C" fn(Ptr, *const c_void, c_int) -> c_int
            ),
        })
    }
}

pub(crate) struct Tls {
    socket: TcpStream,
    api: Api,
    ctx: Ptr,
    ssl: Ptr,
}
// The parameter channel serializes each complete request/reply behind a mutex.
// No OpenSSL connection is accessed concurrently, including during destruction.
unsafe impl Send for Tls {}
impl Tls {
    pub(crate) fn connect(socket: TcpStream, ca: &str, cert: &str, key: &str) -> Result<Self> {
        let ca = CString::new(ca).map_err(|_| Error::new("tls", "invalid CA path"))?;
        let cert = CString::new(cert).map_err(|_| Error::new("tls", "invalid certificate path"))?;
        let key = CString::new(key).map_err(|_| Error::new("tls", "invalid key path"))?;
        unsafe {
            let api = Api::load()?;
            let ctx = (api.ctx_new)((api.method)());
            if ctx.is_null() {
                return Err(Error::new("tls", "TLS context creation failed"));
            }
            let mut result = Self {
                socket,
                api,
                ctx,
                ssl: ptr::null_mut(),
            };
            let a = &result.api;
            (a.verify)(ctx, 1, ptr::null_mut());
            if (a.ctrl)(ctx, 123, 0x0303, ptr::null_mut()) != 1
                || (a.ca)(ctx, ca.as_ptr(), ptr::null()) != 1
                || (a.cert)(ctx, cert.as_ptr()) != 1
                || (a.key)(ctx, key.as_ptr(), 1) != 1
                || (a.check_key)(ctx) != 1
            {
                return Err(Error::new("tls", "cannot load TLS credentials"));
            }
            result.ssl = (a.new)(ctx);
            if result.ssl.is_null()
                || (a.fd)(result.ssl, result.socket.as_raw_fd()) != 1
                || (a.connect)(result.ssl) != 1
                || (a.verified)(result.ssl) != 0
            {
                return Err(Error::new("tls", "TLS handshake or CA verification failed"));
            }
            let certificate = (a.peer)(result.ssl);
            if certificate.is_null() {
                return Err(Error::new("tls", "coordinator certificate required"));
            }
            let subject = (a.subject)(certificate);
            let mut name = [0u8; 256];
            let first = (a.name_index)(subject, 13, -1);
            let length = (a.name_text)(
                subject,
                13,
                name.as_mut_ptr() as *mut c_char,
                name.len() as c_int,
            );
            let valid = first >= 0
                && (a.name_index)(subject, 13, first) < 0
                && length == 11
                && &name[..11] == b"coordinator";
            (a.cert_free)(certificate);
            if !valid {
                return Err(Error::new("tls", "unexpected OSE certificate identity"));
            }
            Ok(result)
        }
    }
}
impl Read for Tls {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        if bytes.is_empty() {
            return Ok(0);
        }
        let n = unsafe {
            (self.api.read)(
                self.ssl,
                bytes.as_mut_ptr() as Ptr,
                bytes.len().min(i32::MAX as usize) as c_int,
            )
        };
        if n > 0 {
            Ok(n as usize)
        } else {
            Err(io::Error::new(
                io::ErrorKind::ConnectionAborted,
                "TLS read failed or connection closed",
            ))
        }
    }
}
impl Write for Tls {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.is_empty() {
            return Ok(0);
        }
        let n = unsafe {
            (self.api.write)(
                self.ssl,
                bytes.as_ptr() as *const c_void,
                bytes.len().min(i32::MAX as usize) as c_int,
            )
        };
        if n > 0 {
            Ok(n as usize)
        } else {
            Err(io::Error::new(
                io::ErrorKind::ConnectionAborted,
                "TLS write failed",
            ))
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
impl Drop for Tls {
    fn drop(&mut self) {
        unsafe {
            let _ = self.socket.shutdown(std::net::Shutdown::Both);
            if !self.ssl.is_null() {
                (self.api.free)(self.ssl);
            }
            (self.api.ctx_free)(self.ctx);
        }
    }
}
