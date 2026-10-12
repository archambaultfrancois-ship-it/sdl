//! Catalogue-bound predicates over logical object values or wire values.
mod parser;
mod wire;
#[cfg(test)]
mod tests;
use crate::dynamic::{parse_descriptor, SchemaDesc};
use crate::{Complex32, Complex64};
use std::cmp::Ordering;

pub const MAX_PATH: usize = 512;
const MAX_LOADS: usize = 128;
const COMPLEX_OWNER:&str="$complex";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FilterError(pub String);
impl std::fmt::Display for FilterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str(&self.0) }
}
impl std::error::Error for FilterError {}
pub(crate) fn error(text: impl Into<String>) -> FilterError { FilterError(text.into()) }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FilterResult { Match, NoMatch, Indeterminate, Error(FilterError) }
impl FilterResult {
    pub fn code(&self) -> i32 { match self { Self::Match=>1, Self::NoMatch=>0, Self::Indeterminate=>2, Self::Error(_)=>-1 } }
}

#[derive(Clone, Copy, Debug)]
pub enum Step<'a> { Field { id: u32, owner: &'a str, name: &'a str }, Index(usize) }
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Atom<'a> {
    Missing, Bool(bool), Signed(i64), Unsigned(u64), Float(f64),
    String(&'a str), Complex(f64,f64), Array(usize), Message,
}
pub trait Source {
    fn read<'a>(&'a self, path: &[Step<'_>]) -> Result<Atom<'a>, FilterError>;
    /// A source may resolve a prepared slot without reconstructing its path.
    fn read_prepared<'a>(&'a self, _slot: usize) -> Option<Result<Atom<'a>, FilterError>> { None }
    /// Optional typed access for catalogue float slots, with identical missing/error semantics.
    fn read_float_prepared(&self, _slot: usize) -> Option<Result<Option<f64>, FilterError>> { None }
    fn read_slot<'a>(&'a self, _slot: usize, path: &[Step<'_>]) -> Result<Atom<'a>, FilterError> { self.read(path) }
    fn span<'a>(&'a self, _path: &[Step<'_>]) -> Option<Span<'a>> { None }
    fn span_slot<'a>(&'a self,_slot:usize,path:&[Step<'_>])->Option<Span<'a>>{self.span(path)}
}
/// Implemented by generated messages and by native scalar/collection types.
pub trait FilterValue {
    fn filter_read<'a>(&'a self, path: &[Step<'_>]) -> Result<Atom<'a>, FilterError>;
    fn filter_span<'a>(&'a self, _path: &[Step<'_>]) -> Option<Span<'a>> { None }
}
impl<T: FilterValue> Source for T {
    fn read<'a>(&'a self, path: &[Step<'_>]) -> Result<Atom<'a>, FilterError> { self.filter_read(path) }
    fn span<'a>(&'a self,path:&[Step<'_>])->Option<Span<'a>>{self.filter_span(path)}
}
#[derive(Clone,Copy,Debug,PartialEq,Eq)]
#[repr(u32)]
pub enum SpanKind { Signed=2, Unsigned=3, Float=4 }
/// Borrowed strided scalar column. Constructors require initialized, readable cells.
/// Memory bounds cannot be changed by safe code after construction.
///
/// ```compile_fail
/// use sdl_runtime::filter::FilterValue;
/// let value = 1_u8;
/// let mut span = value.filter_span(&[]).unwrap();
/// span.count = usize::MAX;
/// ```
///
/// ```compile_fail
/// use sdl_runtime::filter::FilterValue;
/// let value = 1_u8;
/// let mut span = value.filter_span(&[]).unwrap();
/// span.data = std::ptr::null();
/// span.stride = usize::MAX;
/// span.width = 8;
/// ```
#[derive(Clone,Copy)]
pub struct Span<'a>{data:*const u8,count:usize,stride:usize,kind:SpanKind,width:usize,big_endian:bool,quantum:Option<f64>,float32:bool,borrow:std::marker::PhantomData<&'a u8>}
impl<'a> Span<'a>{
    /// # Safety
    /// Every cell (including its entire width) must be initialized, readable and
    /// immutable for the span's lifetime. All offsets must fit within the same
    /// allocation and be valid for pointer arithmetic.
    pub unsafe fn new(data:*const u8,count:usize,stride:usize,kind:SpanKind,width:usize,big_endian:bool,quantum:Option<f64>,float32:bool)->Self{
        Self{data,count,stride,kind,width,big_endian,quantum,float32,borrow:std::marker::PhantomData}
    }
    #[inline] pub fn len(&self)->usize{self.count}
    #[inline] pub fn is_empty(&self)->bool{self.count==0}
    #[inline] pub fn as_ptr(&self)->*const u8{self.data}
    #[inline] pub fn stride(&self)->usize{self.stride}
    #[inline] pub fn kind(&self)->SpanKind{self.kind}
    #[inline] pub fn width(&self)->usize{self.width}
    #[inline] pub fn is_big_endian(&self)->bool{self.big_endian}
    #[inline] pub fn quantum(&self)->Option<f64>{self.quantum}
    #[inline] pub fn uses_float32_quantum(&self)->bool{self.float32}
    pub fn value(self,index:usize)->Result<Atom<'a>,FilterError>{
        if index>=self.count||!matches!(self.width,1|2|4|8)||self.data.is_null()
            ||self.kind==SpanKind::Float&&self.quantum.is_none()&&!matches!(self.width,4|8)
            ||self.quantum.is_some_and(|q|!q.is_finite()||q<=0.0){return Err(error("invalid scalar span"));}
        let offset=index.checked_mul(self.stride).ok_or_else(||error("span offset overflow"))?;
        let p=unsafe{self.data.add(offset)};
        let raw=unsafe{match self.width{1=>u64::from(*p),2=>u64::from(std::ptr::read_unaligned(p.cast::<u16>())),4=>u64::from(std::ptr::read_unaligned(p.cast::<u32>())),_=>std::ptr::read_unaligned(p.cast::<u64>())}};
        let raw=if self.big_endian&&cfg!(target_endian="little"){match self.width{2=>(raw as u16).swap_bytes() as u64,4=>(raw as u32).swap_bytes() as u64,8=>raw.swap_bytes(),_=>raw}}else{raw};
        let signed=match self.width{1=>raw as i8 as i64,2=>raw as i16 as i64,4=>raw as i32 as i64,_=>raw as i64};
        if let Some(q)=self.quantum{let f=signed as f64*q;return Ok(Atom::Float(if self.float32{f as f32 as f64}else{f}));}
        Ok(match self.kind{SpanKind::Signed=>Atom::Signed(signed),SpanKind::Unsigned=>Atom::Unsigned(raw),SpanKind::Float=>Atom::Float(if self.width==4{f32::from_bits(raw as u32) as f64}else{f64::from_bits(raw)})})
    }
    fn strided(mut self,count:usize,stride:usize)->Self{self.count=count;self.stride=stride;self}
}
fn leaf<'a>(path: &[Step<'_>], value: Atom<'a>) -> Result<Atom<'a>, FilterError> {
    if path.is_empty() { Ok(value) } else { Err(error("path continues beyond a scalar")) }
}
macro_rules! scalar {
    ($kind:ident,$cast:ty,$($t:ty),+) => {$(
        impl FilterValue for $t {
            fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{leaf(p,Atom::$kind(*self as $cast))}
            fn filter_span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{if !p.is_empty(){return None;}Some(unsafe{Span::new(self as *const $t as *const u8,1,std::mem::size_of::<$t>(),SpanKind::$kind,std::mem::size_of::<$t>(),false,None,false)})}
        }
    )+};
}
scalar!(Signed,i64,i8,i16,i32,i64);
scalar!(Unsigned,u64,u8,u16,u32,u64);
scalar!(Float,f64,f32,f64);
impl FilterValue for bool { fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{leaf(p,Atom::Bool(*self))} }
impl FilterValue for String { fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{leaf(p,Atom::String(self))} }
impl<T:FilterValue> FilterValue for Option<T> {
    fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{match self {Some(v)=>v.filter_read(p),None=>Ok(Atom::Missing)}}
}
fn sequence<'a,T:FilterValue>(items:&'a[T],p:&[Step<'_>])->Result<Atom<'a>,FilterError>{
    match p.split_first(){None=>Ok(Atom::Array(items.len())),Some((Step::Index(i),tail))=>match items.get(*i){Some(v)=>v.filter_read(tail),None=>Ok(Atom::Missing)},_=>Err(error("array requires an index"))}
}
fn sequence_span<'a,T:FilterValue>(items:&'a[T],p:&[Step<'_>])->Option<Span<'a>>{
    let (step,tail)=p.split_first()?;match step {Step::Index(i) if *i==usize::MAX=>{
        let first=items.first()?;let span=first.filter_span(tail)?;let base=first as *const T as usize;let cell=span.data as usize;
        if span.count!=1||cell<base||cell.checked_add(span.width)?>base.checked_add(std::mem::size_of::<T>())?{return None;}
        // A safe FilterValue implementation may expose a variant's payload.
        // Check every row before borrowing a uniform column, including presence.
        let offset=cell-base;
        for item in &items[1..]{
            let other=item.filter_span(tail)?;let base=item as *const T as usize;
            if other.count!=1||other.data as usize!=base.checked_add(offset)?||other.kind!=span.kind||other.width!=span.width
                ||other.big_endian!=span.big_endian||other.quantum!=span.quantum||other.float32!=span.float32{return None;}
        }
        Some(span.strided(items.len(),std::mem::size_of::<T>()))
    },Step::Index(i)=>items.get(*i)?.filter_span(tail),_=>None}
}
impl<T:FilterValue> FilterValue for Vec<T>{fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{sequence(self,p)}fn filter_span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{sequence_span(self,p)}}
impl<T:FilterValue,const N:usize> FilterValue for [T;N]{fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{sequence(self,p)}fn filter_span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{sequence_span(self,p)}}
macro_rules! complex {
    ($t:ty) => { impl FilterValue for $t {
        fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{match p {
            []=>Ok(Atom::Complex(self.real as f64,self.imag as f64)),
            [Step::Field{id:1,..}]=>Ok(Atom::Float(self.real as f64)),
            [Step::Field{id:2,..}]=>Ok(Atom::Float(self.imag as f64)),_=>Err(error("invalid complex component"))
        }}
        fn filter_span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{let component=match p{[Step::Field{id:1,..}]=>&self.real,[Step::Field{id:2,..}]=>&self.imag,_=>return None};component.filter_span(&[])}
    }};
}
complex!(Complex32); complex!(Complex64);

#[derive(Clone,Debug,PartialEq,Eq)]
enum Ty { Bool, Int, Float, String, Complex, Message(String), Array(Box<Ty>), Enum(String) }
impl Ty { fn numeric(&self)->bool {matches!(self,Self::Int|Self::Float)} }
#[derive(Clone,Debug,PartialEq,Eq)]
enum Part { Field {id:u32,owner:String,name:String}, Index(usize) }
#[derive(Clone,Debug)]
struct Path { base:Option<usize>, parts:Vec<Part>, ty:Ty }
#[derive(Clone,Debug)]
enum Literal { Bool(bool), Signed(i64), Unsigned(u64), Float(f64), String(String) }
#[derive(Clone,Debug)]
enum Op { Constant(Literal), Load(usize), Unary(String,usize), Binary(String,usize,usize), Function(String,usize), Quantifier{all:bool,scope:usize,array:usize,predicate:usize} }
#[derive(Clone,Debug)]
struct Node { op:Op, ty:Ty }

/// Immutable compiled predicate. Preparation and catalogue parsing are outside evaluation.
pub struct Filter { schema:SchemaDesc, root:String, wire_id:usize, fixed_wire_bits:Option<usize>, nodes:Vec<Node>, paths:Vec<Path>, entry:usize, fast:Option<FastPredicate>, wire:wire::Layout }
impl Filter {
    pub fn prepare(catalogue:&str,root:&str,expression:&str)->Result<Self,FilterError>{
        let schema=parse_descriptor(catalogue).map_err(|e|error(format!("invalid catalogue: {e:?}")))?;
        if !schema.messages.contains_key(root){return Err(error(format!("unknown root type {root}")));}
        let (nodes,paths,entry,_scopes)=parser::compile(&schema,root,expression)?;
        let mut names:Vec<_>=schema.messages.keys().collect();names.sort();
        let wire_id=names.iter().position(|n|n.as_str()==root).unwrap()+1;
        let mut wire=wire::Layout::prepare(&schema);
        wire.bind_paths(&schema,root,&paths);
        let fixed_wire_bits=wire.root_size(root);
        let fast=FastPredicate::prepare(&nodes,entry);
        Ok(Self{schema,root:root.into(),wire_id,fixed_wire_bits,nodes,paths,entry,fast,wire})
    }
    pub fn root_type(&self)->&str{&self.root}
    pub fn evaluate<S:Source+?Sized>(&self,source:&S)->FilterResult{
        let evaluated=if let Some(fast)=&self.fast{self.evaluate_fast(source,fast)}else{self.evaluate_general(source)};
        match evaluated{Ok(Atom::Bool(true))=>FilterResult::Match,Ok(Atom::Bool(false))=>FilterResult::NoMatch,
            Ok(Atom::Missing)=>FilterResult::Indeterminate,Ok(_)=>FilterResult::Error(error("predicate did not return bool")),Err(e)=>FilterResult::Error(e)}
    }
    #[inline(never)]
    fn evaluate_fast<'a,S:Source+?Sized>(&'a self,source:&'a S,fast:&FastPredicate)->Result<Atom<'a>,FilterError>{
        Numbers{filter:self,source,cache:[std::mem::MaybeUninit::uninit();MAX_LOADS],initialized:[0;2]}.predicate(fast)
    }
    #[inline(never)]
    fn evaluate_general<'a,S:Source+?Sized>(&'a self,source:&'a S)->Result<Atom<'a>,FilterError>{
        let mut eval=Evaluation{filter:self,source,cache:[std::mem::MaybeUninit::uninit();MAX_LOADS],epochs:[std::mem::MaybeUninit::uninit();MAX_LOADS],initialized:[0;2],epoch:1,
            indices:[std::mem::MaybeUninit::uninit();64],scope_paths:[std::mem::MaybeUninit::uninit();64],path:[std::mem::MaybeUninit::uninit();MAX_PATH]};
        eval.node(self.entry)
    }
    pub fn evaluate_wire(&self,data:&[u8])->FilterResult{
        match wire::WireSource::new(self,data){Ok(source)=>self.evaluate(&source),Err(e)=>FilterResult::Error(e)}
    }
}

enum FloatExpression { Constant(f64),Load(usize),Unary(bool,Box<Self>),Binary(u8,Box<Self>,Box<Self>) }
enum FloatInstruction {Constant(f64),Load(usize),Unary(bool,usize),Binary(u8,usize,usize),SelfBinary(u8,usize)}
enum SmallArithmetic {Pair(u8,usize,usize),SelfBinary(u8,usize),SumSquares(usize,usize)}
struct FloatProgram {code:Vec<FloatInstruction>,output:usize,small:Option<SmallArithmetic>}
impl FloatProgram{
    fn prepare(expression:FloatExpression)->Self{
        fn emit(e:FloatExpression,code:&mut Vec<FloatInstruction>,loads:&mut std::collections::HashMap<usize,usize>)->usize{
            let instruction=match e{FloatExpression::Constant(v)=>FloatInstruction::Constant(v),FloatExpression::Load(p)=>{if let Some(n)=loads.get(&p){return *n;}FloatInstruction::Load(p)},FloatExpression::Unary(sign,n)=>FloatInstruction::Unary(sign,emit(*n,code,loads)),FloatExpression::Binary(op,l,r)=>{
                if let (FloatExpression::Load(a),FloatExpression::Load(b))=(&*l,&*r){if a==b{FloatInstruction::SelfBinary(op,*a)}else{FloatInstruction::Binary(op,emit(*l,code,loads),emit(*r,code,loads))}}
                else{FloatInstruction::Binary(op,emit(*l,code,loads),emit(*r,code,loads))}
            }};
            let index=code.len();if let FloatInstruction::Load(p)=instruction{loads.insert(p,index);}code.push(instruction);index
        }
        let mut code=Vec::new();let output=emit(expression,&mut code,&mut std::collections::HashMap::new());
        let small=match code.as_slice(){
            [FloatInstruction::Load(a),FloatInstruction::Load(b),FloatInstruction::Binary(op,0,1)]=>Some(SmallArithmetic::Pair(*op,*a,*b)),
            [FloatInstruction::SelfBinary(op,a)]=>Some(SmallArithmetic::SelfBinary(*op,*a)),
            [FloatInstruction::SelfBinary(2,a),FloatInstruction::SelfBinary(2,b),FloatInstruction::Binary(0,0,1)]=>Some(SmallArithmetic::SumSquares(*a,*b)),
            _=>None,
        };Self{code,output,small}
    }
}
enum FastPredicate { Constant(bool),Compare(u8,FloatProgram,FloatProgram),Logical(bool,Box<Self>,Box<Self>),Not(Box<Self>) }
impl FloatExpression{
    fn constant(nodes:&[Node],id:usize)->Option<f64>{
        match &nodes[id].op{Op::Constant(Literal::Float(v))=>Some(*v),Op::Constant(Literal::Signed(v)) if v.unsigned_abs()<=1u64<<53=>Some(*v as f64),Op::Constant(Literal::Unsigned(v)) if *v<=1u64<<53=>Some(*v as f64),Op::Unary(op,n) if op=="+"||op=="-"=>Self::constant(nodes,*n).map(|v|if op=="-"{-v}else{v}),_=>None}
    }
    fn prepare(nodes:&[Node],id:usize)->Option<Self>{
        if let Some(v)=Self::constant(nodes,id){return Some(Self::Constant(v));}
        match &nodes[id].op{
            Op::Load(p) if nodes[id].ty.numeric()=>Some(Self::Load(*p)),
            Op::Unary(op,n) if nodes[id].ty==Ty::Float=>Some(Self::Unary(op=="-",Box::new(Self::prepare(nodes,*n)?))),
            Op::Binary(op,l,r) if nodes[id].ty==Ty::Float=>Some(Self::Binary(match op.as_str(){"+"=>0,"-"=>1,"*"=>2,"/"=>3,_=>return None},Box::new(Self::prepare(nodes,*l)?),Box::new(Self::prepare(nodes,*r)?))),_=>None,
        }
    }
}
impl FastPredicate{
    fn prepare(nodes:&[Node],id:usize)->Option<Self>{
        match &nodes[id].op{
            Op::Constant(Literal::Bool(v))=>Some(Self::Constant(*v)),
            Op::Unary(op,n) if op=="!"=>Some(Self::Not(Box::new(Self::prepare(nodes,*n)?))),
            Op::Binary(op,l,r) if op=="&&"||op=="||"=>Some(Self::Logical(op=="&&",Box::new(Self::prepare(nodes,*l)?),Box::new(Self::prepare(nodes,*r)?))),
            Op::Binary(op,l,r) if nodes[id].ty==Ty::Bool=>{
                let safe=(nodes[*l].ty==Ty::Float&&(nodes[*r].ty==Ty::Float||FloatExpression::constant(nodes,*r).is_some()))||(nodes[*r].ty==Ty::Float&&FloatExpression::constant(nodes,*l).is_some());
                if !safe{return None;}
                let code=match op.as_str(){"=="=>0,"!="=>1,"<"=>2,"<="=>3,">"=>4,">="=>5,_=>return None};
                Some(Self::Compare(code,FloatProgram::prepare(FloatExpression::prepare(nodes,*l)?),FloatProgram::prepare(FloatExpression::prepare(nodes,*r)?)))
            },_=>None,
        }
    }
}

struct Numbers<'a,S:Source+?Sized>{
    filter:&'a Filter,source:&'a S,cache:[std::mem::MaybeUninit<Option<f64>>;MAX_LOADS],initialized:[u64;2],
}
impl<'a,S:Source+?Sized> Numbers<'a,S>{
    fn load(&mut self,id:usize)->Result<Option<f64>,FilterError>{
        if self.initialized[id/64]&(1u64<<(id%64))!=0{return Ok(unsafe{self.cache[id].assume_init()});}
        let path=&self.filter.paths[id];
        if path.ty==Ty::Float{if let Some(value)=self.source.read_float_prepared(id){
            let value=value?;self.cache[id].write(value);self.initialized[id/64]|=1u64<<(id%64);return Ok(value);
        }}
        let value=match self.source.read_prepared(id){Some(v)=>v?,None=>{
            if path.parts.len()<=16{self.read_path::<16>(id)?}else{self.read_path::<MAX_PATH>(id)?}
        }};
        let value=normalize(&path.ty,value)?;
        let number=match value{Atom::Missing=>None,v=>Some(real(v).ok_or_else(||error("arithmetic requires numbers"))?)};
        self.cache[id].write(number);self.initialized[id/64]|=1u64<<(id%64);Ok(number)
    }
    #[inline(never)]
    fn read_path<const N:usize>(&self,id:usize)->Result<Atom<'a>,FilterError>{
        let parts=&self.filter.paths[id].parts;
        if parts.len()>N{return Err(error("field path is too deep"));}
        let mut steps=[std::mem::MaybeUninit::uninit();N];
        for (slot,part) in steps.iter_mut().zip(parts){slot.write(match part{Part::Index(i)=>Step::Index(*i),Part::Field{id,owner,name}=>Step::Field{id:*id,owner,name}});}
        self.source.read_slot(id,unsafe{std::slice::from_raw_parts(steps.as_ptr().cast(),parts.len())})
    }
    fn fast_number(&mut self,p:&FloatProgram)->Result<Option<f64>,FilterError>{
        if let Some(small)=&p.small{return match *small{
            SmallArithmetic::Pair(op,l,r)=>{let a=self.load(l)?;let b=self.load(r)?;match (a,b){(Some(a),Some(b))=>Ok(Some(float_binary(op,a,b)?)),_=>Ok(None)}},
            SmallArithmetic::SelfBinary(op,path)=>self.load(path)?.map(|v|float_binary(op,v,v)).transpose(),
            SmallArithmetic::SumSquares(l,r)=>{
                let a=self.load(l)?.map(|v|float_binary(2,v,v)).transpose()?;
                let b=self.load(r)?.map(|v|float_binary(2,v,v)).transpose()?;
                match (a,b){(Some(a),Some(b))=>Ok(Some(float_binary(0,a,b)?)),_=>Ok(None)}
            },
        };}
        match p.code.as_slice(){
            [FloatInstruction::Constant(v)]=>return Ok(Some(*v)),
            [FloatInstruction::Load(path)]=>return self.load(*path),
            _=>{}
        }
        if p.code.len()<=16{self.fast_program::<16>(p)}else{self.fast_program::<2048>(p)}
    }
    #[inline(never)]
    fn fast_program<const N:usize>(&mut self,p:&FloatProgram)->Result<Option<f64>,FilterError>{
        let mut registers=[std::mem::MaybeUninit::<Option<f64>>::uninit();N];
        for (i,op) in p.code.iter().enumerate(){let value=match op{
            FloatInstruction::Constant(v)=>Some(*v),FloatInstruction::Load(path)=>self.load(*path)?,
            FloatInstruction::SelfBinary(op,path)=>match self.load(*path)?{Some(a)=>Some(float_binary(*op,a,a)?),None=>None},
            FloatInstruction::Unary(negative,n)=>unsafe{registers[*n].assume_init()}.map(|v|if *negative{-v}else{v}),
            FloatInstruction::Binary(op,l,r)=>match (unsafe{registers[*l].assume_init()},unsafe{registers[*r].assume_init()}){(Some(a),Some(b))=>{
                Some(float_binary(*op,a,b)?)
            },_=>None},
        };registers[i].write(value);}
        Ok(unsafe{registers[p.output].assume_init()})
    }
    fn predicate(&mut self,p:&FastPredicate)->Result<Atom<'a>,FilterError>{
        match p{
            FastPredicate::Constant(v)=>Ok(Atom::Bool(*v)),FastPredicate::Not(p)=>unary("!",self.predicate(p)?),
            FastPredicate::Logical(and,l,r)=>{let a=self.predicate(l)?;if *and&&a==Atom::Bool(false)||!*and&&a==Atom::Bool(true){return Ok(a);}let b=self.predicate(r)?;binary(if *and{"&&"}else{"||"},a,b)},
            FastPredicate::Compare(op,l,r)=>{let a=self.fast_number(l)?;let b=self.fast_number(r)?;Ok(match (a,b){(Some(a),Some(b)) if !a.is_nan()&&!b.is_nan()=>Atom::Bool(match op{0=>a==b,1=>a!=b,2=>a<b,3=>a<=b,4=>a>b,_=>a>=b}),_=>Atom::Missing})}
        }
    }
}
fn float_binary(op:u8,a:f64,b:f64)->Result<f64,FilterError>{
    if op==3&&b==0.0{return Err(error("division by zero"));}
    let value=match op{0=>a+b,1=>a-b,2=>a*b,_=>a/b};
    if !value.is_finite(){return Err(error("non-finite arithmetic result"));}Ok(value)
}
struct Evaluation<'a,S:Source+?Sized> {
    filter:&'a Filter,source:&'a S,cache:[std::mem::MaybeUninit<Atom<'a>>;MAX_LOADS],epochs:[std::mem::MaybeUninit<usize>;MAX_LOADS],initialized:[u64;2],epoch:usize,
    indices:[std::mem::MaybeUninit<usize>;64],scope_paths:[std::mem::MaybeUninit<usize>;64],path:[std::mem::MaybeUninit<Step<'a>>;MAX_PATH],
}
impl<'a,S:Source+?Sized> Evaluation<'a,S>{
    fn append_path(&mut self,id:usize,used:&mut usize)->Result<(),FilterError>{
        let p=&self.filter.paths[id];
        if let Some(scope)=p.base{
            self.append_path(unsafe{self.scope_paths[scope].assume_init()},used)?;
            if *used>=MAX_PATH{return Err(error("field path is too deep"));}
            self.path[*used].write(Step::Index(unsafe{self.indices[scope].assume_init()}));*used+=1;
        }
        for part in &p.parts{
            if *used>=MAX_PATH{return Err(error("field path is too deep"));}
            self.path[*used].write(match part {Part::Index(i)=>Step::Index(*i),Part::Field{id,owner,name}=>Step::Field{id:*id,owner,name}});
            *used+=1;
        }Ok(())
    }
    fn load(&mut self,id:usize)->Result<Atom<'a>,FilterError>{
        let epoch=if self.filter.paths[id].base.is_none(){0}else{self.epoch};
        if self.initialized[id/64]&(1u64<<(id%64))!=0&&unsafe{self.epochs[id].assume_init()}==epoch{return Ok(unsafe{self.cache[id].assume_init()});}
        let mut used=0;self.append_path(id,&mut used)?;
        let path=unsafe{std::slice::from_raw_parts(self.path.as_ptr().cast::<Step<'a>>(),used)};
        let value=normalize(&self.filter.paths[id].ty,self.source.read_slot(id,path)?)?;
        self.cache[id].write(value);self.epochs[id].write(epoch);self.initialized[id/64]|=1u64<<(id%64);Ok(value)
    }
    fn node(&mut self,id:usize)->Result<Atom<'a>,FilterError>{
        match &self.filter.nodes[id].op {
            Op::Constant(v)=>Ok(match v{Literal::Bool(v)=>Atom::Bool(*v),Literal::Signed(v)=>Atom::Signed(*v),Literal::Unsigned(v)=>Atom::Unsigned(*v),Literal::Float(v)=>Atom::Float(*v),Literal::String(v)=>Atom::String(v)}),
            Op::Load(path)=>self.load(*path),
            Op::Unary(op,n)=>{let v=self.node(*n)?;unary(op,v)},
            Op::Function(name,n)=>{let v=self.node(*n)?;function(name,v)},
            Op::Binary(op,l,r)=>{
                let left=self.node(*l)?;
                if op=="&&"&&left==Atom::Bool(false){return Ok(left);}
                if op=="||"&&left==Atom::Bool(true){return Ok(left);}
                let right=self.node(*r)?;binary(op,left,right)
            },
            Op::Quantifier{all,scope,array,predicate}=>{
                let count=match self.node(*array)?{Atom::Missing=>return Ok(Atom::Missing),Atom::Array(n)=>n,_=>return Err(error("quantifier requires an array"))};
                let path=match self.filter.nodes[*array].op{Op::Load(p)=>p,_=>return Err(error("quantifier requires a field path"))};
                self.scope_paths[*scope].write(path);self.indices[*scope].write(0);
                if let Some(value)=self.column_quantifier(*predicate,*scope,*all,count)?{return Ok(value);}
                let mut missing=false;
                for i in 0..count{
                    self.indices[*scope].write(i);self.epoch=self.epoch.wrapping_add(1).max(1);
                    match self.node(*predicate)?{Atom::Bool(v) if v!=*all=>return Ok(Atom::Bool(v)),Atom::Missing=>missing=true,Atom::Bool(_)=>{},_=>return Err(error("quantifier predicate requires bool"))}
                }
                self.epoch=self.epoch.wrapping_add(1).max(1);
                Ok(if missing{Atom::Missing}else{Atom::Bool(*all)})
            }
        }
    }
    fn column_test(&mut self,id:usize,scope:usize)->Option<(Span<'a>,&'a str,Atom<'a>,bool)>{
        let (op,left,right)=match &self.filter.nodes[id].op{Op::Binary(op,l,r) if matches!(op.as_str(),"=="|"!="|"<"|"<="|">"|">=")=>(op.as_str(),*l,*r),_=>return None};
        let (load,constant,reverse)=if matches!(self.filter.nodes[left].op,Op::Constant(_)){(right,left,true)}else{(left,right,false)};
        let (path,projection)=match &self.filter.nodes[load].op{Op::Load(p)=>(*p,None),Op::Function(name,n) if matches!(name.as_str(),"real"|"imag")=>match self.filter.nodes[*n].op{Op::Load(p)=>(p,Some(if name=="real"{1}else{2})),_=>return None},_=>return None};
        if self.filter.paths[path].base!=Some(scope){return None;}
        // SIZE_MAX is the span wildcard, but an explicit query index of that
        // value must retain ordinary out-of-bounds/missing semantics.
        if self.filter.paths[path].parts.iter().any(|p|matches!(p,Part::Index(i) if *i==usize::MAX)){return None;}
        let literal=match &self.filter.nodes[constant].op{Op::Constant(v)=>match v{Literal::Signed(v)=>Atom::Signed(*v),Literal::Unsigned(v)=>Atom::Unsigned(*v),Literal::Float(v)=>Atom::Float(*v),_=>return None},_=>return None};
        let saved=unsafe{self.indices[scope].assume_init()};self.indices[scope].write(usize::MAX);let mut used=0;let prepared=self.append_path(path,&mut used).ok();self.indices[scope].write(saved);prepared?;
        if let Some(id)=projection{if used==MAX_PATH{return None;}self.path[used].write(Step::Field{id,owner:COMPLEX_OWNER,name:if id==1{"real"}else{"imag"}});used+=1;}
        let steps=unsafe{std::slice::from_raw_parts(self.path.as_ptr().cast::<Step<'a>>(),used)};
        self.source.span_slot(path,steps).and_then(|span|{
            let float=span.kind==SpanKind::Float||span.quantum.is_some();
            let expected_float=projection.is_some()||self.filter.paths[path].ty==Ty::Float;
            if float!=expected_float{return None;}Some((span,op,literal,reverse))
        })
    }
    fn column_quantifier(&mut self,predicate:usize,scope:usize,all:bool,count:usize)->Result<Option<Atom<'a>>,FilterError>{
        let (first,second,join)=match &self.filter.nodes[predicate].op{Op::Binary(op,l,r) if op=="&&"||op=="||"=>(*l,Some(*r),op.as_str()),_=>(predicate,None,"")};
        let a=match self.column_test(first,scope){Some(a) if a.0.count==count=>a,_=>return Ok(None)};
        let b=match second{Some(id)=>match self.column_test(id,scope){Some(b) if b.0.count==count=>Some(b),_=>return Ok(None)},None=>None};
        let mut missing=false;
        for i in 0..count{
            let value=a.0.value(i)?;let mut result=if a.3{binary(a.1,a.2,value)?}else{binary(a.1,value,a.2)?};
            if let Some(b)=b{if !(join=="&&"&&result==Atom::Bool(false)||join=="||"&&result==Atom::Bool(true)){
                let value=b.0.value(i)?;let right=if b.3{binary(b.1,b.2,value)?}else{binary(b.1,value,b.2)?};result=binary(join,result,right)?;
            }}
            match result{Atom::Bool(v) if v!=all=>return Ok(Some(Atom::Bool(v))),Atom::Missing=>missing=true,_=>{}}
        }
        Ok(Some(if missing{Atom::Missing}else{Atom::Bool(all)}))
    }
}
fn normalize<'a>(ty:&Ty,value:Atom<'a>)->Result<Atom<'a>,FilterError>{
    let value=if *ty==Ty::Float{match value{Atom::Signed(v)=>Atom::Float(v as f64),Atom::Unsigned(v)=>Atom::Float(v as f64),v=>v}}else{value};
    if valid_atom(ty,value){Ok(value)}else{Err(error("source value does not match catalogue type"))}
}
fn valid_atom(ty:&Ty,v:Atom<'_>)->bool{
    matches!(v,Atom::Missing)||match ty {Ty::Bool=>matches!(v,Atom::Bool(_)),Ty::Int|Ty::Enum(_)=>matches!(v,Atom::Signed(_)|Atom::Unsigned(_)),Ty::Float=>matches!(v,Atom::Float(_)),Ty::String=>matches!(v,Atom::String(_)),Ty::Complex=>matches!(v,Atom::Complex(..)),Ty::Message(_)=>matches!(v,Atom::Message),Ty::Array(_)=>matches!(v,Atom::Array(_))}
}
fn integer(v:Atom<'_>)->Option<i128>{match v{Atom::Signed(v)=>Some(v as i128),Atom::Unsigned(v)=>Some(v as i128),_=>None}}
fn real(v:Atom<'_>)->Option<f64>{match v{Atom::Float(v)=>Some(v),Atom::Signed(v)=>Some(v as f64),Atom::Unsigned(v)=>Some(v as f64),_=>None}}
fn checked_integer<'a>(n:i128)->Result<Atom<'a>,FilterError>{
    if n<i64::MIN as i128||n>u64::MAX as i128{Err(error("integer arithmetic overflow"))}else if n<0{Ok(Atom::Signed(n as i64))}else{Ok(Atom::Unsigned(n as u64))}
}
fn unary<'a>(op:&str,v:Atom<'a>)->Result<Atom<'a>,FilterError>{
    if v==Atom::Missing{return Ok(v);}
    match (op,v){("!",Atom::Bool(b))=>Ok(Atom::Bool(!b)),("+",_)=>Ok(v),("-",Atom::Float(f))=>Ok(Atom::Float(-f)),("-",_)=>checked_integer(-integer(v).ok_or_else(||error("unary minus requires a number"))?),_=>Err(error("invalid unary operation"))}
}
fn function<'a>(name:&str,v:Atom<'a>)->Result<Atom<'a>,FilterError>{
    if name=="exists"{return Ok(Atom::Bool(v!=Atom::Missing));}
    if v==Atom::Missing{return Ok(v);}
    match (name,v){("len",Atom::Array(n))=>Ok(Atom::Unsigned(n as u64)),("len",Atom::String(s))=>Ok(Atom::Unsigned(s.chars().count() as u64)),("abs",Atom::Float(f))=>Ok(Atom::Float(f.abs())),("abs",Atom::Complex(r,i))=>Ok(Atom::Float(r.hypot(i))),
        ("abs",_)=>checked_integer(integer(v).ok_or_else(||error("abs requires a number"))?.abs()),("real",Atom::Complex(r,_))=>Ok(Atom::Float(r)),("imag",Atom::Complex(_,i))=>Ok(Atom::Float(i)),("isfinite",_)=>Ok(Atom::Bool(real(v).ok_or_else(||error("isfinite requires a number"))?.is_finite())),_=>Err(error("invalid function argument"))}
}
fn mixed_cmp(n:i128,f:f64)->Option<Ordering>{
    if f.is_nan(){return None;}if f>=18446744073709551616.0{return Some(Ordering::Less);}if f< i64::MIN as f64{return Some(Ordering::Greater);}
    let whole=f.trunc() as i128;Some(match n.cmp(&whole){Ordering::Equal=>0.0.partial_cmp(&f.fract())?,other=>other})
}
fn compare(l:Atom<'_>,r:Atom<'_>)->Result<Option<Ordering>,FilterError>{
    Ok(match (l,r){(Atom::Bool(a),Atom::Bool(b))=>Some(a.cmp(&b)),(Atom::String(a),Atom::String(b))=>Some(a.cmp(b)),(Atom::Float(a),Atom::Float(b))=>a.partial_cmp(&b),
        (Atom::Float(a),b)=>mixed_cmp(integer(b).ok_or_else(||error("incompatible comparison"))?,a).map(Ordering::reverse),
        (a,Atom::Float(b))=>mixed_cmp(integer(a).ok_or_else(||error("incompatible comparison"))?,b),
        (a,b)=>Some(integer(a).ok_or_else(||error("incompatible comparison"))?.cmp(&integer(b).ok_or_else(||error("incompatible comparison"))?))})
}
fn binary<'a>(op:&str,l:Atom<'a>,r:Atom<'a>)->Result<Atom<'a>,FilterError>{
    if op=="&&"{return Ok(if l==Atom::Bool(false)||r==Atom::Bool(false){Atom::Bool(false)}else if l==Atom::Missing||r==Atom::Missing{Atom::Missing}else{Atom::Bool(true)});}
    if op=="||"{return Ok(if l==Atom::Bool(true)||r==Atom::Bool(true){Atom::Bool(true)}else if l==Atom::Missing||r==Atom::Missing{Atom::Missing}else{Atom::Bool(false)});}
    if l==Atom::Missing||r==Atom::Missing{return Ok(Atom::Missing);}
    if matches!(op,"=="|"!="|"<"|"<="|">"|">="){
        return Ok(match compare(l,r)?{None=>Atom::Missing,Some(c)=>Atom::Bool(match op{"=="=>c==Ordering::Equal,"!="=>c!=Ordering::Equal,"<"=>c==Ordering::Less,"<="=>c!=Ordering::Greater,">"=>c==Ordering::Greater,_=>c!=Ordering::Less})});
    }
    if op!="/"{if let (Some(a),Some(b))=(integer(l),integer(r)){
        let value=match op{"+"=>a.checked_add(b),"-"=>a.checked_sub(b),"*"=>a.checked_mul(b),"%" if b!=0=>a.checked_rem(b),"%"=>return Err(error("division by zero")),_=>None}.ok_or_else(||error("integer arithmetic overflow"))?;
        return checked_integer(value);
    }}
    let a=real(l).ok_or_else(||error("arithmetic requires numbers"))?;let b=real(r).ok_or_else(||error("arithmetic requires numbers"))?;
    if op=="/"&&b==0.0{return Err(error("division by zero"));}
    let value=match op{"+"=>a+b,"-"=>a-b,"*"=>a*b,"/"=>a/b,_=>return Err(error("invalid arithmetic operator"))};
    if !value.is_finite(){return Err(error("non-finite arithmetic result"));}Ok(Atom::Float(value))
}
