with Ose_ada_worker;
package Ada_Worker_Handler is
    type Object is new Ose_ada_worker.Handler with record
        Received : Natural := 0;
    end record;
    overriding procedure On_input (Self : in out Object; Ctx : in out Ose_ada_worker.Context;
                                   Message : not null Ose_ada_worker.Input_input);
end Ada_Worker_Handler;
