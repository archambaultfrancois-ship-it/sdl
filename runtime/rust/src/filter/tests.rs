use super::*;

struct Floats { x: Atom<'static>, y: Atom<'static> }
impl Source for Floats {
    fn read<'a>(&'a self, p: &[Step<'_>]) -> Result<Atom<'a>, FilterError> {
        Ok(match p { [Step::Field { id: 1, .. }] => self.x,
                     [Step::Field { id: 2, .. }] => self.y, _ => Atom::Missing })
    }
}

#[test]
fn numeric_program_and_reference_agree_on_missing_errors_and_ieee_values() {
    let catalogue = "SDL2|Root{1:fl64:x;2:fl64:y;}";
    let values = [Atom::Missing, Atom::Float(0.), Atom::Float(-0.), Atom::Float(3.),
        Atom::Float(-4.), Atom::Float(f64::MAX), Atom::Float(f64::NAN),
        Atom::Float(f64::INFINITY), Atom::Float(f64::NEG_INFINITY),
        Atom::Signed(3), Atom::Unsigned(u64::MAX), Atom::Bool(true)];
    for expression in ["x>0", "x==y", "x*x+y*y<=25", "x/y>1", "-x<=y",
        "x>0&&y/y>0", "x>0||y/y>0", "!(x>y)", "x+y==y+x", "x+0.0>1"] {
        let fast = Filter::prepare(catalogue, "Root", expression).unwrap();
        assert!(fast.fast.is_some(), "{expression}");
        let mut reference = Filter::prepare(catalogue, "Root", expression).unwrap();
        reference.fast = None;
        for x in values { for y in values {
            let input = Floats { x, y };
            assert_eq!(fast.evaluate(&input), reference.evaluate(&input), "{expression}: {x:?}, {y:?}");
        }}
    }
}

struct NoAcceleration<'a, S>(&'a S);
impl<S: Source> Source for NoAcceleration<'_, S> {
    fn read<'a>(&'a self, p: &[Step<'_>]) -> Result<Atom<'a>, FilterError> { self.0.read(p) }
}

#[test]
fn wire_columns_match_scalar_reads_for_quantization_complex_and_unsigned() {
    let catalogue = "SDL2|Point{1:fl32:x@b16,q0.1;2:uint64:id;}|Root{1+:Point:points;2+:c32:samples;3:int32[2][3]:matrix;}";
    let mut wire = vec![2, 3];
    for (x, id) in [(12_i16, 0_u64), (-23, 1), (456, u64::MAX)] {
        wire.extend_from_slice(&x.to_be_bytes()); wire.extend_from_slice(&id.to_be_bytes());
    }
    wire.push(2);
    for (r, i) in [(3_f32, 4_f32), (f32::NAN, 0_f32)] {
        wire.extend_from_slice(&r.to_be_bytes()); wire.extend_from_slice(&i.to_be_bytes());
    }
    for value in [1_i32, 2, 3, 4, 5, 6] { wire.extend_from_slice(&value.to_be_bytes()); }
    for expression in ["all(points,p=>p.x>=0)", "any(points,p=>p.x>=0&&p.x<2)",
        "any(points,p=>p.id==18446744073709551615)", "all(points,p=>p.id<18446744073709551615)",
        "all(samples,p=>real(p)>0)", "any(samples,p=>p.imag>3)",
        "all(matrix[1],p=>p>=4)", "any(points,p=>all(matrix[1],q=>p.x<q))"] {
        let filter = Filter::prepare(catalogue, "Root", expression).unwrap();
        let source = wire::WireSource::new(&filter, &wire).unwrap();
        assert_eq!(filter.evaluate(&source), filter.evaluate(&NoAcceleration(&source)), "{expression}");
    }
}

