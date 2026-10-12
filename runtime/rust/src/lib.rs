mod envelope;
pub use envelope::{pack_envelope, unpack_envelope, ENVELOPE_HEADER_SIZE};
mod compatibility;
pub use compatibility::{compare_catalogues, CompatibilityDiagnostic};
mod catalogue;
pub use catalogue::catalogue_id;
mod dynamic;
mod wire;
pub use dynamic::{decode_dynamic, DynamicMessage, DynamicValue, FieldDesc};
pub use wire::{
    add_size, count_size, decode, decode_any, description, display, display_struct_end,
    display_struct_field, display_struct_start, encode, encode_sequence, prepare, write_count, CodecError,
    Complex32, Complex64, Context, Reader, SdlDisplay, SdlMessage, TypeInfo, TypedMessage,
    WireValue,
};

mod representation;
pub use representation::WireOptions;
