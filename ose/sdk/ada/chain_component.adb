with Ada.Unchecked_Conversion;
with Chain_Text;
with Interfaces.C.Strings;
package body Chain_Component is
   use type Interfaces.C.int;
   use type Interfaces.C.size_t;
   use type Interfaces.Unsigned_64;
   function C_Read (E : access Raw_Event) return Interfaces.C.int
     with Import, Convention => C, External_Name => "chain_read";
   procedure C_Release (E : access Raw_Event)
     with Import, Convention => C, External_Name => "chain_release";
   function C_Send (Code : Interfaces.C.char; Data : System.Address; Size : Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "chain_send";
   function C_Ready return Interfaces.C.int
     with Import, Convention => C, External_Name => "chain_ready";
   function C_Tick (E : access constant Raw_Event; Time_Ns, Tick_Index : access Interfaces.Unsigned_64) return Interfaces.C.int
     with Import, Convention => C, External_Name => "chain_tick";
   function C_Publish (Port : Interfaces.C.char_array; Data : System.Address; Size : Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "chain_publish";
   function C_Input (E : access constant Raw_Event; Port : access System.Address; Port_Size : access Interfaces.C.size_t;
                     Catalogue : access System.Address; Catalogue_Size : access Interfaces.C.size_t;
                     Payload : access System.Address; Payload_Size : access Interfaces.C.size_t) return Interfaces.C.int
     with Import, Convention => C, External_Name => "chain_input";
   procedure Check (Result : Interfaces.C.int) is
   begin
      if Result = 0 then raise Protocol_Error with "invalid or incomplete CH/1 frame"; end if;
   end Check;
   function Copy_Text (Data : System.Address; Size : Interfaces.C.size_t) return String is
      Bytes : Interfaces.C.char_array (0 .. Size) with Import, Address => Data;
      Result : String (1 .. Natural (Size));
   begin
      for I in Result'Range loop Result (I) := Interfaces.C.To_Ada (Bytes (Interfaces.C.size_t (I - 1))); end loop;
      if not Chain_Text.Valid_UTF8 (Result) then raise Protocol_Error with "invalid UTF-8"; end if;
      -- Strings are UTF-8 octets, as in the public SDL Ada codec.
      return Result;
   end Copy_Text;
   overriding procedure Finalize (E : in out Event) is
   begin C_Release (E.Raw'Access); end Finalize;
   procedure Read_Event (E : in out Event) is
   begin C_Release (E.Raw'Access); Check (C_Read (E.Raw'Access)); end Read_Event;
   function Op (E : Event) return Character is (Interfaces.C.To_Ada (E.Raw.Code));
   function Body_Text (E : Event) return String is (Copy_Text (E.Raw.Data, E.Raw.Size));
   function Body_Size (E : Event) return Natural is (Natural (E.Raw.Size));
   procedure Input (E : Event; Port, Catalogue : out Ada.Strings.Unbounded.Unbounded_String;
                    Payload : out SDL_Runtime.Buffer) is
      P, C, D : aliased System.Address;
      PN, CN, DN : aliased Interfaces.C.size_t;
   begin
      Check (C_Input (E.Raw'Access, P'Access, PN'Access, C'Access, CN'Access, D'Access, DN'Access));
      if PN = 0 then raise Protocol_Error with "empty input port"; end if;
      Port := Ada.Strings.Unbounded.To_Unbounded_String (Copy_Text (P, PN));
      Catalogue := Ada.Strings.Unbounded.To_Unbounded_String (Copy_Text (C, CN));
      Payload.Clear;
      declare Bytes : SDL_Runtime.Bytes (1 .. Natural (DN)) with Import, Address => D;
      begin for B of Bytes loop Payload.Append (B); end loop; end;
   end Input;
   procedure Tick (E : Event; Time_Ns, Tick_Index : in out Interfaces.Unsigned_64) is
      T : aliased Interfaces.Unsigned_64 := Time_Ns;
      I : aliased Interfaces.Unsigned_64 := Tick_Index;
   begin
      if Body_Size (E) = 0 and I = Interfaces.Unsigned_64'Last then
         raise Protocol_Error with "tick index overflow";
      end if;
      Check (C_Tick (E.Raw'Access, T'Access, I'Access)); Time_Ns := T; Tick_Index := I;
   end Tick;
   procedure Ready is begin Check (C_Ready); end Ready;
   procedure Finish is begin Check (C_Send (Interfaces.C.To_C ('F'), System.Null_Address, 0)); end Finish;
   procedure Publish (Port : String; Payload : SDL_Runtime.Bytes) is
   begin
      Check (C_Publish (Interfaces.C.To_C (Port), Payload'Address, Interfaces.C.size_t (Payload'Length)));
   end Publish;
end Chain_Component;
