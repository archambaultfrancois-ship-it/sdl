//! Synchronous parameter-only client. One RPC at a time, with bounded long-polls.
//! Payloads are ordinary SDL bytes; callers supply their generated SDL codecs.
use crate::{
    json::{self, object, Json},
    packet, unpack, Error, Result,
};
use std::{
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    sync::Mutex,
    time::Duration,
};

trait Channel: Read + Write + Send {}
impl<T: Read + Write + Send> Channel for T {}
struct Connection {
    stream: Box<dyn Channel>,
    request: u64,
    failed: bool,
}
pub struct ParameterClient {
    connection: Mutex<Connection>,
}
pub struct TlsConfig<'a> {
    pub ca: &'a str,
    pub certificate: &'a str,
    pub key: &'a str,
}

impl ParameterClient {
    pub fn connect(host: &str, port: u16, client: &str, token: &str) -> Result<Self> {
        Self::open(host, port, client, Some(token), None)
    }
    pub fn connect_tls(host: &str, port: u16, client: &str, tls: TlsConfig<'_>) -> Result<Self> {
        Self::open(host, port, client, None, Some(tls))
    }
    fn open(
        host: &str,
        port: u16,
        client: &str,
        token: Option<&str>,
        tls: Option<TlsConfig<'_>>,
    ) -> Result<Self> {
        let address = (host, port)
            .to_socket_addrs()?
            .next()
            .ok_or_else(|| Error::new("address", "no server address"))?;
        let stream = TcpStream::connect_timeout(&address, Duration::from_secs(10))?;
        stream.set_read_timeout(Some(Duration::from_secs(15)))?;
        stream.set_write_timeout(Some(Duration::from_secs(15)))?;
        let stream: Box<dyn Channel> = if let Some(tls) = tls {
            #[cfg(unix)]
            {
                Box::new(crate::parameters_tls::Tls::connect(
                    stream,
                    tls.ca,
                    tls.certificate,
                    tls.key,
                )?)
            }
            #[cfg(not(unix))]
            {
                let _ = tls;
                return Err(Error::new(
                    "tls",
                    "native parameter TLS currently requires Unix",
                ));
            }
        } else {
            Box::new(stream)
        };
        let result = Self {
            connection: Mutex::new(Connection {
                stream,
                request: 0,
                failed: false,
            }),
        };
        result.request(
            "parameters_open",
            object([
                ("client", client.into()),
                ("token", token.map(Json::from).unwrap_or(Json::Null)),
            ]),
            &[],
        )?;
        Ok(result)
    }
    pub fn request(
        &self,
        operation: &str,
        arguments: Json,
        payload: &[u8],
    ) -> Result<(Json, Vec<u8>)> {
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| Error::new("closed", "parameter connection failed"))?;
        if connection.failed {
            return Err(Error::new(
                "closed",
                "parameter connection failed; reconnect",
            ));
        }
        let Json::Object(mut fields) = arguments else {
            return Err(Error::new("argument", "RPC arguments must be an object"));
        };
        if !operation.starts_with("parameters_") {
            return Err(Error::new("argument", "parameter RPC required"));
        }
        connection.request += 1;
        let request = connection.request;
        fields.insert("op".into(), operation.into());
        fields.insert("request".into(), request.into());
        let bytes = packet(&Json::Object(fields), payload, 1024 * 1024)?;
        let exchange = (|| -> Result<_> {
            connection
                .stream
                .write_all(&(bytes.len() as u32).to_be_bytes())?;
            connection.stream.write_all(&bytes)?;
            let mut size = [0; 4];
            connection.stream.read_exact(&mut size)?;
            let size = u32::from_be_bytes(size) as usize;
            if !(7..=1024 * 1024).contains(&size) {
                return Err(Error::new("protocol", "invalid parameter frame length"));
            }
            let mut bytes = vec![0; size];
            connection.stream.read_exact(&mut bytes)?;
            let (header, payload) = unpack(&bytes, 1024 * 1024)?;
            if header.get("op")?.text()? != "result" || header.get("request")?.uint()? != request {
                return Err(Error::new("protocol", "unexpected parameter reply"));
            }
            Ok((header, payload))
        })();
        let (header, payload) = match exchange {
            Ok(reply) => reply,
            Err(error) => {
                connection.failed = true;
                return Err(error);
            }
        };
        if let Ok(error) = header.get("error") {
            return Err(Error::new("parameters", error.text()?));
        }
        if header.get("content").and_then(Json::text).ok() == Some("json") {
            Ok((json::parse(&payload)?, Vec::new()))
        } else {
            Ok((header, payload))
        }
    }
    pub fn list(&self) -> Result<Json> {
        Ok(self
            .request("parameters_list", Json::Object(Default::default()), &[])?
            .0
            .get("blocks")?
            .clone())
    }
    pub fn describe(&self, path: &str) -> Result<(String, String)> {
        let (header, bytes) =
            self.request("parameters_describe", object([("path", path.into())]), &[])?;
        Ok((
            header.get("root")?.text()?.to_owned(),
            String::from_utf8(bytes).map_err(|_| Error::new("schema", "catalogue UTF8"))?,
        ))
    }
    pub fn get(&self, path: &str) -> Result<Vec<u8>> {
        Ok(self
            .request("parameters_get", object([("path", path.into())]), &[])?
            .1)
    }
    pub fn set(&self, path: &str, payload: &[u8]) -> Result<()> {
        self.request("parameters_set", object([("path", path.into())]), payload)?;
        Ok(())
    }
    pub fn subscribe(&self, paths: &[&str]) -> Result<()> {
        self.request(
            "parameters_subscribe",
            object([(
                "paths",
                Json::Array(paths.iter().map(|p| (*p).into()).collect()),
            )]),
            &[],
        )?;
        Ok(())
    }
    pub fn next(&self) -> Result<Option<(String, Vec<u8>)>> {
        let (header, bytes) =
            self.request("parameters_next", Json::Object(Default::default()), &[])?;
        if matches!(header.get("empty"), Ok(Json::Bool(true))) {
            return Ok(None);
        }
        Ok(Some((header.get("path")?.text()?.to_owned(), bytes)))
    }
    pub fn unsubscribe(&self) -> Result<()> {
        self.request(
            "parameters_unsubscribe",
            Json::Object(Default::default()),
            &[],
        )?;
        Ok(())
    }
}
