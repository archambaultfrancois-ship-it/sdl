use crate::dynamic::{parse_descriptor, SchemaDesc};
use std::collections::{BTreeMap, HashSet};

/// First incompatibility when the consumer reads the publisher catalogue.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CompatibilityDiagnostic {
    pub code: &'static str,
    pub message: String,
    pub path: String,
    pub field_id: u32,
    pub publisher_type: String,
    pub consumer_type: String,
    pub enum_value: i32,
}
impl CompatibilityDiagnostic {
    pub fn compatible(&self) -> bool { self.code == "COMPATIBLE" }
}
fn diagnostic(code: &'static str, message: &str, path: &str, field_id: u32,
              publisher_type: &str, consumer_type: &str, enum_value: i32) -> CompatibilityDiagnostic {
    CompatibilityDiagnostic {
        code, message: message.into(), path: path.into(), field_id,
        publisher_type: publisher_type.into(), consumer_type: consumer_type.into(), enum_value,
    }
}
fn empty(code: &'static str) -> CompatibilityDiagnostic { diagnostic(code, "", "", 0, "", "", 0) }
fn kind(schema: &SchemaDesc, name: &str) -> u8 {
    if schema.messages.contains_key(name) { 1 } else if schema.enums.contains_key(name) { 2 } else { 0 }
}
fn visit(p: &SchemaDesc, c: &SchemaDesc, name: &str, message: &str, path: &str, field_id: u32,
         required: bool, checked: &mut HashSet<String>) -> Option<CompatibilityDiagnostic> {
    if kind(p, name) != kind(c, name) {
        return Some(diagnostic("TYPE_KIND_MISMATCH", message, path, field_id, name, name, 0));
    }
    if checked.contains(name) { return None; }
    if let Some(values) = p.enums.get(name) {
        let accepted: HashSet<_> = c.enums[name].iter().map(|(_, v)| *v).collect();
        for (_, value) in values {
            if !accepted.contains(value) {
                return Some(diagnostic("ENUM_VALUE_UNKNOWN", message, path, field_id, name, name, *value));
            }
        }
    } else if let Some(pm) = p.messages.get(name) {
        let cm = &c.messages[name];
        let local: BTreeMap<_, _> = cm.fields.iter().map(|f| (f.id, f)).collect();
        for field in &pm.fields {
            let other = match local.get(&field.id) { Some(value) => *value, None => continue };
            let at = format!("{}.{}", path, field.name);
            let code = if field.type_name != other.type_name { "FIELD_TYPE_MISMATCH" }
                else if field.dimensions != other.dimensions { "FIELD_DIMENSIONS_MISMATCH" }
                else if field.modifier.min(2) != other.modifier.min(2) { "FIELD_CARDINALITY_MISMATCH" }
                else { "COMPATIBLE" };
            if code != "COMPATIBLE" {
                return Some(diagnostic(code, message, &at, field.id, &field.type_name, &other.type_name, 0));
            }
            if let Some(issue) = visit(p, c, &field.type_name, message, &at, field.id, required, checked) {
                return Some(issue);
            }
        }
        if required {
            let ids: HashSet<_> = pm.fields.iter().map(|f| f.id).collect();
            for field in &cm.fields {
                if field.modifier == 0 && !ids.contains(&field.id) {
                    return Some(diagnostic("REQUIRED_FIELD_MISSING", message,
                        &format!("{}.{}", path, field.name), field.id, "", &field.type_name, 0));
                }
            }
        }
    }
    checked.insert(name.into());
    None
}
/// Direction: consumer reads publisher; no generated types are required.
/// root=None checks every publisher message in wire ID order.
/// require_required adds a business policy beyond SDL2's missing-field defaults.
pub fn compare_catalogues(publisher: &str, consumer: &str, root: Option<&str>,
                          require_required: bool) -> CompatibilityDiagnostic {
    let p = match parse_descriptor(publisher) {
        Ok(schema) => schema, Err(_) => return empty("INVALID_PUBLISHER_CATALOGUE"),
    };
    let c = match parse_descriptor(consumer) {
        Ok(schema) => schema, Err(_) => return empty("INVALID_CONSUMER_CATALOGUE"),
    };
    let mut roots: Vec<_> = match root {
        Some(name) => vec![name], None => p.messages.keys().map(String::as_str).collect(),
    };
    roots.sort();
    let mut checked = HashSet::new();
    for name in roots {
        if !p.messages.contains_key(name) || kind(&c, name) == 0 {
            return diagnostic("ROOT_UNKNOWN", name, name, 0,
                if p.messages.contains_key(name) { name } else { "" },
                if kind(&c, name) != 0 { name } else { "" }, 0);
        }
        if let Some(issue) = visit(&p, &c, name, name, name, 0, require_required, &mut checked) {
            return issue;
        }
    }
    empty("COMPATIBLE")
}