#[test]
fn truncated_unused_columns_keep_short_circuit_semantics() {
    let catalogue = "SDL2|Point{1:fl64:x;}|Root{1+:Point:points;}";
    let mut wire = vec![2, 3]; wire.extend_from_slice(&0_f64.to_be_bytes());
    let filter = Filter::prepare(catalogue, "Root", "all(points,p=>p.x>0)").unwrap();
    assert_eq!(filter.evaluate_wire(&wire), FilterResult::NoMatch);
    let filter = Filter::prepare(catalogue, "Root", "any(points,p=>p.x>0)").unwrap();
    assert!(matches!(filter.evaluate_wire(&wire), FilterResult::Error(_)));
    let catalogue = "SDL2|Root{1+:c32:samples;}";
    let mut wire = vec![1, 1];wire.extend_from_slice(&3_f32.to_be_bytes());
    let filter = Filter::prepare(catalogue, "Root", "any(samples,s=>s.real>0)").unwrap();
    let source = wire::WireSource::new(&filter, &wire).unwrap();
    assert_eq!(filter.evaluate(&source),FilterResult::Match);
    assert_eq!(filter.evaluate(&NoAcceleration(&source)),FilterResult::Match);
    let filter = Filter::prepare(catalogue, "Root", "any(samples,s=>real(s)>0)").unwrap();
    let source = wire::WireSource::new(&filter, &wire).unwrap();
    assert!(matches!(filter.evaluate(&source),FilterResult::Error(_)));
    assert_eq!(filter.evaluate(&source),filter.evaluate(&NoAcceleration(&source)));
}

#[test]
fn variable_length_columns_and_nested_scopes_do_not_reuse_wrong_positions() {
    let catalogue = "SDL2|Root{1*:int16:values@z;}";
    // Zigzag -1, 0, 64 and -65, including two multibyte values.
    let wire = [1, 4, 1, 0, 128, 1, 129, 1];
    for expression in ["values[3]==-65&&values[1]==0", "any(values,p=>p==64)",
        "all(values,p=>p<65)", "all(values,p=>any(values,q=>q==p))"] {
        assert_eq!(Filter::prepare(catalogue, "Root", expression).unwrap().evaluate_wire(&wire), FilterResult::Match, "{expression}");
    }
    assert!(matches!(Filter::prepare(catalogue, "Root", "values[2]==64").unwrap()
        .evaluate_wire(&[1, 4, 1, 0, 128]), FilterResult::Error(_)));
}

#[test]
fn byte_order_and_quantized_fixed_offsets_agree_with_general_reader() {
    let catalogue = "SDL2|Root{1:fl64:x;2:fl32:y@b16,q0.1;}";
    for x in [0., -0., 3., f64::MAX, f64::INFINITY, f64::NAN] {
        for y in [0_i16, -1, 123, i16::MIN, i16::MAX] {
            let mut wire = vec![1]; wire.extend_from_slice(&x.to_be_bytes()); wire.extend_from_slice(&y.to_be_bytes());
            for expression in ["x>y", "x+y>0", "x/y>1", "y<0||x==0"] {
                let filter = Filter::prepare(catalogue, "Root", expression).unwrap();
                let source = wire::WireSource::new(&filter, &wire).unwrap();
                assert_eq!(filter.evaluate(&source), filter.evaluate(&NoAcceleration(&source)), "{expression}: {x}, {y}");
            }
        }
    }
}

#[test]
fn strided_native_columns_never_follow_allocated_child_buffers() {
    let rows = vec![vec![1_f64], vec![2_f64]];
    let path = [Step::Index(usize::MAX), Step::Index(0)];
    assert!(rows.filter_span(&path).is_none());
    let rows = [[1_f64, 2.], [3., 4.]];
    let span = rows.filter_span(&[Step::Index(usize::MAX), Step::Index(1)]).unwrap();
    assert_eq!(span.value(0).unwrap(), Atom::Float(2.));
    assert_eq!(span.value(1).unwrap(), Atom::Float(4.));
    assert!(span.value(2).is_err());
    enum Variant { Present(f64), Absent }
    impl FilterValue for Variant {
        fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{
            match self{Self::Present(v)=>v.filter_read(p),Self::Absent=>Ok(Atom::Missing)}
        }
        fn filter_span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{
            match self{Self::Present(v)=>v.filter_span(p),Self::Absent=>None}
        }
    }
    let rows=[Variant::Present(1.),Variant::Absent];
    assert!(rows.filter_span(&[Step::Index(usize::MAX)]).is_none());
}

