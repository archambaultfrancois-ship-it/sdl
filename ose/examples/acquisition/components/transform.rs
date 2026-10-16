use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;

#[derive(Default)]
pub struct Handler;

impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, message: &Measurement) -> Result {
        let mut result = message.clone();
        result.value *= 2.0;
        ctx.publish_result(&result)
    }
}
