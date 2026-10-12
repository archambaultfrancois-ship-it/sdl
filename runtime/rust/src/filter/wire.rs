use super::*;
use crate::dynamic::FieldDesc;
use std::collections::HashMap;
use std::cell::RefCell;

pub(super) struct Layout {
    sizes:HashMap<String,Option<usize>>, offsets:HashMap<String,HashMap<u32,usize>>, quantum:HashMap<String,f64>,
    bounds:Vec<Option<Bound>>,
}
struct Bound {bit:usize,width:usize,kind:SpanKind,quantum:Option<f64>,float32:bool,boolean:bool,enums:Option<Vec<i32>>}
impl Layout {
    pub fn root_size(&self,root:&str)->Option<usize>{self.sizes.get(root).copied().flatten()}
    pub fn prepare(schema:&SchemaDesc)->Self{
        let mut layout=Self{sizes:HashMap::new(),offsets:HashMap::new(),quantum:HashMap::new(),bounds:Vec::new()};
        for owner in schema.messages.keys(){layout.message_size(schema,owner);}
        for (owner,m) in &schema.messages{
            let mut position=Some(0usize);
            for f in &m.fields{
                if let Some(q)=&f.wire.q{layout.quantum.insert(q.clone(),q.parse().unwrap());}
                if let Some(p)=position{
                    let p=field_start(f,p,0).unwrap_or(usize::MAX);
                    if p!=usize::MAX{layout.offsets.entry(owner.clone()).or_default().insert(f.id,p);}
                    position=layout.field_size(schema,f).and_then(|n|p.checked_add(n)).and_then(|p|field_end(f,p));
                }
            }
        }layout
    }
    pub fn bind_paths(&mut self,schema:&SchemaDesc,root:&str,paths:&[Path]){
        self.bounds=paths.iter().map(|p|if p.base.is_none(){self.bound(schema,root,&p.parts)}else{None}).collect();
    }
    fn bound(&self,schema:&SchemaDesc,root:&str,parts:&[Part])->Option<Bound>{
        let mut name=root;let mut bit=0usize;let mut field:Option<&FieldDesc>=None;let mut dim=0;
        for part in parts{
            match part{
                Part::Field{id,owner,..}=>{
                    if name=="c32"||name=="c64"{if *id<1||*id>2{return None;}let width=if name=="c32"{4}else{8};return Some(Bound{bit:bit+(*id as usize-1)*width*8,width,kind:SpanKind::Float,quantum:None,float32:false,boolean:false,enums:None});}
                    if owner!=name||field.is_some_and(|f|dim<f.dimensions.len()){return None;}
                    let f=schema.messages.get(name)?.fields.iter().find(|f|f.id==*id)?;
                    if f.modifier!=0{return None;}bit=bit.checked_add(*self.offsets.get(name)?.get(id)?)?;name=&f.type_name;field=Some(f);dim=0;
                },
                Part::Index(i)=>{let f=field?;let n=*f.dimensions.get(dim)?;if *i>=n{return None;}dim+=1;bit=bit.checked_add(i.checked_mul(self.element_size(schema,f,dim)?)?)?;}
            }
        }
        let f=field?;if dim<f.dimensions.len()||f.wire.encoding.is_some()||bit%8!=0{return None;}
        let bits=f.wire.bits.map_or_else(||match name{"bool"|"int8"|"uint8"=>8,"int16"|"uint16"=>16,"int32"|"uint32"|"fl32"=>32,"int64"|"uint64"|"fl64"=>64,n if schema.enums.contains_key(n)=>32,_=>0},|n|n as usize);
        if !matches!(bits,8|16|32|64){return None;}
        let kind=if matches!(name,"fl32"|"fl64"){SpanKind::Float}else if name.starts_with("uint")||name=="bool"{SpanKind::Unsigned}else{SpanKind::Signed};
        Some(Bound{bit,width:bits/8,kind,quantum:f.wire.q.as_ref().map(|q|self.quantum[q]),float32:name=="fl32",boolean:name=="bool",enums:schema.enums.get(name).map(|e|e.iter().map(|(_,v)|*v).collect())})
    }
    fn field_size(&mut self,schema:&SchemaDesc,f:&FieldDesc)->Option<usize>{
        if f.modifier!=0||f.wire.encoding.is_some(){return None;}
        let mut size=if let Some(bits)=f.wire.bits{bits as usize}else{self.type_size(schema,&f.type_name)?};
        for n in &f.dimensions{size=size.checked_mul(*n)?;}Some(size)
    }
    fn type_size(&mut self,schema:&SchemaDesc,name:&str)->Option<usize>{
        Some(match name{"bool"|"int8"|"uint8"=>8,"int16"|"uint16"=>16,"int32"|"uint32"|"fl32"=>32,"int64"|"uint64"|"fl64"|"c32"=>64,"c64"=>128,"string"=>return None,
            n if schema.enums.contains_key(n)=>32,n=>self.message_size(schema,n)?})
    }
    fn message_size(&mut self,schema:&SchemaDesc,name:&str)->Option<usize>{
        if let Some(n)=self.sizes.get(name){return *n;}
        let mut position=Some(0usize);
        for f in &schema.messages[name].fields{
            position=position.and_then(|p|field_start(f,p,0)).and_then(|p|self.field_size(schema,f).and_then(|n|p.checked_add(n))).and_then(|p|field_end(f,p));
        }
        position=position.and_then(|p|p.checked_add((8-p%8)%8));self.sizes.insert(name.into(),position);position
    }
    fn element_size(&self,schema:&SchemaDesc,f:&FieldDesc,dim:usize)->Option<usize>{
        if f.wire.encoding.is_some(){return None;}
        let mut n=if let Some(bits)=f.wire.bits{bits as usize}else{match f.type_name.as_str(){"bool"|"int8"|"uint8"=>8,"int16"|"uint16"=>16,"int32"|"uint32"|"fl32"=>32,"int64"|"uint64"|"fl64"|"c32"=>64,"c64"=>128,"string"=>return None,n if schema.enums.contains_key(n)=>32,n=>self.sizes.get(n).copied().flatten()?}};
        for d in &f.dimensions[dim..]{n=n.checked_mul(*d)?;}Some(n)
    }
}
fn field_start(f:&FieldDesc,mut p:usize,base:usize)->Option<usize>{
    if f.wire.bits.is_none()||f.modifier!=0||f.wire.align.is_some(){p=p.checked_add((8-p%8)%8)?;}
    if let Some(a)=f.wire.align{let a=a as usize;let offset=(p-base)/8;p=p.checked_add((a-offset%a)%a*8)?;}Some(p)
}
fn field_end(f:&FieldDesc,mut p:usize)->Option<usize>{
    if let Some(pad)=f.wire.pad_after{if pad>0{p=p.checked_add((8-p%8)%8)?.checked_add(pad as usize*8)?;}}Some(p)
}
#[derive(Clone,Copy)]
struct Cursor<'a>{data:&'a[u8],bit:usize}
impl<'a> Cursor<'a>{
    fn skip(&mut self,n:usize)->Result<(),FilterError>{self.bit=self.bit.checked_add(n).filter(|p|*p<=self.data.len().saturating_mul(8)).ok_or_else(||error("truncated wire value"))?;Ok(())}
    fn seek(&mut self,bit:usize)->Result<(),FilterError>{if bit>self.data.len().saturating_mul(8){return Err(error("truncated wire value"));}self.bit=bit;Ok(())}
    fn bits(&mut self,n:usize)->Result<u64,FilterError>{
        if n>64{return Err(error("invalid scalar width"));}let start=self.bit;self.skip(n)?;let mut value=0;
        if start%8==0&&n%8==0{for b in &self.data[start/8..self.bit/8]{value=(value<<8)|u64::from(*b);}}
        else{for i in start..self.bit{value=(value<<1)|u64::from((self.data[i/8]>>(7-i%8))&1);}}Ok(value)
    }
    fn varint(&mut self,width:usize)->Result<u64,FilterError>{
        let mut value=0;for i in 0..width.div_ceil(7){let b=self.bits(8)?;if i==9&&b>1{return Err(error("varint overflow"));}value|=(b&127)<<(i*7);
            if width<64&&value>>width!=0{return Err(error("varint overflow"));}if b<128{if i>0&&b==0{return Err(error("noncanonical varint"));}return Ok(value);}}
        Err(error("varint overflow"))
    }
    fn bytes(&mut self,n:usize)->Result<&'a[u8],FilterError>{if self.bit%8!=0{return Err(error("unaligned wire bytes"));}let start=self.bit/8;self.skip(n.checked_mul(8).ok_or_else(||error("wire length overflow"))?)?;Ok(&self.data[start..self.bit/8])}
}
#[derive(Clone,Copy,Default)]
struct Location {field:usize,dim:usize,origin:usize,index:usize,bit:usize}
struct Locations {entries:[std::mem::MaybeUninit<Location>;64],used:usize,next:usize}
pub(super) struct WireSource<'a>{filter:&'a Filter,data:&'a[u8],body:usize,locations:RefCell<Option<Box<Locations>>>}
impl<'a> WireSource<'a>{
    pub fn new(filter:&'a Filter,data:&'a[u8])->Result<Self,FilterError>{
        let mut cursor=Cursor{data,bit:0};let id=if data.first().is_some_and(|b|*b<128){cursor.bit=8;data[0] as usize}else{cursor.varint(32)? as usize};
        if id!=filter.wire_id{return Err(error("wire root does not match filter type"));}
        if let Some(size)=filter.fixed_wire_bits{if size!=data.len().saturating_mul(8)-cursor.bit{return Err(error("invalid fixed wire message size"));}}
        Ok(Self{filter,data,body:cursor.bit,locations:RefCell::new(None)})
    }
    fn message(&self,name:&str,path:&[Step<'_>],cursor:&mut Cursor<'a>)->Result<Atom<'a>,FilterError>{
        if path.is_empty(){return Ok(Atom::Message);}
        let field=self.locate(name,path,cursor)?;
        self.field(field,&path[1..],cursor)
    }
    fn locate(&self,name:&str,path:&[Step<'_>],cursor:&mut Cursor<'a>)->Result<&FieldDesc,FilterError>{
        let (id,owner)=match path[0]{Step::Field{id,owner,..}=>(id,owner),_=>return Err(error("message requires a field"))};
        if owner!=name{return Err(error("wire path owner mismatch"));}
        let base=cursor.bit;let fields=&self.filter.schema.messages[name].fields;
        // Fixed prefixes are prepared once; no preceding values need decoding.
        if let Some(offset)=self.filter.wire.offsets.get(name).and_then(|offsets|offsets.get(&id)){
            cursor.seek(base.checked_add(*offset).ok_or_else(||error("wire offset overflow"))?)?;
            let f=fields.iter().find(|f|f.id==id).ok_or_else(||error("unknown field ID"))?;
            return Ok(f);
        }
        for f in fields{
            cursor.seek(field_start(f,cursor.bit,base).ok_or_else(||error("wire offset overflow"))?)?;
            if f.id==id{return Ok(f);}
            self.skip_field(f,cursor)?;
            cursor.seek(field_end(f,cursor.bit).ok_or_else(||error("wire offset overflow"))?)?;
        }Err(error("unknown field ID"))
    }
    fn column(&self,name:&str,path:&[Step<'_>],cursor:&mut Cursor<'a>,column:Option<(usize,usize)>)->Result<Option<Span<'a>>,FilterError>{
        if path.is_empty(){return Ok(None);}
        let f=self.locate(name,path,cursor)?;
        let mut path=&path[1..];let mut column=column;
        let count=self.count(f,cursor)?;
        if f.modifier==1&&count==0{return Ok(None);}
        if f.modifier>=2{
            let Some((Step::Index(index),tail))=path.split_first()else{return Ok(None)};
            if *index==usize::MAX{
                if column.is_some()||count==0{return Ok(None);}
                let Some(stride)=self.filter.wire.element_size(&self.filter.schema,f,0).filter(|n|n%8==0)else{return Ok(None)};
                column=Some((count,stride/8));
            }else{if *index>=count{return Ok(None);}self.skip_elements(f,0,*index,cursor)?;}
            path=tail;
        }
        for (dim,count) in f.dimensions.iter().enumerate(){
            let Some((Step::Index(index),tail))=path.split_first()else{return Ok(None)};
            if *index==usize::MAX{
                if column.is_some()||*count==0{return Ok(None);}
                let Some(stride)=self.filter.wire.element_size(&self.filter.schema,f,dim+1).filter(|n|n%8==0)else{return Ok(None)};
                column=Some((*count,stride/8));
            }else{if *index>=*count{return Ok(None);}self.skip_elements(f,dim+1,*index,cursor)?;}
            path=tail;
        }
        let name=f.type_name.as_str();
        if self.filter.schema.messages.contains_key(name){return self.column(name,path,cursor,column);}
        let Some((count,stride))=column else{return Ok(None)};
        let (kind,width)=match name{
            "c32"|"c64"=>{
                let [Step::Field{id,..}]=path else{return Ok(None)};
                if !matches!(id,1|2){return Ok(None);}
                let width=if name=="c32"{4}else{8};cursor.skip((*id as usize-1)*width*8)?;
                (SpanKind::Float,width)
            },
            "int8"|"int16"|"int32"|"int64"|"uint8"|"uint16"|"uint32"|"uint64"|"fl32"|"fl64"=>{
                if !path.is_empty()||f.wire.encoding.is_some(){return Ok(None);}
                let bits=f.wire.bits.map_or_else(||match name{"int8"|"uint8"=>8,"int16"|"uint16"=>16,"int32"|"uint32"|"fl32"=>32,_=>64},|b|b as usize);
                if !matches!(bits,8|16|32|64){return Ok(None);}
                (if name.starts_with("uint"){SpanKind::Unsigned}else if name.starts_with("int"){SpanKind::Signed}else{SpanKind::Float},bits/8)
            },_=>return Ok(None),
        };
        if cursor.bit%8!=0{return Ok(None);}
        let start=cursor.bit/8;
        let end=(count-1).checked_mul(stride).and_then(|n|n.checked_add(start)).and_then(|n|n.checked_add(width)).ok_or_else(||error("wire column overflow"))?;
        if end>self.data.len(){return Ok(None);}
        Ok(Some(unsafe{Span::new(self.data.as_ptr().add(start),count,stride,kind,width,true,f.wire.q.as_ref().map(|q|self.filter.wire.quantum[q]),name=="fl32")}))
    }
    fn count(&self,f:&FieldDesc,cursor:&mut Cursor<'a>)->Result<usize,FilterError>{
        let n=if f.modifier==0{1}else{cursor.varint(32)? as usize};
        if f.modifier==1&&n>1{return Err(error("invalid field count"));}
        if n>1048576{
            // Fixed wire elements are bounded by the buffer, not the variable
            // element limit. Keep small targeted reads lazy as before.
            let size=self.filter.wire.element_size(&self.filter.schema,f,0)
                .filter(|size|*size>0).ok_or_else(||error("invalid field count"))?;
            let remaining=self.data.len().saturating_mul(8).saturating_sub(cursor.bit);
            if n>remaining/size{return Err(error("truncated wire value"));}
        }
        Ok(n)
    }
    fn field(&self,f:&FieldDesc,path:&[Step<'_>],cursor:&mut Cursor<'a>)->Result<Atom<'a>,FilterError>{
        let count=self.count(f,cursor)?;if f.modifier==1&&count==0{return Ok(Atom::Missing);}
        if f.modifier>=2{
            if path.is_empty(){return Ok(Atom::Array(count));}
            let index=match path[0]{Step::Index(i)=>i,_=>return Err(error("array requires an index"))};
            if index>=count{return Ok(Atom::Missing);}self.skip_elements(f,0,index,cursor)?;return self.value(f,0,&path[1..],cursor);
        }self.value(f,0,path,cursor)
    }
    fn value(&self,f:&FieldDesc,dim:usize,path:&[Step<'_>],cursor:&mut Cursor<'a>)->Result<Atom<'a>,FilterError>{
        if dim<f.dimensions.len(){
            if path.is_empty(){return Ok(Atom::Array(f.dimensions[dim]));}
            let index=match path[0]{Step::Index(i)=>i,_=>return Err(error("array requires an index"))};
            if index>=f.dimensions[dim]{return Ok(Atom::Missing);}self.skip_elements(f,dim+1,index,cursor)?;
            return self.value(f,dim+1,&path[1..],cursor);
        }
        let name=f.type_name.as_str();if self.filter.schema.messages.contains_key(name){return self.message(name,path,cursor);}
        let value=if name=="string"{let n=cursor.varint(32)? as usize;Atom::String(std::str::from_utf8(cursor.bytes(n)?).map_err(|_|error("invalid UTF-8"))?)}
        else if name=="c32"||name=="c64"{
            if let [Step::Field{id,..}]=path{
                if !matches!(id,1|2){return Err(error("invalid complex component"));}
                let width=if name=="c32"{32}else{64};cursor.skip((*id as usize-1)*width)?;
                return Ok(Atom::Float(if width==32{f32::from_bits(cursor.bits(32)? as u32) as f64}else{f64::from_bits(cursor.bits(64)?)}));
            }
            let (r,i)=if name=="c32"{(f32::from_bits(cursor.bits(32)? as u32) as f64,f32::from_bits(cursor.bits(32)? as u32) as f64)}else{(f64::from_bits(cursor.bits(64)?),f64::from_bits(cursor.bits(64)?))};Atom::Complex(r,i)}
        else{
            let width=match name{"bool"|"int8"|"uint8"=>8,"int16"|"uint16"=>16,"int64"|"uint64"|"fl64"=>64,_=>32};
            let bits=f.wire.bits.map_or(width,|n|n as usize);
            let mut raw=if f.wire.encoding.is_some(){cursor.varint(width)?}else{cursor.bits(bits)?};
            let signed=name.starts_with("int")||self.filter.schema.enums.contains_key(name)||f.wire.q.is_some();
            if f.wire.bits.is_some()&&signed&&bits<64&&raw&(1u64<<(bits-1))!=0{raw|=u64::MAX<<bits;}
            else if f.wire.encoding.as_deref()==Some("zigzag"){raw=(raw>>1)^0u64.wrapping_sub(raw&1);}
            else if f.wire.encoding.is_some()&&signed&&raw>>(width-1)!=0{return Err(error("signed varint is out of range"));}
            else if f.wire.encoding.is_none()&&signed&&bits<64&&raw&(1u64<<(bits-1))!=0{raw|=u64::MAX<<bits;}
            if let Some(q)=&f.wire.q{let value=(raw as i64 as f64)*self.filter.wire.quantum[q];Atom::Float(if name=="fl32"{value as f32 as f64}else{value})}
            else if name=="fl32"{Atom::Float(f32::from_bits(raw as u32) as f64)}else if name=="fl64"{Atom::Float(f64::from_bits(raw))}
            else if name=="bool"{if raw>1{return Err(error("invalid boolean"));}Atom::Bool(raw!=0)}
            else if signed{if let Some(values)=self.filter.schema.enums.get(name){if !values.iter().any(|(_,n)|*n as i64==raw as i64){return Err(error("invalid enum"));}}Atom::Signed(raw as i64)}else{Atom::Unsigned(raw)}
        };
        if !path.is_empty(){return Err(error("path continues beyond scalar"));}Ok(value)
    }
    fn skip_elements(&self,f:&FieldDesc,dim:usize,n:usize,cursor:&mut Cursor<'a>)->Result<(),FilterError>{
        if n==0{return Ok(());}
        if let Some(size)=self.filter.wire.element_size(&self.filter.schema,f,dim){return cursor.skip(size.checked_mul(n).ok_or_else(||error("wire length overflow"))?);}
        let field=f as *const FieldDesc as usize;let origin=cursor.bit;let mut start=0;
        {let locations=self.locations.borrow();if let Some(cache)=locations.as_deref(){for i in 0..cache.used{let last=unsafe{cache.entries[i].assume_init()};if last.field==field&&last.dim==dim&&last.origin==origin&&last.index<=n{cursor.seek(last.bit)?;start=last.index;break;}}}}
        for _ in start..n{self.skip_value(f,dim,cursor)?;}
        let mut locations=self.locations.borrow_mut();let cache=locations.get_or_insert_with(||Box::new(Locations{entries:[std::mem::MaybeUninit::uninit();64],used:0,next:0}));
        let mut found=None;for i in 0..cache.used{let l=unsafe{cache.entries[i].assume_init()};if l.field==field&&l.dim==dim&&l.origin==origin{found=Some(i);break;}}
        let slot=found.unwrap_or_else(||{let slot=if cache.used<64{let slot=cache.used;cache.used+=1;slot}else{let slot=cache.next%64;cache.next+=1;slot};slot});
        cache.entries[slot].write(Location{field,dim,origin,index:n,bit:cursor.bit});Ok(())
    }
    fn skip_field(&self,f:&FieldDesc,cursor:&mut Cursor<'a>)->Result<(),FilterError>{let n=self.count(f,cursor)?;self.skip_elements(f,0,n,cursor)}
    fn skip_value(&self,f:&FieldDesc,dim:usize,cursor:&mut Cursor<'a>)->Result<(),FilterError>{
        if dim<f.dimensions.len(){return self.skip_elements(f,dim+1,f.dimensions[dim],cursor);}
        if f.type_name=="string"{let n=cursor.varint(32)? as usize;return cursor.skip(n.checked_mul(8).ok_or_else(||error("wire length overflow"))?);}
        if let Some(m)=self.filter.schema.messages.get(&f.type_name){let base=cursor.bit;for child in &m.fields{
            cursor.seek(field_start(child,cursor.bit,base).ok_or_else(||error("wire offset overflow"))?)?;self.skip_field(child,cursor)?;
            cursor.seek(field_end(child,cursor.bit).ok_or_else(||error("wire offset overflow"))?)?;
        }return cursor.skip((8-cursor.bit%8)%8);}
        self.value(f,dim,&[],cursor).map(|_|())
    }
}
impl Source for WireSource<'_>{
    fn span_slot<'a>(&'a self,slot:usize,path:&[Step<'_>])->Option<Span<'a>>{
        // real(value)/imag(value) load a complete complex atom. Preserve that
        // validation; an explicit value.real/value.imag path needs one component.
        if self.filter.paths.get(slot).is_some_and(|p|p.ty==Ty::Complex){return None;}
        self.span(path)
    }
    fn read_float_prepared(&self,slot:usize)->Option<Result<Option<f64>,FilterError>>{
        let b=self.filter.wire.bounds.get(slot)?.as_ref()?;
        if b.kind!=SpanKind::Float{return None;}
        Some((||{
            let start=self.body.checked_add(b.bit).ok_or_else(||error("wire offset overflow"))?/8;
            let end=start.checked_add(b.width).ok_or_else(||error("wire offset overflow"))?;
            let bytes=self.data.get(start..end).ok_or_else(||error("truncated wire value"))?;
            let raw=match b.width{1=>bytes[0] as u64,2=>u16::from_be_bytes(bytes.try_into().unwrap()) as u64,4=>u32::from_be_bytes(bytes.try_into().unwrap()) as u64,_=>u64::from_be_bytes(bytes.try_into().unwrap())};
            let value=if let Some(q)=b.quantum{
                let signed=match b.width{1=>raw as i8 as i64,2=>raw as i16 as i64,4=>raw as i32 as i64,_=>raw as i64};
                let value=signed as f64*q;if b.float32{value as f32 as f64}else{value}
            }else if b.width==4{f32::from_bits(raw as u32) as f64}else{f64::from_bits(raw)};
            Ok(Some(value))
        })())
    }
    fn span<'a>(&'a self,path:&[Step<'_>])->Option<Span<'a>>{
        self.column(&self.filter.root,path,&mut Cursor{data:self.data,bit:self.body},None).ok().flatten()
    }
    fn read<'a>(&'a self,path:&[Step<'_>])->Result<Atom<'a>,FilterError>{let mut cursor=Cursor{data:self.data,bit:self.body};self.message(&self.filter.root,path,&mut cursor)}
    fn read_slot<'a>(&'a self,slot:usize,path:&[Step<'_>])->Result<Atom<'a>,FilterError>{
        match self.read_prepared(slot){Some(v)=>v,None=>self.read(path)}
    }
    fn read_prepared<'a>(&'a self,slot:usize)->Option<Result<Atom<'a>,FilterError>>{
        let bound=self.filter.wire.bounds.get(slot)?.as_ref()?;
        Some((||{
            let start=self.body.checked_add(bound.bit).ok_or_else(||error("wire offset overflow"))?/8;
            let bytes=self.data.get(start..start.checked_add(bound.width).ok_or_else(||error("wire offset overflow"))?).ok_or_else(||error("truncated wire value"))?;
            let value=unsafe{Span::new(bytes.as_ptr(),1,bound.width,bound.kind,bound.width,true,bound.quantum,bound.float32)}.value(0)?;
            if bound.boolean{let v=integer(value).ok_or_else(||error("invalid boolean"))?;if v>1{return Err(error("invalid boolean"));}return Ok(Atom::Bool(v!=0));}
            if let Some(values)=&bound.enums{let v=integer(value).unwrap();if !values.iter().any(|n|*n as i128==v){return Err(error("invalid enum"));}}
            return Ok(value);
        })())
    }
}
