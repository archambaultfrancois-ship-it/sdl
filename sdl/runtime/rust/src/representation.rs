//! Field wire representation, layered over the ordinary native codecs.
use crate::dynamic::{SchemaDesc, FieldDesc};
use crate::wire::CodecError;
const LIMIT: usize = 64 * 1024 * 1024;
#[derive(Clone, Debug, Default, PartialEq)]
pub struct WireOptions { pub bits: Option<u32>, pub q: Option<String>, pub encoding: Option<String>, pub align: Option<u32>, pub pad_after: Option<u32> }
impl WireOptions {
    pub(crate) fn parse(text: &str) -> Result<Self, CodecError> {
        let mut w = Self::default();
        for item in text.split(',') {
            if let Some(decimal) = item.strip_prefix('q') {
                let parts: Vec<_> = decimal.split('.').collect();
                if w.q.is_some() || parts.len() > 2 || parts.iter().any(|p| p.is_empty() || !p.as_bytes().iter().all(u8::is_ascii_digit)) {
                    return Err(CodecError::InvalidDescriptor);
                }
                w.q = Some(decimal.to_owned());
                continue;
            }
            if matches!(item, "v" | "z") {
                if w.encoding.is_some() { return Err(CodecError::InvalidDescriptor); }
                w.encoding = Some(if item == "v" { "varint" } else { "zigzag" }.to_owned());
                continue;
            }
            if item.len() < 2 || !item.as_bytes()[1..].iter().all(u8::is_ascii_digit) {
                return Err(CodecError::InvalidDescriptor);
            }
            let value = item[1..].parse().map_err(|_| CodecError::InvalidDescriptor)?;
            match item.as_bytes()[0] {
                b'b' if w.bits.is_none() => w.bits = Some(value),
                b'a' if w.align.is_none() => w.align = Some(value),
                b'p' if w.pad_after.is_none() => w.pad_after = Some(value),
                _ => return Err(CodecError::InvalidDescriptor),
            }
        }
        Ok(w)
    }
    pub(crate) fn validate(&self, name:&str, is_enum:bool)->Result<(),CodecError> {
        let width=match name {"bool"=>1,"int8"|"uint8"=>8,"int16"|"uint16"=>16,"int32"|"uint32"=>32,"int64"|"uint64"=>64,"fl32" if self.q.is_some()=>32,"fl64" if self.q.is_some()=>64,_ if is_enum=>32,_=>0};
        if self.bits.is_some_and(|b| b==0||b>width) || self.encoding.is_some() && (width==0||name=="bool"||self.bits.is_some()||self.encoding.as_deref()==Some("zigzag")&&name.starts_with("uint")) || self.align.is_some_and(|a| a==0||a>256||!a.is_power_of_two()) || self.pad_after.is_some_and(|p|p>65535) { return Err(CodecError::InvalidDescriptor); }
        if let Some(decimal) = &self.q {
            if !matches!(name, "fl32" | "fl64") || self.bits.is_none() || self.encoding.is_some() { return Err(CodecError::InvalidDescriptor); }
            let q: f64 = decimal.parse().map_err(|_| CodecError::InvalidDescriptor)?;
            let bound = q * 2f64.powi(self.bits.unwrap() as i32 - 1);
            if !q.is_finite() || q <= 0.0 || !bound.is_finite() || name == "fl32" && (q <= 2f64.powi(-150) || bound > f32::MAX as f64) { return Err(CodecError::InvalidDescriptor); }
        }
        Ok(())
    }
    pub(crate) fn suffix(&self)->String {
        let mut parts=Vec::new();
        if let Some(b)=self.bits {parts.push(format!("b{b}"));}
        if let Some(q)=&self.q {parts.push(format!("q{q}"));}
        if let Some(e)=&self.encoding {parts.push(if e == "varint" { "v".to_owned() } else { "z".to_owned() });}
        if let Some(a)=self.align {parts.push(format!("a{a}"));}
        if let Some(p)=self.pad_after {parts.push(format!("p{p}"));}
        if parts.is_empty(){String::new()}else{format!("@{}",parts.join(","))}
    }
}
struct Stream<'a> { input: Option<&'a [u8]>, out: Vec<u8>, bit:usize }
impl<'a> Stream<'a> {
    fn new(input:Option<&'a[u8]>)->Self{Self{input,out:Vec::new(),bit:0}}
    fn bits(&mut self,width:usize,mut value:u64)->Result<u64,CodecError>{
        if width>64||self.bit>LIMIT*8||width>LIMIT*8-self.bit{return Err(CodecError::LengthOverflow);}
        if let Some(data)=self.input {
            if self.bit>data.len()*8||width>data.len()*8-self.bit{return Err(CodecError::Truncated);}
            value=0;
            for _ in 0..width {value=(value<<1)|u64::from((data[self.bit/8]>>(7-self.bit%8))&1);self.bit+=1;}
        }else{
            for i in (0..width).rev(){if self.bit%8==0{self.out.push(0);}self.out[self.bit/8]|=(((value>>i)&1)as u8)<<(7-self.bit%8);self.bit+=1;}
        }
        Ok(value)
    }
    fn zeros(&mut self,mut n:usize)->Result<(),CodecError>{while n>0{let k=n.min(64);if self.bits(k,0)?!=0{return Err(CodecError::InvalidDescriptor);}n-=k;}Ok(())}
    fn align(&mut self)->Result<(),CodecError>{self.zeros((8-self.bit%8)%8)}
    fn varint(&mut self,mut value:u64,width:usize)->Result<u64,CodecError>{
        if self.input.is_some(){value=0;for i in 0..width.div_ceil(7){let b=self.bits(8,0)?;if i==9&&b>1{return Err(CodecError::LengthOverflow);}value|=(b&127)<<(i*7);if width<64&&value>>width!=0{return Err(CodecError::LengthOverflow);}if b<128{if i>0&&b==0{return Err(CodecError::InvalidDescriptor);}return Ok(value);}}return Err(CodecError::LengthOverflow);}
        let original=value;loop{self.bits(8,(value&127)|if value>=128{128}else{0})?;value>>=7;if value==0{break;}}Ok(original)
    }
}
fn copy(source:&mut Stream<'_>,dest:&mut Stream<'_>,n:usize)->Result<(),CodecError>{
    let data=source.input.ok_or(CodecError::InvalidDescriptor)?;
    if source.bit%8!=0||dest.bit%8!=0||source.bit/8>data.len()||n>data.len()-source.bit/8{return Err(CodecError::Truncated);}
    if n>LIMIT||dest.bit/8>LIMIT-n{return Err(CodecError::LengthOverflow);}
    dest.out.extend_from_slice(&data[source.bit/8..source.bit/8+n]);source.bit+=n*8;dest.bit+=n*8;Ok(())
}
fn count(plain:&mut Stream<'_>,custom:&mut Stream<'_>,encode:bool)->Result<u64,CodecError>{let n=if encode{plain.varint(0,32)?}else{custom.varint(0,32)?};if encode{custom.varint(n,32)?;}else{plain.varint(n,32)?;}Ok(n)}
fn value(schema:&SchemaDesc,f:&FieldDesc,dim:usize,plain:&mut Stream<'_>,custom:&mut Stream<'_>,encode:bool,depth:usize)->Result<(),CodecError>{
    if dim<f.dimensions.len(){if f.dimensions[dim]>1048576{return Err(CodecError::LengthOverflow);}for _ in 0..f.dimensions[dim]{value(schema,f,dim+1,plain,custom,encode,depth)?;}return Ok(());}
    if schema.messages.contains_key(&f.type_name){return message(schema,&f.type_name,plain,custom,encode,depth+1);}
    if f.type_name=="string"{let n=count(plain,custom,encode)? as usize;return if encode{copy(plain,custom,n)}else{copy(custom,plain,n)};}
    let size=match f.type_name.as_str(){"bool"|"int8"|"uint8"=>1,"int16"|"uint16"=>2,"int64"|"uint64"|"fl64"|"c32"=>8,"c64"=>16,_=>4};
    let w=&f.wire;
    if let Some(decimal) = &w.q {
        let q: f64 = decimal.parse().map_err(|_| CodecError::InvalidDescriptor)?;
        let bits = w.bits.ok_or(CodecError::InvalidDescriptor)? as usize;
        if encode {
            let raw = plain.bits(size * 8, 0)?;
            let x = if size == 4 { f32::from_bits(raw as u32) as f64 } else { f64::from_bits(raw) };
            let scaled = x / q;
            if !scaled.is_finite() { return Err(CodecError::TypeMismatch); }
            let mut rounded = scaled.floor();
            let fraction = scaled - rounded;
            if fraction > 0.5 || fraction == 0.5 && rounded % 2.0 != 0.0 { rounded += 1.0; }
            let limit = 2f64.powi(bits as i32 - 1);
            if rounded < -limit || rounded >= limit { return Err(CodecError::TypeMismatch); }
            custom.bits(bits, rounded as i64 as u64)?;
        } else {
            let mut raw = custom.bits(bits, 0)?;
            if bits < 64 && raw & (1u64 << (bits - 1)) != 0 { raw |= u64::MAX << bits; }
            let x = (raw as i64 as f64) * q;
            plain.bits(size * 8, if size == 4 { (x as f32).to_bits() as u64 } else { x.to_bits() })?;
        }
        return Ok(());
    }
    if w.bits.is_none()&&w.encoding.is_none(){return if encode{copy(plain,custom,size)}else{copy(custom,plain,size)};}
    let width=size*8;let bits=w.bits.map_or(width,|b|b as usize);let signed=f.type_name.starts_with("int")||schema.enums.contains_key(&f.type_name);
    if encode{
        let mut n=plain.bits(width,0)?;
        if signed&&width<64&&n&(1u64<<(width-1))!=0{n|=u64::MAX<<width;}
        if w.bits.is_some(){let mask=if bits==64{u64::MAX}else{(1u64<<bits)-1};let mut extended=n&mask;if signed&&bits<64&&extended&(1u64<<(bits-1))!=0{extended|=!mask;}if extended!=n{return Err(CodecError::TypeMismatch);}custom.bits(bits,n)?;}
        else{if w.encoding.as_deref()==Some("zigzag"){n=(n<<1)^(0u64.wrapping_sub(n>>63));}else if signed&&n>>63!=0{return Err(CodecError::TypeMismatch);}custom.varint(n,64)?;}
    }else{
        let mut n=if w.bits.is_some(){custom.bits(bits,0)?}else{custom.varint(0,width)?};
        if w.bits.is_some()&&signed&&bits<64&&n&(1u64<<(bits-1))!=0{n|=u64::MAX<<bits;}
        else if w.encoding.as_deref()==Some("zigzag"){n=(n>>1)^0u64.wrapping_sub(n&1);}
        else if w.bits.is_none()&&signed&&n>>(width-1)!=0{return Err(CodecError::TypeMismatch);}
        plain.bits(width,n)?;
    }Ok(())
}
fn message(schema:&SchemaDesc,name:&str,plain:&mut Stream<'_>,custom:&mut Stream<'_>,encode:bool,depth:usize)->Result<(),CodecError>{
    if depth>64{return Err(CodecError::InvalidDescriptor);}let base=custom.bit;
    for f in &schema.messages[name].fields{
        let w=&f.wire;
        if w.bits.is_none()||f.modifier!=0||w.align.is_some(){custom.align()?;}
        if let Some(a)=w.align{let a=a as usize;let offset=(custom.bit-base)/8;custom.zeros((a-offset%a)%a*8)?;}
        let n=if f.modifier==0{1}else{count(plain,custom,encode)?};
        if n>1048576||f.modifier==1&&n>1{return Err(CodecError::LengthOverflow);}
        for _ in 0..n{value(schema,f,0,plain,custom,encode,depth)?;}
        if let Some(p)=w.pad_after{if p>0{custom.align()?;custom.zeros(p as usize*8)?;}}
    }custom.align()
}
pub(crate) fn transform(schema:&SchemaDesc,names:&[String],data:&[u8],encode:bool)->Result<Vec<u8>,CodecError>{
    let mut source=Stream::new(Some(data));let mut dest=Stream::new(None);
    let (plain,custom)=if encode{(&mut source,&mut dest)}else{(&mut dest,&mut source)};
    let id=count(plain,custom,encode)? as usize;
    if id==0||id>names.len(){return Err(CodecError::TypeMismatch);}
    message(schema,&names[id-1],plain,custom,encode,0)?;
    if source.bit!=data.len()*8{return Err(CodecError::InvalidDescriptor);}Ok(dest.out)
}
