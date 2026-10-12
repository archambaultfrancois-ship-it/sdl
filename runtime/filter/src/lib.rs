//! Stable C gateway to the common predicate engine.
pub use sdl_runtime::filter::*;
use std::cell::RefCell;
use std::ffi::{c_void,CString};

thread_local! {static LAST_ERROR:RefCell<CString>=RefCell::new(CString::new("").unwrap());}
fn report(text:String)->i32 {LAST_ERROR.with(|s|*s.borrow_mut()=CString::new(text.replace('\0',"\\0")).unwrap());-1}
#[repr(C)]
#[derive(Clone,Copy)]
pub struct Bytes { pub data:*const u8,pub size:usize }
#[repr(C)]
#[derive(Clone,Copy)]
pub struct CStep {pub kind:u32,pub id:u32,pub index:usize,pub owner:Bytes,pub name:Bytes}
#[repr(C)]
#[derive(Clone,Copy)]
pub struct Value {pub kind:u32,pub reserved:u32,pub signed:i64,pub unsigned:u64,pub real:f64,pub imag:f64,pub bytes:Bytes,pub count:usize}
pub type Read=unsafe extern "C" fn(*mut c_void,*const CStep,usize,u32,*mut Value)->i32;
#[repr(C)]
pub struct CSpan {pub data:*const u8,pub count:usize,pub stride:usize,pub kind:u32,pub width:u32,pub flags:u32,pub reserved:u32,pub quantum:f64}
pub type ReadSpan=unsafe extern "C" fn(*mut c_void,*const CStep,usize,u32,*mut CSpan)->i32;
unsafe fn bytes<'a>(b:Bytes)->Result<&'a[u8],FilterError>{if b.size==0{return Ok(&[]);}if b.data.is_null()||b.size>isize::MAX as usize{return Err(FilterError("invalid input slice".into()));}Ok(std::slice::from_raw_parts(b.data,b.size))}
unsafe fn text<'a>(b:Bytes)->Result<&'a str,FilterError>{std::str::from_utf8(bytes(b)?).map_err(|_|FilterError("invalid UTF-8".into()))}
struct Foreign {read:Read,user:*mut c_void,span:Option<ReadSpan>}
fn with_steps<R>(path:&[Step<'_>],call:impl FnOnce(*const CStep)->R)->R{
    if path.len()<=16{steps::<R,16>(path,call)}else{steps::<R,MAX_PATH>(path,call)}
}
#[inline(never)]
fn steps<R,const N:usize>(path:&[Step<'_>],call:impl FnOnce(*const CStep)->R)->R{
    assert!(path.len()<=N);
    let blank=Bytes{data:std::ptr::null(),size:0};
    let mut steps:[std::mem::MaybeUninit<CStep>;N]=[std::mem::MaybeUninit::uninit();N];
    for (target,step) in steps.iter_mut().zip(path){target.write(match step{Step::Index(i)=>CStep{kind:1,index:*i,id:0,owner:blank,name:blank},Step::Field{id,owner,name}=>CStep{kind:0,id:*id,index:0,owner:Bytes{data:owner.as_ptr(),size:owner.len()},name:Bytes{data:name.as_ptr(),size:name.len()}}});}
    call(steps.as_ptr().cast())
}
impl Source for Foreign {
    fn read<'a>(&'a self,path:&[Step<'_>])->Result<Atom<'a>,FilterError>{
        self.read_slot(0,path)
    }
    fn read_slot<'a>(&'a self,slot:usize,path:&[Step<'_>])->Result<Atom<'a>,FilterError>{
        let blank=Bytes{data:std::ptr::null(),size:0};
        let mut value=Value{kind:0,reserved:0,signed:0,unsigned:0,real:0.0,imag:0.0,bytes:blank,count:0};
        if with_steps(path,|steps|unsafe{(self.read)(self.user,steps,path.len(),slot as u32,&mut value)})!=0{return Err(FilterError("object field accessor failed".into()));}
        Ok(match value.kind{0=>Atom::Missing,1=>Atom::Bool(value.unsigned!=0),2=>Atom::Signed(value.signed),3=>Atom::Unsigned(value.unsigned),4=>Atom::Float(value.real),5=>Atom::String(unsafe{text(value.bytes)?}),6=>Atom::Array(value.count),7=>Atom::Message,8=>Atom::Complex(value.real,value.imag),_=>return Err(FilterError("invalid accessor value kind".into()))})
    }
    fn span_slot<'a>(&'a self,slot:usize,path:&[Step<'_>])->Option<Span<'a>>{
        let reader=self.span?;
        let mut span=CSpan{data:std::ptr::null(),count:0,stride:0,kind:0,width:0,flags:0,reserved:0,quantum:0.0};
        if with_steps(path,|steps|unsafe{reader(self.user,steps,path.len(),slot as u32,&mut span)})!=0{return None;}
        let kind=match span.kind{2=>SpanKind::Signed,3=>SpanKind::Unsigned,4=>SpanKind::Float,_=>return None};
        if !matches!(span.width,1|2|4|8)||span.data.is_null()||span.count.checked_mul(span.stride).is_none(){return None;}
        Some(unsafe{Span::new(span.data,span.count,span.stride,kind,span.width as usize,span.flags&1!=0,if span.flags&2!=0{Some(span.quantum)}else{None},span.flags&4!=0)})
    }
}
fn result(value:FilterResult)->i32{match value{FilterResult::Error(e)=>report(e.0),value=>value.code()}}
#[no_mangle] pub extern "C" fn sdl_filter_abi_version()->u32{1}
#[no_mangle] pub extern "C" fn sdl_filter_error()->*const std::ffi::c_char{LAST_ERROR.with(|s|s.borrow().as_ptr())}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_failure(diagnostic:Bytes)->i32{
    match text(diagnostic){Ok(s)=>report(s.into()),Err(e)=>report(e.0)}
}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_prepare(catalogue:Bytes,root:Bytes,expression:Bytes)->*mut Filter{
    match text(catalogue).and_then(|c|text(root).and_then(|r|text(expression).and_then(|e|Filter::prepare(c,r,e)))){Ok(p)=>Box::into_raw(Box::new(p)),Err(e)=>{report(e.0);std::ptr::null_mut()}}
}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_free(p:*mut Filter){if !p.is_null(){drop(Box::from_raw(p));}}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_root(p:*const Filter)->Bytes{
    if p.is_null(){return Bytes{data:std::ptr::null(),size:0};}let root=(*p).root_type();Bytes{data:root.as_ptr(),size:root.len()}
}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_evaluate_wire(p:*const Filter,data:Bytes)->i32{
    if p.is_null(){return report("null filter".into());}match bytes(data){Ok(b)=>result((*p).evaluate_wire(b)),Err(e)=>report(e.0)}
}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_evaluate_object(p:*const Filter,read:Option<Read>,user:*mut c_void)->i32{
    if p.is_null()||read.is_none(){return report("null filter or accessor".into());}result((*p).evaluate(&Foreign{read:read.unwrap(),user,span:None}))
}
#[no_mangle] pub unsafe extern "C" fn sdl_filter_evaluate_object_spans(p:*const Filter,read:Option<Read>,span:Option<ReadSpan>,user:*mut c_void)->i32{
    if p.is_null()||read.is_none(){return report("null filter or accessor".into());}result((*p).evaluate(&Foreign{read:read.unwrap(),user,span}))
}
