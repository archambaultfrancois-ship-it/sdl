use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, input: &Measurement) -> Result {
        ctx.publish_value(&Measurement { sequence: input.sequence, value: input.value + 2.0 })
    }
}
