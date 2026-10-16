use super::*;

#[derive(Debug,Clone)]
struct Token { text:String, offset:usize, kind:TokenKind }
#[derive(Debug,Clone,Copy,PartialEq,Eq)]
enum TokenKind { Syntax, String, End }
impl Token {
    fn syntax(&self,text:&str)->bool{self.kind==TokenKind::Syntax&&self.text==text}
}
fn lex(text:&str)->Result<Vec<Token>,FilterError>{
    if text.len()>65536{return Err(error("expression exceeds 65536 bytes"));}
    let mut tokens=Vec::new();let mut i=0;let b=text.as_bytes();
    while i<b.len(){
        if b[i].is_ascii_whitespace(){i+=1;continue;}
        let start=i;
        if b[i]==b'\''||b[i]==b'"'{
            let quote=b[i];i+=1;let mut value=String::new();let mut closed=false;
            while i<b.len(){
                if b[i]==quote{i+=1;closed=true;break;}
                if b[i]==b'\\'{i+=1;if i==b.len(){break;}value.push(match b[i]{b'n'=>'\n',b'r'=>'\r',b't'=>'\t',b'\\'=>'\\',b'\''=>'\'',b'"'=>'"',_=>return Err(error(format!("invalid string escape at byte {i}")))});i+=1;}
                else{let c=text[i..].chars().next().unwrap();value.push(c);i+=c.len_utf8();}
            }
            if !closed{return Err(error(format!("unterminated string at byte {start}")));}
            tokens.push(Token{text:value,offset:start,kind:TokenKind::String});continue;
        }
        if b[i].is_ascii_digit()||(b[i]==b'.'&&b.get(i+1).is_some_and(u8::is_ascii_digit)){
            i+=1;while i<b.len()&&b[i].is_ascii_digit(){i+=1;}
            if b.get(i)==Some(&b'.'){i+=1;while i<b.len()&&b[i].is_ascii_digit(){i+=1;}}
            if matches!(b.get(i),Some(b'e'|b'E')){i+=1;if matches!(b.get(i),Some(b'+'|b'-')){i+=1;}let digits=i;while i<b.len()&&b[i].is_ascii_digit(){i+=1;}if i==digits{return Err(error(format!("invalid exponent at byte {start}")));}}
        }else if text[i..].chars().next().is_some_and(|c|c.is_alphabetic()||c=='_'){
            while i<b.len(){let c=text[i..].chars().next().unwrap();if !c.is_alphanumeric()&&c!='_'&&c!='$'{break;}i+=c.len_utf8();}
        }else{
            if !b[i].is_ascii(){return Err(error(format!("unexpected character at byte {start}")));}
            i+=1;if i<b.len()&&b[i].is_ascii()&&matches!(&text[start..i+1],"&&"|"||"|"=="|"!="|"<="|">="|"=>"){i+=1;}
            if !matches!(&text[start..i],"&&"|"||"|"=="|"!="|"<="|">="|"=>"|"+"|"-"|"*"|"/"|"%"|"!"|"<"|">"|"("|")"|"["|"]"|","|"."){return Err(error(format!("unexpected character at byte {start}")));}
        }
        tokens.push(Token{text:text[start..i].into(),offset:start,kind:TokenKind::Syntax});
    }
    tokens.push(Token{text:String::new(),offset:text.len(),kind:TokenKind::End});Ok(tokens)
}
fn type_of(schema:&SchemaDesc,name:&str)->Ty{
    match name{"bool"=>Ty::Bool,"fl32"|"fl64"=>Ty::Float,"string"=>Ty::String,"c32"|"c64"=>Ty::Complex,
        n if schema.messages.contains_key(n)=>Ty::Message(n.into()),n if schema.enums.contains_key(n)=>Ty::Enum(n.into()),_=>Ty::Int}
}
struct Parser<'a>{schema:&'a SchemaDesc,root:&'a str,tokens:Vec<Token>,pos:usize,nodes:Vec<Node>,heights:Vec<usize>,paths:Vec<Path>,scopes:Vec<(String,usize,Ty)>,scope_count:usize,depth:usize}
pub(super) fn compile(schema:&SchemaDesc,root:&str,text:&str)->Result<(Vec<Node>,Vec<Path>,usize,usize),FilterError>{
    let mut p=Parser{schema,root,tokens:lex(text)?,pos:0,nodes:Vec::new(),heights:Vec::new(),paths:Vec::new(),scopes:Vec::new(),scope_count:0,depth:0};
    let entry=p.expression(0)?;
    if p.peek().kind!=TokenKind::End{return p.fail("unexpected token");}
    if p.nodes[entry].ty!=Ty::Bool{return p.fail("filter must return bool");}
    Ok((p.nodes,p.paths,entry,p.scope_count))
}
impl Parser<'_>{
    fn peek(&self)->&Token{&self.tokens[self.pos]}
    fn fail<T>(&self,text:&str)->Result<T,FilterError>{Err(error(format!("{text} at byte {}",self.peek().offset)))}
    fn take(&mut self)->Token{let t=self.peek().clone();if t.kind!=TokenKind::End{self.pos+=1;}t}
    fn expect(&mut self,t:&str)->Result<(),FilterError>{if !self.peek().syntax(t){return self.fail(&format!("expected {t}"));}self.take();Ok(())}
    fn node(&mut self,op:Op,ty:Ty)->Result<usize,FilterError>{
        if self.nodes.len()>=2048{return self.fail("expression exceeds 2048 operations");}
        let height=match &op{Op::Unary(_,n)|Op::Function(_,n)=>self.heights[*n]+1,Op::Binary(_,l,r)=>self.heights[*l].max(self.heights[*r])+1,Op::Quantifier{array,predicate,..}=>self.heights[*array].max(self.heights[*predicate])+1,_=>1};
        if height>64{return self.fail("evaluation nesting exceeds 64 levels");}
        let id=self.nodes.len();self.nodes.push(Node{op,ty});self.heights.push(height);Ok(id)
    }
    fn expression(&mut self,min:u8)->Result<usize,FilterError>{
        self.depth+=1;if self.depth>64{return self.fail("expression nesting exceeds 64 levels");}
        let mut left=self.primary()?;
        loop{
            if self.peek().kind!=TokenKind::Syntax{break;}
            let op=self.peek().text.as_str();let precedence=match op{"||"=>1,"&&"=>2,"=="|"!="=>3,"<"|"<="|">"|">="=>4,"+"|"-"=>5,"*"|"/"|"%"=>6,_=>0};
            if precedence==0||precedence<min{break;}
            let op=self.take().text;let right=self.expression(precedence+1)?;
            let l=&self.nodes[left].ty;let r=&self.nodes[right].ty;
            let ty=match op.as_str(){
                "&&"|"||" if l==&Ty::Bool&&r==&Ty::Bool=>Ty::Bool,
                "=="|"!=" if l==r&&matches!(l,Ty::Bool|Ty::String|Ty::Enum(_))||l.numeric()&&r.numeric()=>Ty::Bool,
                "<"|"<="|">"|">=" if l.numeric()&&r.numeric()||l==&Ty::String&&r==&Ty::String=>Ty::Bool,
                "%" if l==&Ty::Int&&r==&Ty::Int=>Ty::Int,
                "+"|"-"|"*"|"/" if l.numeric()&&r.numeric()=>if op=="/"||l==&Ty::Float||r==&Ty::Float{Ty::Float}else{Ty::Int},
                _=>return self.fail("incompatible operand types"),
            };
            left=self.node(Op::Binary(op,left,right),ty)?;
        }
        self.depth-=1;Ok(left)
    }
    fn primary(&mut self)->Result<usize,FilterError>{
        let token=self.take();
        if token.kind==TokenKind::String{return self.node(Op::Constant(Literal::String(token.text)),Ty::String);}
        match token.text.as_str(){
            "!"|"+"|"-"=>{let n=self.expression(7)?;let t=self.nodes[n].ty.clone();if token.text=="!"&&t!=Ty::Bool||token.text!="!"&&!t.numeric(){return self.fail("invalid unary operand");}return self.node(Op::Unary(token.text,n),t);},
            "("=>{let n=self.expression(0)?;self.expect(")")?;return Ok(n);},
            "true"|"false"=>return self.node(Op::Constant(Literal::Bool(token.text=="true")),Ty::Bool),
            ""=>return self.fail("expected expression"),_=>{}
        }
        if token.text.as_bytes()[0].is_ascii_digit()||token.text.starts_with('.')&&token.text.len()>1{
            if token.text.contains(['.','e','E']){let f=token.text.parse::<f64>().map_err(|_|error("invalid floating literal"))?;if !f.is_finite(){return self.fail("floating literal is out of range");}return self.node(Op::Constant(Literal::Float(f)),Ty::Float);}
            let n=token.text.parse::<u64>().map_err(|_|error("integer literal exceeds uint64"))?;return self.node(Op::Constant(Literal::Unsigned(n)),Ty::Int);
        }
        if self.peek().syntax("("){return self.function(token.text);}
        if token.text=="."{let name=self.identifier()?;return self.path(format!(".{name}"));}
        self.path(token.text)
    }
    fn function(&mut self,name:String)->Result<usize,FilterError>{
        self.expect("(")?;let argument=self.expression(0)?;
        if name=="any"||name=="all"{
            let element=match self.nodes[argument].ty.clone(){Ty::Array(t)=>*t,_=>return self.fail("quantifier requires an array")};
            if !matches!(self.nodes[argument].op,Op::Load(_)){return self.fail("quantifier requires a field path");}
            self.expect(",")?;let token=self.take();let variable=token.text;
            if token.kind!=TokenKind::Syntax||matches!(variable.as_str(),"true"|"false")||variable.is_empty()||!variable.chars().next().is_some_and(|c|c.is_alphabetic()||c=='_'){return self.fail("expected lambda variable");}
            self.expect("=>")?;let scope=self.scope_count;self.scope_count+=1;
            if self.scope_count>64{return self.fail("too many quantifiers");}
            self.scopes.push((variable,scope,element));let predicate=self.expression(0)?;self.scopes.pop();self.expect(")")?;
            if self.nodes[predicate].ty!=Ty::Bool{return self.fail("quantifier predicate must return bool");}
            return self.node(Op::Quantifier{all:name=="all",scope,array:argument,predicate},Ty::Bool);
        }
        self.expect(")")?;let t=&self.nodes[argument].ty;
        let ty=match name.as_str(){"exists"=>Ty::Bool,"len" if matches!(t,Ty::Array(_)|Ty::String)=>Ty::Int,"abs" if t.numeric()=>t.clone(),"abs" if t==&Ty::Complex=>Ty::Float,"real"|"imag" if t==&Ty::Complex=>Ty::Float,"isfinite" if t.numeric()=>Ty::Bool,_=>return self.fail("unknown function or incompatible argument")};
        self.node(Op::Function(name,argument),ty)
    }
    fn path(&mut self,first:String)->Result<usize,FilterError>{
        let mut names=vec![first];
        while self.peek().syntax("."){self.take();names.push(self.identifier()?);}
        // Enum constants use their full logical type or the root package's short name.
        if names.len()>=2&&!self.schema.messages[self.root].fields.iter().any(|f|f.name==names[0])&&!self.scopes.iter().any(|(n,_,_)|n==&names[0]){
            let enum_name=names[..names.len()-1].join(".");let package=self.root.rsplit_once('.').map(|(p,_)|p);
            let local=package.map(|p|format!("{p}.{enum_name}"));
            let chosen=if enum_name.starts_with('.') {enum_name[1..].to_owned()} else if enum_name.contains('.')||package.is_none(){enum_name}else{local.unwrap()};
            if let Some(values)=self.schema.enums.get(&chosen){if let Some((_,v))=values.iter().find(|(n,_)|n==names.last().unwrap()){return self.node(Op::Constant(Literal::Signed(*v as i64)),Ty::Enum(chosen));}}
        }
        let scope=self.scopes.iter().rev().find(|(n,_,_)|n==&names[0]).cloned();
        let (base,mut ty,start)=match scope{Some((_,scope,t))=>(Some(scope),t,1),None=>(None,Ty::Message(self.root.into()),0)};
        let mut parts=Vec::new();
        for name in &names[start..]{ty=self.field(&ty,name,&mut parts)?;}
        loop{
            if self.peek().syntax("["){
                self.take();let token=self.take();if token.kind!=TokenKind::Syntax{return self.fail("array index must be an unsigned literal");}
                let index=token.text.parse::<usize>().map_err(|_|error("array index must be an unsigned literal"))?;self.expect("]")?;
                ty=match ty{Ty::Array(t)=>*t,_=>return self.fail("indexing requires an array")};parts.push(Part::Index(index));
            }else if self.peek().syntax("."){self.take();let name=self.identifier()?;ty=self.field(&ty,&name,&mut parts)?;}
            else{break;}
        }
        if let Some(id)=self.paths.iter().position(|p|p.base==base&&p.parts==parts){return self.node(Op::Load(id),ty);}
        if self.paths.len()>=MAX_LOADS{return self.fail("expression exceeds 128 field accesses");}
        let id=self.paths.len();self.paths.push(Path{base,parts,ty:ty.clone()});self.node(Op::Load(id),ty)
    }
    fn identifier(&mut self)->Result<String,FilterError>{
        let token=self.take();
        if token.kind!=TokenKind::Syntax||!token.text.chars().next().is_some_and(|c|c.is_alphabetic()||c=='_'){return self.fail("expected field name");}
        Ok(token.text)
    }
    fn field(&self,ty:&Ty,name:&str,parts:&mut Vec<Part>)->Result<Ty,FilterError>{
        if ty==&Ty::Complex&&matches!(name,"real"|"imag"){parts.push(Part::Field{id:if name=="real"{1}else{2},owner:COMPLEX_OWNER.into(),name:name.into()});return Ok(Ty::Float);}
        let owner=match ty{Ty::Message(n)=>n,_=>return self.fail("field selection requires a message")};
        let f=self.schema.messages[owner].fields.iter().find(|f|f.name==name).ok_or_else(||error(format!("unknown field {owner}.{name}")))?;
        parts.push(Part::Field{id:f.id,owner:owner.clone(),name:f.name.clone()});let mut t=type_of(self.schema,&f.type_name);
        for _ in &f.dimensions{t=Ty::Array(Box::new(t));}if f.modifier>=2{t=Ty::Array(Box::new(t));}Ok(t)
    }
}
