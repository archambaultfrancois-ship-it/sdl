with SDL_measurement;
with SDL_Runtime;
with Ada.Environment_Variables;
package body Ada_Worker_Handler is
    overriding procedure On_input (Self : in out Object; Ctx : in out Ose_ada_worker.Context;
                                   Message : not null Ose_ada_worker.Input_input) is
        use type SDL_Runtime.Float64;
        Result : SDL_measurement.T_Measurement := Message.all;
    begin
        Self.Received := Self.Received + 1;
        if Ada.Environment_Variables.Exists ("OSE_TEST_DELAY_MS") then
            delay Duration'Value (Ada.Environment_Variables.Value ("OSE_TEST_DELAY_MS")) / 1000;
        end if;
        Result.F_value := Result.F_value + 4.0;
        Ose_ada_worker.Publish_value (Ctx, Result);
    end On_input;
end Ada_Worker_Handler;
