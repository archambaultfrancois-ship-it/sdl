//! External CENV/1 client. Control, each subscription's admission, and callbacks run independently.
use std::{
    any::Any,
    collections::{BTreeMap, HashMap, HashSet, VecDeque},
    io::{Read, Write},
    net::{Shutdown, SocketAddr, TcpStream, ToSocketAddrs, UdpSocket},
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc, Arc, Condvar, Mutex, Weak,
    },
    thread,
    time::{Duration, Instant},
};
mod ffi;
pub mod json;
use json::{object, Json};
#[derive(Clone, Debug)]
pub struct Error {
    pub code: String,
    pub message: String,
}
impl Error {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self::new("connection_lost", e.to_string())
    }
}
pub type Result<T> = std::result::Result<T, Error>;
type Packet = (Json, Vec<u8>);
type Value = Box<dyn Any + Send>;
type Decoder = Box<dyn FnMut(&str, &[u8]) -> Result<Value> + Send>;
type Callback = Box<dyn FnMut(Value, &MessageInfo) -> Result<()> + Send>;
fn text<'a>(j: &'a Json, k: &str) -> Result<&'a str> {
    j.get(k)?.text()
}
fn number(j: &Json, k: &str) -> Result<u64> {
    j.get(k)?.uint()
}
fn packet(h: &Json, b: &[u8], limit: usize) -> Result<Vec<u8>> {
    let m = h.stringify();
    if m.len() > 65536 || 7 + m.len() + b.len() > limit {
        return Err(Error::new("oversize", "frame limit exceeded"));
    }
    let mut p = Vec::with_capacity(7 + m.len() + b.len());
    p.extend(b"CE\x01");
    p.extend((m.len() as u32).to_be_bytes());
    p.extend(m.as_bytes());
    p.extend(b);
    Ok(p)
}
fn unpack(p: &[u8], limit: usize) -> Result<Packet> {
    if p.len() < 7 || p.len() > limit || &p[..3] != b"CE\x01" {
        return Err(Error::new("protocol", "invalid packet"));
    }
    let n = u32::from_be_bytes(p[3..7].try_into().unwrap()) as usize;
    if n > 65536 || n > p.len() - 7 {
        return Err(Error::new("protocol", "metadata size"));
    }
    let h = json::parse(&p[7..7 + n])?;
    text(&h, "op")?;
    Ok((h, p[7 + n..].to_vec()))
}
fn receive(s: &mut TcpStream, limit: usize) -> Result<Packet> {
    receive_with(s, || limit)
}
fn receive_with(s: &mut TcpStream, limit: impl Fn() -> usize) -> Result<Packet> {
    let mut n = [0; 4];
    s.read_exact(&mut n)?;
    let n = u32::from_be_bytes(n) as usize;
    let limit = limit();
    if !(7..=limit).contains(&n) {
        return Err(Error::new("protocol", "TCP length"));
    }
    let mut p = vec![0; n];
    s.read_exact(&mut p)?;
    unpack(&p, limit)
}
fn send(s: &mut TcpStream, h: &Json, b: &[u8], limit: usize) -> Result<()> {
    let p = packet(h, b, limit)?;
    s.write_all(&(p.len() as u32).to_be_bytes())?;
    s.write_all(&p)?;
    Ok(())
}
#[derive(Clone, Debug)]
pub struct Retry {
    pub max_retries: u32,
    pub ack_timeout_ms: u32,
    pub backoff_multiplier: u32,
}
impl Default for Retry {
    fn default() -> Self {
        Self {
            max_retries: 3,
            ack_timeout_ms: 100,
            backoff_multiplier: 2,
        }
    }
}
#[derive(Clone, Debug)]
pub struct Options {
    pub latest: bool,
    pub capacity: usize,
    pub overflow: String,
    pub ack_received: bool,
    pub retry: Option<Retry>,
    pub wait_timeout_ms: u32,
    pub udp: bool,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            latest: false,
            capacity: 256,
            overflow: "reject_new".into(),
            ack_received: false,
            retry: None,
            wait_timeout_ms: 100,
            udp: false,
        }
    }
}
impl Options {
    pub fn latest() -> Self {
        Self {
            latest: true,
            capacity: 1,
            overflow: "drop_oldest".into(),
            ..Self::default()
        }
    }
    fn json(&self) -> Json {
        let r = self.retry.clone().unwrap_or(Retry {
            max_retries: 0,
            ..Retry::default()
        });
        object([
            ("buffer", if self.latest { "latest" } else { "fifo" }.into()),
            ("capacity", (self.capacity as u64).into()),
            ("overflow", self.overflow.clone().into()),
            (
                "ack",
                if self.ack_received {
                    "received"
                } else {
                    "none"
                }
                .into(),
            ),
            ("max_retries", (r.max_retries as u64).into()),
            ("ack_timeout_ms", (r.ack_timeout_ms as u64).into()),
            ("backoff_multiplier", (r.backoff_multiplier as u64).into()),
            ("wait_timeout_ms", (self.wait_timeout_ms as u64).into()),
            ("transport", if self.udp { "udp" } else { "tcp" }.into()),
        ])
    }
}
#[derive(Clone, Debug)]
pub struct MessageInfo {
    pub publication_id: String,
    pub topic: String,
    pub epoch: String,
    pub sequence: u64,
}
#[derive(Clone, Debug)]
pub struct PublishResult {
    pub publication_id: String,
    pub sequence: u64,
    pub epoch: String,
    pub delivery: BTreeMap<String, String>,
}
impl PublishResult {
    pub fn accepted(&self) -> bool {
        self.delivery.values().all(|v| v == "accepted")
    }
}
struct Inner {
    writer: Mutex<TcpStream>,
    shutdown: TcpStream,
    udp: UdpSocket,
    endpoint: Mutex<SocketAddr>,
    address: SocketAddr,
    pending: Mutex<HashMap<u64, mpsc::SyncSender<Result<Packet>>>>,
    serial: AtomicU64,
    limit: AtomicUsize,
    datagram: AtomicUsize,
    timeout: Mutex<Duration>,
    session: Mutex<String>,
    subscriptions: Mutex<HashMap<String, Arc<Sub>>>,
    catalogues: Mutex<VecDeque<(u32, String)>>,
    stopped: AtomicBool,
    failure: Mutex<Option<Error>>,
    wake: Condvar,
}
pub struct Client {
    inner: Arc<Inner>,
}
impl Inner {
    fn fail(&self, error: Error) {
        let mut f = self.failure.lock().unwrap();
        if f.is_none() {
            *f = Some(error.clone())
        }
        drop(f);
        self.stop();
    }
    fn stop(&self) {
        let _guard = self.failure.lock().unwrap();
        self.stopped.store(true, Ordering::SeqCst);
        let _ = self.shutdown.shutdown(Shutdown::Both);
        self.wake.notify_all();
        for (_, p) in self.pending.lock().unwrap().drain() {
            let _ = p.try_send(Err(Error::new("connection_lost", "client closed")));
        }
        for s in self.subscriptions.lock().unwrap().values() {
            s.stop();
        }
    }
    fn rpc(&self, op: &str, args: Json, body: &[u8]) -> Result<Packet> {
        if self.stopped.load(Ordering::SeqCst) {
            return Err(Error::new("connection_lost", "client closed"));
        }
        let id = self.serial.fetch_add(1, Ordering::SeqCst);
        let mut h = if let Json::Object(m) = args {
            m
        } else {
            BTreeMap::new()
        };
        h.insert("op".into(), op.into());
        h.insert("request".into(), id.into());
        let (tx, rx) = mpsc::sync_channel(1);
        {
            let mut p = self.pending.lock().unwrap();
            if p.len() >= 32 {
                return Err(Error::new("capacity", "too many pending RPCs"));
            }
            p.insert(id, tx);
        }
        let write = send(
            &mut self.writer.lock().unwrap(),
            &Json::Object(h),
            body,
            self.limit.load(Ordering::SeqCst),
        );
        if let Err(e) = write {
            self.pending.lock().unwrap().remove(&id);
            if e.code != "oversize" {
                self.fail(e.clone());
            }
            return Err(e);
        }
        let result = rx
            .recv_timeout(*self.timeout.lock().unwrap())
            .map_err(|_| Error::new("timeout", "request deadline; publication outcome unknown"));
        self.pending.lock().unwrap().remove(&id);
        let (h, b) = result??;
        if let Ok(e) = h.get("error") {
            return Err(Error::new(
                h.get("error_code")
                    .and_then(Json::text)
                    .unwrap_or("invalid_request"),
                e.text()?,
            ));
        }
        if h.get("content").and_then(Json::text).ok() == Some("json") {
            Ok((json::parse(&b)?, vec![]))
        } else {
            Ok((h, b))
        }
    }
    fn catalogue(&self, body: &[u8]) -> Result<String> {
        if body.len() < 10 || &body[..5] != b"S2PS\x02" {
            return Err(Error::new("schema", "invalid envelope"));
        }
        let id = u32::from_be_bytes(body[5..9].try_into().unwrap());
        if let Some((_, t)) = self
            .catalogues
            .lock()
            .unwrap()
            .iter()
            .find(|(n, _)| *n == id)
        {
            return Ok(t.clone());
        }
        let (_, b) = self.rpc(
            "catalogue",
            object([("catalogue_id", (id as u64).into())]),
            &[],
        )?;
        let t = String::from_utf8(b).map_err(|_| Error::new("schema", "catalogue UTF8"))?;
        let mut c = self.catalogues.lock().unwrap();
        c.push_back((id, t.clone()));
        if c.len() > 64 {
            c.pop_front();
        }
        Ok(t)
    }
}
impl Client {
    pub fn connect(host: &str, port: u16, client_id: &str) -> Result<Self> {
        let address = (host, port)
            .to_socket_addrs()?
            .find(|a| a.is_ipv4())
            .ok_or_else(|| Error::new("connection_lost", "IPv4 endpoint required"))?;
        let stream = TcpStream::connect_timeout(&address, Duration::from_secs(10))?;
        stream.set_nodelay(true)?;
        stream.set_write_timeout(Some(Duration::from_secs(15)))?;
        let mut reader = stream.try_clone()?;
        let udp = UdpSocket::bind("0.0.0.0:0")?;
        udp.set_read_timeout(Some(Duration::from_millis(100)))?;
        let inner = Arc::new(Inner {
            shutdown: stream.try_clone()?,
            writer: Mutex::new(stream),
            udp,
            endpoint: Mutex::new(address),
            address,
            pending: Mutex::new(HashMap::new()),
            serial: AtomicU64::new(1),
            limit: AtomicUsize::new(1024 * 1024),
            datagram: AtomicUsize::new(1200),
            timeout: Mutex::new(Duration::from_secs(15)),
            session: Mutex::new(String::new()),
            subscriptions: Mutex::new(HashMap::new()),
            catalogues: Mutex::new(VecDeque::new()),
            stopped: AtomicBool::new(false),
            failure: Mutex::new(None),
            wake: Condvar::new(),
        });
        let weak = Arc::downgrade(&inner);
        thread::spawn(move || loop {
            match receive_with(&mut reader, || {
                weak.upgrade()
                    .map(|c| c.limit.load(Ordering::SeqCst))
                    .unwrap_or(0)
            }) {
                Ok(p) => {
                    if let Some(c) = weak.upgrade() {
                        if let Ok(id) = number(&p.0, "request") {
                            if let Some(tx) = c.pending.lock().unwrap().remove(&id) {
                                let _ = tx.try_send(Ok(p));
                            }
                        }
                    }
                }
                Err(e) => {
                    if let Some(c) = weak.upgrade() {
                        if !c.stopped.load(Ordering::SeqCst) {
                            c.fail(e)
                        }
                    }
                    break;
                }
            }
        });
        let client = Self { inner };
        let (reply, _) = client.inner.rpc(
            "client_hello",
            object([
                ("client_id", client_id.into()),
                (
                    "udp_port",
                    (client.inner.udp.local_addr()?.port() as u64).into(),
                ),
            ]),
            &[],
        )?;
        *client.inner.session.lock().unwrap() = text(&reply, "session")?.into();
        client
            .inner
            .limit
            .store(number(&reply, "max_frame")? as usize, Ordering::SeqCst);
        client
            .inner
            .datagram
            .store(number(&reply, "max_datagram")? as usize, Ordering::SeqCst);
        *client.inner.timeout.lock().unwrap() =
            Duration::from_millis(15000 + number(&reply, "max_wait_timeout_ms")?);
        *client.inner.endpoint.lock().unwrap() =
            SocketAddr::new(address.ip(), number(&reply, "udp_port")? as u16);
        let weak = Arc::downgrade(&client.inner);
        let udp = client.inner.udp.try_clone()?;
        thread::spawn(move || {
            let mut p = vec![0; 65508];
            loop {
                let c = match weak.upgrade() {
                    Some(c) if !c.stopped.load(Ordering::SeqCst) => c,
                    _ => break,
                };
                let endpoint = *c.endpoint.lock().unwrap();
                let limit = c.datagram.load(Ordering::SeqCst);
                drop(c);
                match udp.recv_from(&mut p) {
                    Ok((n, a)) if a == endpoint => {
                        if let Ok((h, b)) = unpack(&p[..n], limit) {
                            if let (Some(c), Ok(id)) = (weak.upgrade(), text(&h, "subscription")) {
                                if let Some(s) = c.subscriptions.lock().unwrap().get(id) {
                                    let _ = s.stage.try_send((h, b));
                                }
                            }
                        }
                    }
                    Ok(_) => {}
                    Err(e)
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                        ) => {}
                    Err(e) => {
                        if let Some(c) = weak.upgrade() {
                            c.fail(e.into())
                        }
                        break;
                    }
                }
            }
        });
        Ok(client)
    }
    pub fn list_topics(&self) -> Result<Json> {
        Ok(self
            .inner
            .rpc("list_topics", Json::Null, &[])?
            .0
            .get("topics")?
            .clone())
    }
    pub fn run(&self) -> Result<()> {
        let mut f = self.inner.failure.lock().unwrap();
        while !self.inner.stopped.load(Ordering::SeqCst) {
            f = self.inner.wake.wait(f).unwrap()
        }
        match f.clone() {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }
    pub fn close(&self) {
        self.inner.stop();
        self.inner.subscriptions.lock().unwrap().clear();
    }
    pub fn publisher<M: Message>(&self, topic: &str) -> Result<Publisher<M>> {
        let codec = M::codec()?;
        self.declare(topic, M::root(), &M::catalogue()?)?;
        Ok(Publisher {
            client: Arc::downgrade(&self.inner),
            topic: topic.into(),
            codec: Mutex::new(codec),
        })
    }
    pub fn declare(&self, topic: &str, root: &str, catalogue: &str) -> Result<()> {
        self.inner.rpc(
            "declare_publisher",
            object([("topic", topic.into()), ("root", root.into())]),
            catalogue.as_bytes(),
        )?;
        Ok(())
    }
    pub fn publish_envelope(&self, topic: &str, envelope: &[u8]) -> Result<PublishResult> {
        publish(&self.inner, topic, envelope)
    }
    pub fn subscribe<M: Message>(
        &self,
        topic: &str,
        options: Options,
        mut callback: impl FnMut(M, &MessageInfo) -> Result<()> + Send + 'static,
    ) -> Result<Subscription> {
        let mut codec = M::codec()?;
        self.subscribe_raw(
            topic,
            M::root(),
            &M::catalogue()?,
            options,
            Box::new(move |cat, b| Ok(Box::new(codec.decode(cat, b)?))),
            Box::new(move |v, i| {
                callback(
                    *v.downcast::<M>()
                        .map_err(|_| Error::new("schema", "type mismatch"))?,
                    i,
                )
            }),
        )
    }
    fn subscribe_raw(
        &self,
        topic: &str,
        root: &str,
        catalogue: &str,
        options: Options,
        decoder: Decoder,
        callback: Callback,
    ) -> Result<Subscription> {
        let (r, _) = self.inner.rpc(
            "subscribe",
            object([
                ("topic", topic.into()),
                ("root", root.into()),
                ("policy", options.json()),
            ]),
            catalogue.as_bytes(),
        )?;
        let id = text(&r, "subscription")?.to_owned();
        let (tx, rx) = mpsc::sync_channel(options.capacity);
        let sub = Arc::new(Sub {
            id: id.clone(),
            owner: Arc::downgrade(&self.inner),
            options,
            stage: tx,
            queue: Mutex::new(Queue::default()),
            changed: Condvar::new(),
            stopped: AtomicBool::new(false),
            socket: Mutex::new(None),
            workers: AtomicUsize::new(2),
            released: AtomicBool::new(false),
        });
        self.inner
            .subscriptions
            .lock()
            .unwrap()
            .insert(id, sub.clone());
        let s = sub.clone();
        thread::spawn(move || {
            let result = admission(&s, rx, decoder);
            s.finished(result)
        });
        let s = sub.clone();
        thread::spawn(move || {
            let result = dispatch(&s, callback);
            s.finished(result)
        });
        let setup = (|| {
            if !sub.options.udp {
                let mut stream =
                    TcpStream::connect_timeout(&self.inner.address, Duration::from_secs(10))?;
                stream.set_write_timeout(Some(Duration::from_secs(15)))?;
                stream.set_nodelay(true)?;
                send(
                    &mut stream,
                    &object([
                        ("op", "attach_client".into()),
                        ("request", 1u64.into()),
                        ("session", self.inner.session.lock().unwrap().clone().into()),
                        ("subscription", sub.id.clone().into()),
                    ]),
                    &[],
                    self.inner.limit.load(Ordering::SeqCst),
                )?;
                stream.set_read_timeout(Some(Duration::from_secs(15)))?;
                let (h, _) = receive(&mut stream, self.inner.limit.load(Ordering::SeqCst))?;
                if let Ok(e) = h.get("error") {
                    return Err(Error::new("session", e.text()?));
                }
                stream.set_read_timeout(None)?;
                *sub.socket.lock().unwrap() = Some(stream.try_clone()?);
                let s = sub.clone();
                let limit = self.inner.limit.load(Ordering::SeqCst);
                s.workers.fetch_add(1, Ordering::SeqCst);
                thread::spawn(move || {
                    let r = (|| {
                        while !s.stopped.load(Ordering::SeqCst) {
                            let p = receive(&mut stream, limit)?;
                            let mut p = Some(p);
                            while let Some(v) = p.take() {
                                match s.stage.try_send(v) {
                                    Ok(()) => {}
                                    Err(mpsc::TrySendError::Full(v)) => {
                                        if s.stopped.load(Ordering::SeqCst) {
                                            return Ok(());
                                        }
                                        p = Some(v);
                                        thread::sleep(Duration::from_millis(1));
                                    }
                                    Err(_) => return Ok(()),
                                }
                            }
                        }
                        Ok(())
                    })();
                    s.finished(r)
                });
            }
            self.inner.rpc(
                "activate_subscription",
                object([("subscription", sub.id.clone().into())]),
                &[],
            )?;
            Ok(())
        })();
        if let Err(e) = setup {
            sub.stop();
            self.inner.subscriptions.lock().unwrap().remove(&sub.id);
            let _ = self.inner.rpc(
                "unsubscribe",
                object([("subscription", sub.id.clone().into())]),
                &[],
            );
            return Err(e);
        }
        Ok(Subscription { sub })
    }
}
impl Drop for Client {
    fn drop(&mut self) {
        self.close()
    }
}
fn publish(c: &Inner, topic: &str, b: &[u8]) -> Result<PublishResult> {
    let (r, _) = c.rpc("publish", object([("topic", topic.into())]), b)?;
    let mut delivery = BTreeMap::new();
    if let Json::Object(d) = r.get("delivery")? {
        for (k, v) in d {
            delivery.insert(k.clone(), v.text()?.into());
        }
    } else {
        return Err(Error::new("protocol", "delivery map"));
    }
    Ok(PublishResult {
        publication_id: text(&r, "publication_id")?.into(),
        sequence: number(&r, "sequence")?,
        epoch: text(&r, "epoch")?.into(),
        delivery,
    })
}
pub trait Message: Send + 'static + Sized {
    type Codec: MessageCodec<Self> + Send;
    fn root() -> &'static str;
    fn catalogue() -> Result<String>;
    fn codec() -> Result<Self::Codec>;
}
pub trait MessageCodec<M> {
    fn encode(&mut self, message: &M) -> Result<Vec<u8>>;
    fn decode(&mut self, catalogue: &str, envelope: &[u8]) -> Result<M>;
}
impl<M, C: MessageCodec<M> + ?Sized> MessageCodec<M> for Box<C> {
    fn encode(&mut self, m: &M) -> Result<Vec<u8>> {
        (**self).encode(m)
    }
    fn decode(&mut self, c: &str, b: &[u8]) -> Result<M> {
        (**self).decode(c, b)
    }
}
pub struct Publisher<M: Message> {
    client: Weak<Inner>,
    topic: String,
    codec: Mutex<M::Codec>,
}
impl<M: Message> Publisher<M> {
    pub fn publish(&self, m: &M) -> Result<PublishResult> {
        let b = self.codec.lock().unwrap().encode(m)?;
        publish(
            self.client
                .upgrade()
                .ok_or_else(|| Error::new("connection_lost", "client gone"))?
                .as_ref(),
            &self.topic,
            &b,
        )
    }
    pub fn close(&self) -> Result<()> {
        if let Some(c) = self.client.upgrade() {
            c.rpc(
                "release_publisher",
                object([("topic", self.topic.clone().into())]),
                &[],
            )?;
        }
        Ok(())
    }
}
struct Delivery {
    value: Value,
    info: MessageInfo,
}
#[derive(Default)]
struct Queue {
    items: VecDeque<Delivery>,
    active: bool,
    seen: HashSet<String>,
    history: VecDeque<String>,
    pending_ids: HashSet<String>,
    last: Option<(String, u64)>,
}
struct Sub {
    id: String,
    owner: Weak<Inner>,
    options: Options,
    stage: mpsc::SyncSender<Packet>,
    queue: Mutex<Queue>,
    changed: Condvar,
    stopped: AtomicBool,
    socket: Mutex<Option<TcpStream>>,
    workers: AtomicUsize,
    released: AtomicBool,
}
impl Sub {
    fn stop(&self) {
        let _guard = self.queue.lock().unwrap();
        self.stopped.store(true, Ordering::SeqCst);
        if let Some(s) = self.socket.lock().unwrap().take() {
            let _ = s.shutdown(Shutdown::Both);
        }
        self.changed.notify_all();
    }
    fn finished(&self, result: Result<()>) {
        if let Err(e) = result {
            if !self.stopped.load(Ordering::SeqCst) {
                if let Some(c) = self.owner.upgrade() {
                    c.fail(e)
                }
            }
        }
        let _guard = self.queue.lock().unwrap();
        self.workers.fetch_sub(1, Ordering::SeqCst);
        self.changed.notify_all();
    }
}
pub struct Subscription {
    sub: Arc<Sub>,
}
impl Subscription {
    pub fn close(&self) -> Result<()> {
        if self.sub.released.swap(true, Ordering::SeqCst) {
            return Ok(());
        }
        self.sub.stop();
        if let Some(c) = self.sub.owner.upgrade() {
            c.subscriptions.lock().unwrap().remove(&self.sub.id);
            if !c.stopped.load(Ordering::SeqCst) {
                c.rpc(
                    "unsubscribe",
                    object([("subscription", self.sub.id.clone().into())]),
                    &[],
                )?;
            }
        }
        Ok(())
    }
    pub fn wait_closed(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        let mut q = self.sub.queue.lock().unwrap();
        while self.sub.workers.load(Ordering::SeqCst) > 0 {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return false;
            }
            q = self.sub.changed.wait_timeout(q, remaining).unwrap().0;
        }
        true
    }
    pub fn stats(&self) -> Result<Json> {
        let c = self
            .sub
            .owner
            .upgrade()
            .ok_or_else(|| Error::new("connection_lost", "client gone"))?;
        Ok(c.rpc(
            "subscription_stats",
            object([("subscription", self.sub.id.clone().into())]),
            &[],
        )?
        .0)
    }
}
impl Drop for Subscription {
    fn drop(&mut self) {
        let _ = self.close();
    }
}
fn admission(s: &Sub, rx: mpsc::Receiver<Packet>, mut decode: Decoder) -> Result<()> {
    while !s.stopped.load(Ordering::SeqCst) {
        let (h, b) = match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(p) => p,
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(_) => break,
        };
        if text(&h, "op")? != "data" || text(&h, "subscription")? != s.id {
            return Err(Error::new("protocol", "unexpected data"));
        }
        let c = s
            .owner
            .upgrade()
            .ok_or_else(|| Error::new("connection_lost", "client gone"))?;
        let cat = c.catalogue(&b)?;
        let value = decode(&cat, &b)?;
        let info = MessageInfo {
            publication_id: text(&h, "id")?.into(),
            topic: text(&h, "topic")?.into(),
            epoch: text(&h, "epoch")?.into(),
            sequence: number(&h, "sequence")?,
        };
        let deadline = Instant::now() + Duration::from_millis(s.options.wait_timeout_ms as u64);
        let mut q = s.queue.lock().unwrap();
        let duplicate = q.pending_ids.contains(&info.publication_id)
            || q.seen.contains(&info.publication_id)
            || s.options.latest
                && q.last
                    .as_ref()
                    .map(|(e, n)| e == &info.epoch && *n >= info.sequence)
                    .unwrap_or(false);
        let mut accepted = duplicate;
        if !duplicate {
            loop {
                if s.stopped.load(Ordering::SeqCst) {
                    return Ok(());
                }
                let depth = q.items.len() + usize::from(q.active && !s.options.latest);
                if depth < s.options.capacity {
                    accepted = true;
                    break;
                }
                if s.options.overflow == "drop_oldest" {
                    if let Some(victim) = q.items.pop_front() {
                        q.pending_ids.remove(&victim.info.publication_id);
                        accepted = true;
                    }
                    break;
                }
                if s.options.overflow != "wait" || Instant::now() >= deadline {
                    break;
                }
                q = s
                    .changed
                    .wait_timeout(q, deadline.saturating_duration_since(Instant::now()))
                    .unwrap()
                    .0;
            }
            if accepted {
                q.last = Some((info.epoch.clone(), info.sequence));
                q.seen.insert(info.publication_id.clone());
                q.history.push_back(info.publication_id.clone());
                if q.history.len() > 4096 {
                    let old = q.history.pop_front().unwrap();
                    q.seen.remove(&old);
                }
                q.pending_ids.insert(info.publication_id.clone());
                q.items.push_back(Delivery {
                    value,
                    info: info.clone(),
                });
                s.changed.notify_all();
            }
        }
        drop(q);
        if accepted && s.options.ack_received {
            let ack = object([
                ("op", "ack".into()),
                ("subscription", s.id.clone().into()),
                ("id", info.publication_id.into()),
            ]);
            if s.options.udp {
                c.udp.send_to(
                    &packet(&ack, &[], c.datagram.load(Ordering::SeqCst))?,
                    *c.endpoint.lock().unwrap(),
                )?;
            } else {
                send(
                    &mut c.writer.lock().unwrap(),
                    &ack,
                    &[],
                    c.limit.load(Ordering::SeqCst),
                )?;
            }
        }
    }
    Ok(())
}
fn dispatch(s: &Sub, mut callback: Callback) -> Result<()> {
    loop {
        let mut q = s.queue.lock().unwrap();
        while q.items.is_empty() && !s.stopped.load(Ordering::SeqCst) {
            q = s.changed.wait(q).unwrap()
        }
        if s.stopped.load(Ordering::SeqCst) {
            q.items.clear();
            q.pending_ids.clear();
            return Ok(());
        }
        let d = q.items.pop_front().unwrap();
        q.active = true;
        drop(q);
        let result =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| callback(d.value, &d.info)));
        let mut q = s.queue.lock().unwrap();
        q.active = false;
        q.pending_ids.remove(&d.info.publication_id);
        s.changed.notify_all();
        drop(q);
        match result {
            Ok(Ok(())) => {}
            Ok(Err(e)) => return Err(Error::new("callback_failed", e.to_string())),
            Err(_) => return Err(Error::new("callback_failed", "callback panicked")),
        }
    }
}