#[test]
fn explicit_maximum_index_is_missing_even_inside_a_column_predicate() {
    struct Row([f64;1]);
    impl FilterValue for Row {
        fn filter_read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{
            if p.is_empty(){Ok(Atom::Message)}else{self.0.filter_read(&p[1..])}
        }
        fn filter_span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{
            if p.is_empty(){None}else{self.0.filter_span(&p[1..])}
        }
    }
    struct Rows(Vec<Row>);
    impl Source for Rows {
        fn read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{self.0.filter_read(&p[1..])}
        fn span<'a>(&'a self,p:&[Step<'_>])->Option<Span<'a>>{self.0.filter_span(&p[1..])}
    }
    let query=format!("all(values,p=>p.value[{}]>0)",usize::MAX);
    let filter=Filter::prepare("SDL2|Point{1:fl64[1]:value;}|Root{1+:Point:values;}","Root",&query).unwrap();
    assert_eq!(filter.evaluate(&Rows(vec![Row([1.]),Row([2.])])),FilterResult::Indeterminate);
}

#[test]
fn empty_literals_keep_the_remaining_predicate_and_function_arguments() {
    struct Values;
    impl Source for Values {
        fn read<'a>(&'a self,p:&[Step<'_>])->Result<Atom<'a>,FilterError>{
            Ok(match p{[Step::Field{id:1,..}]=>Atom::String("nonempty"),[Step::Field{id:2,..}]=>Atom::Bool(false),_=>Atom::Missing})
        }
    }
    let catalogue="SDL2|Root{1:string:label;2:bool:active;}";
    for (query,result) in [
        ("label != '' && active",FilterResult::NoMatch),
        ("label == '' || !active",FilterResult::Match),
        ("len('') == 0",FilterResult::Match),
        ("'' == \"\" && len(')') == 1 && len('&&') == 2",FilterResult::Match),
    ]{assert_eq!(Filter::prepare(catalogue,"Root",query).unwrap().evaluate(&Values),result,"{query}");}
    for query in ["true ''","label != '' false","'' == '' active"]{
        assert!(Filter::prepare(catalogue,"Root",query).is_err(),"{query}");
    }
}

#[test]
fn quoted_operators_delimiters_and_identifiers_are_never_syntax() {
    let catalogue="SDL2|Point{1:fl64:x;}|Root{1:fl64:x;2:string:label;3+:Point:points;}";
    for query in ["x '>' 0","x > 0 '&&' x < 2","(x>0 ')'","len(label ')') == 0",
        "len '(' label )==0","points '[' 0].x>0","points[0 ']'.x>0","points['0'].x>0",
        "points[0].'x'>0","any(points ',' p=>p.x>0)","any(points,p '=>' p.x>0)",
        "any(points,'p'=>p.x>0)"] {
        assert!(Filter::prepare(catalogue,"Root",query).is_err(),"{query}");
    }
    for query in ["label=='>'","label=='&&'","len(')')==1"]{
        assert!(Filter::prepare(catalogue,"Root",query).is_ok(),"{query}");
    }
}

#[test]
fn large_fixed_wire_sequences_follow_decoder_limits_without_lifting_variable_caps() {
    let count=1_048_577;
    let catalogue="SDL2|Root{1+:uint8:values;}";
    let mut wire=vec![1];crate::write_count(count,&mut wire).unwrap();wire.resize(wire.len()+count,7);
    let context=crate::prepare(catalogue,&[]).unwrap();
    let decoded=crate::decode_dynamic(&context,&wire).unwrap();
    assert!(matches!(&decoded.fields["values"],crate::DynamicValue::Array(v) if v.len()==count));
    drop(decoded);
    for query in ["len(values)>1048576","values[1048576]==7"]{
        let filter=Filter::prepare(catalogue,"Root",query).unwrap();
        assert_eq!(filter.evaluate_wire(&wire),FilterResult::Match,"{query}");
        assert!(matches!(filter.evaluate_wire(&wire[..wire.len()-1]),FilterResult::Error(_)));
    }
    // A zero-byte string still needs a length byte: an oversized sequence of
    // them remains subject to the ordinary variable-element decoder cap.
    let catalogue="SDL2|Root{1*:string:values;}";
    let context=crate::prepare(catalogue,&[]).unwrap();
    wire[4..].fill(0);
    assert!(crate::decode_dynamic(&context,&wire).is_err());
    assert!(matches!(Filter::prepare(catalogue,"Root","len(values)>1048576").unwrap().evaluate_wire(&wire),FilterResult::Error(_)));
}
