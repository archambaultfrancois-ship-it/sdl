use sdl_filter::{Atom,Filter,FilterError,FilterResult,Source,Step};
const CATALOGUE:&str="SDL2|Point{1:int32:x;2:int32:y;}|Root{1?:Point:position;2+:Point:points;3:uint64:sequence;4:fl64:temperature@b16,q0.1;}";
struct Object { present:bool }
impl Source for Object {
    fn read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{
        let ids:Vec<_>=p.iter().map(|s|match s{Step::Field{id,..}=>*id as usize,Step::Index(i)=>*i}).collect();
        Ok(match ids.as_slice(){[1] if !self.present=>Atom::Missing,[1]=>Atom::Message,[1,1] if !self.present=>Atom::Missing,[1,1]=>Atom::Signed(3),[1,2]=>Atom::Signed(4),[2]=>Atom::Array(3),[2,i,_] if *i>=3=>Atom::Missing,[2,i,1]=>Atom::Signed(*i as i64-1),[2,_,2]=>Atom::Signed(4),[3]=>Atom::Unsigned(u64::MAX),[4]=>Atom::Float(12.34),_=>Atom::Missing})
    }
}
fn evaluate(expression:&str)->FilterResult{Filter::prepare(CATALOGUE,"Root",expression).unwrap().evaluate(&Object{present:true})}
#[test]fn scalar_arithmetic_and_exact_integer_comparison(){
    for text in ["true","position.x * position.x + position.y * position.y == 25","sequence == 18446744073709551615","sequence < 18446744073709551616.0","sequence != 18446744073709551616.0","-9223372036854775808 < 0","position.x / 2 == 1.5","position.y % 3 == 1"] {assert_eq!(evaluate(text),FilterResult::Match,"{text}");}
    assert!(matches!(evaluate("sequence+1>0"),FilterResult::Error(_)));
    assert!(matches!(evaluate("1/0>0"),FilterResult::Error(_)));
}
#[test]fn optional_truth_tables_and_short_circuit(){
    let absent=Object{present:false};
    for (text,status) in [("position.x>0",FilterResult::Indeterminate),("exists(position)&&position.x>0",FilterResult::NoMatch),("position.x>0||true",FilterResult::Match),("position.x>0&&false",FilterResult::NoMatch),("true||1/0>0",FilterResult::Match),("false&&1/0>0",FilterResult::NoMatch)]{assert_eq!(Filter::prepare(CATALOGUE,"Root",text).unwrap().evaluate(&absent),status,"{text}");}
}
#[test]fn quantified_collections_and_nested_scopes(){
    for text in ["any(points,p=>p.x>0)","all(points,p=>p.y==4)","any(points,p=>any(points,q=>p.x+q.x==2))","len(points)==3","points[2].x==1"] {assert_eq!(evaluate(text),FilterResult::Match,"{text}");}
    assert_eq!(evaluate("all(points,p=>p.x>0)"),FilterResult::NoMatch);
    assert_eq!(evaluate("points[3].x>0"),FilterResult::Indeterminate);
}
#[test]fn malformed_and_mistyped_expressions(){
    for text in ["","position","unknown==1","points.x>0","position.x&&true","any(position,p=>true)","abs(true)>0","state==0","1+","1e","'missing","😀","position.x>=","sequence==18446744073709551616"]{assert!(Filter::prepare(CATALOGUE,"Root",text).is_err(),"{text}");}
}
#[test]fn quantized_wire_values_are_reconstructed_locally(){
    let filter=Filter::prepare("SDL2|Sample{1:fl64:temperature@b16,q0.1;2:bool:active@b1;}","Sample","temperature>12.32").unwrap();
    assert_eq!(filter.evaluate_wire(&[1,0,123,128]),FilterResult::NoMatch);
    let filter=Filter::prepare("SDL2|Sample{1:fl64:temperature@b16,q0.1;2:bool:active@b1;}","Sample","temperature==12.3&&active").unwrap();
    assert_eq!(filter.evaluate_wire(&[1,0,123,128]),FilterResult::Match);
    assert!(matches!(filter.evaluate_wire(&[1,0]),FilterResult::Error(_)));
    assert!(matches!(filter.evaluate_wire(&[2,0,123,128]),FilterResult::Error(_)));
}
#[test]fn enum_strings_complex_and_nonfinite_values(){
    let c="SDL2|Root{1:State:state;2:string:label;3:c32:value;}|#State{OFF=0;ON=1;}";
    struct Values;
    impl Source for Values{fn read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{Ok(match p { [Step::Field{id:1,..}]=>Atom::Signed(1),[Step::Field{id:2,..}]=>Atom::String("café"),[Step::Field{id:3,..}]=>Atom::Complex(3.0,4.0),[Step::Field{id:3,..},Step::Field{id:1,..}]=>Atom::Float(3.0),_=>Atom::Missing})}}
    for text in ["state==State.ON","state==.State.ON","label=='café'&&len(label)==4","abs(value)==5","real(value)==3&&imag(value)==4","value.real==3"]{assert_eq!(Filter::prepare(c,"Root",text).unwrap().evaluate(&Values),FilterResult::Match,"{text}");}
}
