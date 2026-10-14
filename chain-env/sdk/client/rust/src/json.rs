//! Bounded CENV JSON parser. Number lexemes retain integer precision.
use crate::{Error, Result};
use std::collections::BTreeMap;
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Number(String),
    String(String),
    Array(Vec<Json>),
    Object(BTreeMap<String, Json>),
}
impl Json {
    pub fn get(&self, k: &str) -> Result<&Self> {
        if let Self::Object(m) = self {
            m.get(k)
                .ok_or_else(|| Error::new("protocol", format!("missing {k}")))
        } else {
            Err(Error::new("protocol", "expected object"))
        }
    }
    pub fn text(&self) -> Result<&str> {
        if let Self::String(s) = self {
            Ok(s)
        } else {
            Err(Error::new("protocol", "expected string"))
        }
    }
    pub fn uint(&self) -> Result<u64> {
        if let Self::Number(s) = self {
            s.parse()
                .map_err(|_| Error::new("protocol", "expected uint"))
        } else {
            Err(Error::new("protocol", "expected number"))
        }
    }
    pub fn stringify(&self) -> String {
        match self {
            Self::Null => "null".into(),
            Self::Bool(v) => v.to_string(),
            Self::Number(v) => v.clone(),
            Self::String(v) => {
                let mut s = String::from("\"");
                for c in v.chars() {
                    match c {
                        '"' => s.push_str("\\\""),
                        '\\' => s.push_str("\\\\"),
                        c if c < ' ' => s.push_str(&format!("\\u{:04x}", c as u32)),
                        c => s.push(c),
                    }
                }
                s.push('"');
                s
            }
            Self::Array(a) => format!(
                "[{}]",
                a.iter().map(Self::stringify).collect::<Vec<_>>().join(",")
            ),
            Self::Object(m) => format!(
                "{{{}}}",
                m.iter()
                    .map(|(k, v)| format!(
                        "{}:{}",
                        Self::String(k.clone()).stringify(),
                        v.stringify()
                    ))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        }
    }
}
impl From<&str> for Json {
    fn from(v: &str) -> Self {
        Self::String(v.into())
    }
}
impl From<String> for Json {
    fn from(v: String) -> Self {
        Self::String(v)
    }
}
impl From<u64> for Json {
    fn from(v: u64) -> Self {
        Self::Number(v.to_string())
    }
}
pub fn object(values: impl IntoIterator<Item = (impl Into<String>, Json)>) -> Json {
    Json::Object(values.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
pub fn parse(data: &[u8]) -> Result<Json> {
    struct Parser<'a> {
        d: &'a [u8],
        i: usize,
    }
    impl Parser<'_> {
        fn bad<T>(&self) -> Result<T> {
            Err(Error::new("protocol", "invalid JSON"))
        }
        fn ws(&mut self) {
            while self.i < self.d.len() && b" \r\n\t".contains(&self.d[self.i]) {
                self.i += 1
            }
        }
        fn hex(&mut self) -> Result<u16> {
            if self.i + 4 > self.d.len() {
                return self.bad();
            }
            let s = std::str::from_utf8(&self.d[self.i..self.i + 4])
                .map_err(|_| Error::new("protocol", "UTF8"))?;
            self.i += 4;
            u16::from_str_radix(s, 16).map_err(|_| Error::new("protocol", "unicode escape"))
        }
        fn string(&mut self) -> Result<String> {
            if self.d.get(self.i) != Some(&b'"') {
                return self.bad();
            }
            self.i += 1;
            let mut out = Vec::new();
            loop {
                let c = *self
                    .d
                    .get(self.i)
                    .ok_or_else(|| Error::new("protocol", "truncated string"))?;
                self.i += 1;
                match c {
                    b'"' => break,
                    b'\\' => {
                        let c = *self
                            .d
                            .get(self.i)
                            .ok_or_else(|| Error::new("protocol", "escape"))?;
                        self.i += 1;
                        match c {
                            b'"' | b'\\' | b'/' => out.push(c),
                            b'b' => out.push(8),
                            b'f' => out.push(12),
                            b'n' => out.push(10),
                            b'r' => out.push(13),
                            b't' => out.push(9),
                            b'u' => {
                                let first = self.hex()?;
                                let code = if (0xd800..=0xdbff).contains(&first) {
                                    if self.d.get(self.i..self.i + 2) != Some(b"\\u") {
                                        return self.bad();
                                    }
                                    self.i += 2;
                                    let low = self.hex()?;
                                    if !(0xdc00..=0xdfff).contains(&low) {
                                        return self.bad();
                                    }
                                    0x10000 + (((first as u32) - 0xd800) << 10) + (low as u32)
                                        - 0xdc00
                                } else {
                                    first as u32
                                };
                                let ch = char::from_u32(code)
                                    .ok_or_else(|| Error::new("protocol", "unicode"))?;
                                let mut b = [0; 4];
                                out.extend_from_slice(ch.encode_utf8(&mut b).as_bytes());
                            }
                            _ => return self.bad(),
                        }
                    }
                    0..=31 => return self.bad(),
                    _ => out.push(c),
                }
            }
            String::from_utf8(out).map_err(|_| Error::new("protocol", "UTF8"))
        }
        fn value(&mut self, depth: usize) -> Result<Json> {
            self.ws();
            if depth > 64 {
                return self.bad();
            }
            match self.d.get(self.i).copied() {
                Some(b'"') => Ok(Json::String(self.string()?)),
                Some(b'{') => {
                    self.i += 1;
                    let mut m = BTreeMap::new();
                    self.ws();
                    if self.d.get(self.i) == Some(&b'}') {
                        self.i += 1;
                        return Ok(Json::Object(m));
                    }
                    loop {
                        self.ws();
                        let k = self.string()?;
                        self.ws();
                        if self.d.get(self.i) != Some(&b':') {
                            return self.bad();
                        }
                        self.i += 1;
                        let v = self.value(depth + 1)?;
                        if m.insert(k, v).is_some() {
                            return self.bad();
                        }
                        self.ws();
                        match self.d.get(self.i) {
                            Some(b'}') => {
                                self.i += 1;
                                break;
                            }
                            Some(b',') => self.i += 1,
                            _ => return self.bad(),
                        }
                    }
                    Ok(Json::Object(m))
                }
                Some(b'[') => {
                    self.i += 1;
                    let mut a = Vec::new();
                    self.ws();
                    if self.d.get(self.i) == Some(&b']') {
                        self.i += 1;
                        return Ok(Json::Array(a));
                    }
                    loop {
                        a.push(self.value(depth + 1)?);
                        self.ws();
                        match self.d.get(self.i) {
                            Some(b']') => {
                                self.i += 1;
                                break;
                            }
                            Some(b',') => self.i += 1,
                            _ => return self.bad(),
                        }
                    }
                    Ok(Json::Array(a))
                }
                Some(b'n') | Some(b't') | Some(b'f') => {
                    for (text, v) in [
                        (b"null".as_slice(), Json::Null),
                        (b"true", Json::Bool(true)),
                        (b"false", Json::Bool(false)),
                    ] {
                        if self.d.get(self.i..self.i + text.len()) == Some(text) {
                            self.i += text.len();
                            return Ok(v);
                        }
                    }
                    self.bad()
                }
                Some(b'-' | b'0'..=b'9') => {
                    let start = self.i;
                    if self.d[self.i] == b'-' {
                        self.i += 1
                    }
                    match self.d.get(self.i) {
                        Some(b'0') => self.i += 1,
                        Some(b'1'..=b'9') => {
                            while matches!(self.d.get(self.i), Some(b'0'..=b'9')) {
                                self.i += 1
                            }
                        }
                        _ => return self.bad(),
                    }
                    if self.d.get(self.i) == Some(&b'.') {
                        self.i += 1;
                        let s = self.i;
                        while matches!(self.d.get(self.i), Some(b'0'..=b'9')) {
                            self.i += 1
                        }
                        if self.i == s {
                            return self.bad();
                        }
                    }
                    if matches!(self.d.get(self.i), Some(b'e' | b'E')) {
                        self.i += 1;
                        if matches!(self.d.get(self.i), Some(b'+' | b'-')) {
                            self.i += 1
                        }
                        let s = self.i;
                        while matches!(self.d.get(self.i), Some(b'0'..=b'9')) {
                            self.i += 1
                        }
                        if self.i == s {
                            return self.bad();
                        }
                    }
                    Ok(Json::Number(
                        std::str::from_utf8(&self.d[start..self.i]).unwrap().into(),
                    ))
                }
                _ => self.bad(),
            }
        }
    }
    let mut p = Parser { d: data, i: 0 };
    let v = p.value(0)?;
    p.ws();
    if p.i != data.len() {
        return p.bad();
    }
    Ok(v)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn json_wire() {
        for s in [
            r#"{"x":"é🚀","n":18446744073709551615}"#,
            r#"{"x":"\ud83d\ude80"}"#,
        ] {
            let v = parse(s.as_bytes()).unwrap();
            assert_eq!(parse(v.stringify().as_bytes()).unwrap(), v)
        }
        for s in ["[1,]", "01", r#"{"a":1,"a":2}"#, r#""\ud800""#] {
            assert!(parse(s.as_bytes()).is_err())
        }
    }
}
