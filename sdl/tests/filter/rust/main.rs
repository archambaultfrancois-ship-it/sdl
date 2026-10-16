#[path="../../../build/filter/generated/rust/mod.rs"] mod generated;
include!("../../../build/filter/cases.rs");
use generated::telemetry::model::{Sample,Vector,Detail,State};
use sdl_runtime::{SdlMessage,Complex32,description,prepare,encode};
use sdl_runtime::filter::Filter;
fn main(){
    let mut value=Sample{sequence:u64::MAX,temperature:12.34,active:true,detail:Some(Detail{code:0,label:"ok".into()}),position:Vector{x:3.0,y:4.0},points:vec![Vector{x:1.0,y:2.0},Vector{x:10.0,y:20.0},Vector{x:-1.0,y:0.5}],matrix:[[1,2,3],[4,5,6]],label:"café".into(),counts:vec![-2,0,7],samples:vec![Complex32{real:1.0,imag:2.0},Complex32{real:-3.0,imag:0.5}],state:State::On};
    let types=[Sample::type_info()];let catalogue=description(&types).unwrap();let context=prepare(&catalogue,&types).unwrap();let wire=encode(&context,&value).unwrap();assert_eq!(wire,std::fs::read("build/filter/golden.bin").unwrap());
    for (expression,a,b) in CASES {let filter=Filter::prepare(&catalogue,Sample::NAME,expression).unwrap();assert_eq!(filter.evaluate(&value).code(),*a,"{expression}");assert_eq!(filter.evaluate_wire(&wire).code(),*b,"{expression}");}
    value.detail=None;value.points.clear();
    for expression in ["!exists(detail)","all(points,p=>false)","!any(points,p=>true)"]{assert_eq!(Filter::prepare(&catalogue,Sample::NAME,expression).unwrap().evaluate(&value).code(),1);}
    println!("Rust filters and wire oracle passed.");
}
