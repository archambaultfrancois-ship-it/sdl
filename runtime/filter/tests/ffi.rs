use sdl_filter::{Bytes, CStep, Value, sdl_filter_prepare, sdl_filter_free,
    sdl_filter_evaluate_object, sdl_filter_evaluate_wire, sdl_filter_error, sdl_filter_failure};
use std::ffi::{c_void, CStr};

fn bytes(s: &str) -> Bytes { Bytes { data: s.as_ptr(), size: s.len() } }
fn diagnostic() -> String { unsafe { CStr::from_ptr(sdl_filter_error()).to_string_lossy().into_owned() } }
const CATALOGUE: &str = "SDL2|Root{1:fl64:x;2:fl64:y;}";

#[test]
fn invalid_slices_and_closed_inputs_return_diagnostics() {
    unsafe {
        let invalid = Bytes { data: std::ptr::null(), size: 1 };
        assert!(sdl_filter_prepare(invalid, bytes("Root"), bytes("true")).is_null());
        assert!(diagnostic().contains("slice"));
        let too_large = Bytes { data: CATALOGUE.as_ptr(), size: usize::MAX };
        assert!(sdl_filter_prepare(too_large, bytes("Root"), bytes("true")).is_null());
        assert_eq!(sdl_filter_evaluate_wire(std::ptr::null(), invalid), -1);
        assert!(diagnostic().contains("null filter"));
        sdl_filter_free(std::ptr::null_mut());
    }
}

unsafe extern "C" fn reader(user: *mut c_void, path: *const CStep, length: usize, _: u32, out: *mut Value) -> i32 {
    if length != 1 { return -1; }
    *(user as *mut usize) += 1;
    (*out).kind = 4;
    (*out).real = match (*path).id { 1 => 3., 2 => 4., _ => return -1 };
    0
}

#[test]
fn numeric_ffi_deduplicates_loads_and_preserves_wire_errors() {
    unsafe {
        let plan = sdl_filter_prepare(bytes(CATALOGUE), bytes("Root"), bytes("x*x+y*y<=25"));
        assert!(!plan.is_null());
        let mut reads = 0_usize;
        assert_eq!(sdl_filter_evaluate_object(plan, Some(reader), &mut reads as *mut _ as *mut c_void), 1);
        assert_eq!(reads, 2);
        let truncated = [1_u8, 0];
        assert_eq!(sdl_filter_evaluate_wire(plan, Bytes { data: truncated.as_ptr(), size: truncated.len() }), -1);
        assert!(diagnostic().contains("size"));
        assert_eq!(sdl_filter_evaluate_object(plan, None, std::ptr::null_mut()), -1);
        sdl_filter_free(plan);
    }
}

#[test]
fn adapter_diagnostics_are_thread_local_and_escape_embedded_nuls() {
    unsafe { assert_eq!(sdl_filter_failure(bytes("main\0diagnostic")), -1); }
    std::thread::spawn(|| {
        unsafe { assert_eq!(sdl_filter_failure(bytes("worker diagnostic")), -1); }
        assert_eq!(diagnostic(), "worker diagnostic");
    }).join().unwrap();
    assert_eq!(diagnostic(), "main\\0diagnostic");
}
