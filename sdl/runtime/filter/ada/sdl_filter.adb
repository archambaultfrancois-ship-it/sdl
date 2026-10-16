with Interfaces;
with Interfaces.C.Strings;
with System.Address_To_Access_Conversions;
package body SDL_Filter is
   use type System.Address;
   use type Interfaces.C.int;
   use SDL_Filter_Values;
   function C_Prepare (Catalogue, Root, Expression : Byte_Slice) return System.Address
      with Import, Convention => C, External_Name => "sdl_filter_prepare";
   procedure C_Free (P : System.Address)
      with Import, Convention => C, External_Name => "sdl_filter_free";
   function C_Wire (P : System.Address; Data : Byte_Slice) return Interfaces.C.int
      with Import, Convention => C, External_Name => "sdl_filter_evaluate_wire";
   function C_Error return Interfaces.C.Strings.chars_ptr
      with Import, Convention => C, External_Name => "sdl_filter_error";
   function C_Root (P : System.Address) return Byte_Slice
      with Import, Convention => C, External_Name => "sdl_filter_root";
   function Finish (Code : Interfaces.C.int) return Result is
   begin
      case Code is
         when 1 => return (Match, Ada.Strings.Unbounded.Null_Unbounded_String);
         when 0 => return (No_Match, Ada.Strings.Unbounded.Null_Unbounded_String);
         when 2 => return (Indeterminate, Ada.Strings.Unbounded.Null_Unbounded_String);
         when others => return (Error, Ada.Strings.Unbounded.To_Unbounded_String (Interfaces.C.Strings.Value (C_Error)));
      end case;
   end Finish;
   function Prepare (Catalogue, Root_Type, Expression : String) return Predicate is
   begin
      return P : Predicate do
         P.Handle := C_Prepare ((Catalogue'Address, Catalogue'Length),
            (Root_Type'Address, Root_Type'Length), (Expression'Address, Expression'Length));
         if P.Handle = System.Null_Address then
            raise Constraint_Error with Interfaces.C.Strings.Value (C_Error);
         end if;
      end return;
   end Prepare;
   overriding procedure Finalize (P : in out Predicate) is
   begin
      if P.Handle /= System.Null_Address then C_Free (P.Handle); P.Handle := System.Null_Address; end if;
   end Finalize;
   function Evaluate_Wire (P : Predicate; Data : SDL_Runtime.Bytes) return Result is
   begin
      return Finish (C_Wire (P.Handle, (Data'Address, Data'Length)));
   end Evaluate_Wire;
   package body Typed is
      type Callback is access function (User, Steps : System.Address; Count : Interfaces.C.size_t;
         Slot : Interfaces.Unsigned_32; Output : System.Address) return Interfaces.C.int
         with Convention => C;
      function C_Object (P : System.Address; Reader : Callback; User : System.Address) return Interfaces.C.int
         with Import, Convention => C, External_Name => "sdl_filter_evaluate_object";
      type Message_Access is access constant Message_Type;
      type Evaluation is limited record
         Message : Message_Access;
         Work : Scratch;
      end record;
      package Pointers is new System.Address_To_Access_Conversions (Evaluation);
      function Reader (User, Steps : System.Address; Count : Interfaces.C.size_t;
         Slot : Interfaces.Unsigned_32; Output : System.Address) return Interfaces.C.int
         with Convention => C;
      function Reader (User, Steps : System.Address; Count : Interfaces.C.size_t;
         Slot : Interfaces.Unsigned_32; Output : System.Address) return Interfaces.C.int is
         E : constant Pointers.Object_Pointer := Pointers.To_Pointer (User);
         P : Path (1 .. Natural (Count)) with Import, Address => Steps;
         V : Value with Import, Address => Output;
      begin
         V := Read (E.Message.all, P, Natural (Slot), E.Work); return 0;
      exception when others => return -1;
      end Reader;
      function Evaluate (P : Predicate; M : aliased in Message_Type) return Result is
         E : aliased Evaluation;
      begin
         if P.Handle = System.Null_Address then raise Constraint_Error with "filter is closed"; end if;
         declare
            S : Step := (Owner => C_Root (P.Handle), others => <>);
         begin
            if not Owner_Is (S, Root_Type) then raise Constraint_Error with "object root does not match filter type"; end if;
         end;
         E.Message := M'Unchecked_Access;
         return Finish (C_Object (P.Handle, Reader'Access, E'Address));
      end Evaluate;
   end Typed;
end SDL_Filter;
