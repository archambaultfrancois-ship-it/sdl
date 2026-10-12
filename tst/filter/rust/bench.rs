#[allow(dead_code)]
#[path="../../../build/filter/generated/rust/mod.rs"]mod generated;
use generated::telemetry::model::{Vector,Sample};
use sdl_runtime::{SdlMessage,prepare,encode,decode};
use sdl_runtime::filter::Filter;
use std::hint::black_box;
fn time(mut action:impl FnMut(),n:usize)->f64{
    for _ in 0..1000{action();}
    let mut trials=[0.;5];for trial in &mut trials{let start=std::time::Instant::now();for _ in 0..n{action();}*trial=start.elapsed().as_secs_f64()*1e9/n as f64;}
    trials.sort_by(f64::total_cmp);trials[2]
}
fn bench<T:SdlMessage+sdl_runtime::filter::FilterValue>(catalogue:&str,value:&T,label:&str,query:&str,n:usize){
    let context=prepare(catalogue,&[T::type_info()]).unwrap();let wire=encode(&context,value).unwrap();let filter=Filter::prepare(catalogue,T::NAME,query).unwrap();
    assert_eq!(filter.evaluate(value).code(),1);assert_eq!(filter.evaluate_wire(&wire).code(),1);
    let e=time(||{black_box(encode(black_box(&context),black_box(value)).unwrap());},n);
    let d=time(||{black_box(decode::<T>(black_box(&context),black_box(&wire)).unwrap());},n);
    let o=time(||{black_box(filter.evaluate(black_box(value)));},n);let w=time(||{black_box(filter.evaluate_wire(black_box(&wire)));},n);
    println!("BENCH Rust {label} {e:.1} {d:.1} {o:.1} {w:.1}");
}
fn main(){
    let catalogue="SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}";let value=Vector{x:3.0,y:4.0};
    for (label,query) in [("scalar","x>0"),("arithmetic","x*x+y*y<=25")]{bench(catalogue,&value,label,query,100000);
    }
    let sample=Sample{temperature:12.34,position:value.clone(),points:vec![value;256],..Default::default()};
    bench(Sample::DESCRIPTOR,&sample,"collection256","all(points,p=>p.x>=0&&p.y>=0)",10000);
}