/// Register a generated local SDL type with this SDK, using the application's SDL runtime.
#[macro_export]
macro_rules! sdl_message {
    ($ty:ty) => {
        impl $crate::Message for $ty {
            type Codec = Box<dyn $crate::MessageCodec<Self> + Send>;
            fn root() -> &'static str {
                <Self as sdl_runtime::SdlMessage>::NAME
            }
            fn catalogue() -> $crate::Result<String> {
                sdl_runtime::description(&[<Self as sdl_runtime::SdlMessage>::type_info()])
                    .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))
            }
            fn codec() -> $crate::Result<Self::Codec> {
                struct Codec {
                    local: String,
                    encoder: sdl_runtime::Context,
                    decoder: Option<(String, sdl_runtime::Context)>,
                }
                impl $crate::MessageCodec<$ty> for Codec {
                    fn encode(&mut self, m: &$ty) -> $crate::Result<Vec<u8>> {
                        let b = sdl_runtime::encode(&self.encoder, m)
                            .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))?;
                        let id = sdl_runtime::catalogue_id(&self.local)
                            .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))?;
                        sdl_runtime::pack_envelope(id, &b)
                            .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))
                    }
                    fn decode(&mut self, cat: &str, b: &[u8]) -> $crate::Result<$ty> {
                        let (id, p) = sdl_runtime::unpack_envelope(b)
                            .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))?;
                        if id
                            != sdl_runtime::catalogue_id(cat)
                                .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))?
                        {
                            return Err($crate::Error::new(
                                "schema",
                                "catalogue identity mismatch",
                            ));
                        }
                        if self.decoder.as_ref().map(|v| v.0.as_str()) != Some(cat) {
                            let check = sdl_runtime::compare_catalogues(
                                cat,
                                &self.local,
                                Some(<$ty as sdl_runtime::SdlMessage>::NAME),
                                true,
                            );
                            if !check.compatible() {
                                return Err($crate::Error::new(
                                    "schema",
                                    format!("{} at {}", check.code, check.path),
                                ));
                            }
                            let ctx = sdl_runtime::prepare(
                                cat,
                                &[<$ty as sdl_runtime::SdlMessage>::type_info()],
                            )
                            .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))?;
                            self.decoder = Some((cat.into(), ctx));
                        }
                        sdl_runtime::decode(&self.decoder.as_ref().unwrap().1, p)
                            .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))
                    }
                }
                let local = <Self as $crate::Message>::catalogue()?;
                let encoder =
                    sdl_runtime::prepare(&local, &[<Self as sdl_runtime::SdlMessage>::type_info()])
                        .map_err(|e| $crate::Error::new("schema", format!("{e:?}")))?;
                Ok(Box::new(Codec {
                    local,
                    encoder,
                    decoder: None,
                }))
            }
        }
    };
}
