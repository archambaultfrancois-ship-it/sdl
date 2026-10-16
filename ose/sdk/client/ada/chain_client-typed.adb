with Ada.Unchecked_Deallocation;
with Ada.Unchecked_Conversion;
with Ada.Exceptions;
with Ada.Task_Attributes;
package body Chain_Client.Typed is
   use Ada.Strings.Unbounded;
   use type Interfaces.C.int;
   use type Interfaces.Unsigned_32;
   use type System.Address;
   use type Interfaces.C.Strings.chars_ptr;
   function C_Declare (Owner : System.Address; Topic, Root, Cat : Interfaces.C.char_array) return Interfaces.C.int
     with Import, Convention => C, External_Name => "cenv_declare";
   function C_Release (Owner : System.Address; Topic : Interfaces.C.char_array) return Interfaces.C.int
     with Import, Convention => C, External_Name => "cenv_release_publisher";
   function C_Publish (Owner : System.Address; Topic : Interfaces.C.char_array; Data : System.Address; Size : Interfaces.C.size_t) return Interfaces.C.Strings.chars_ptr
     with Import, Convention => C, External_Name => "cenv_publish_envelope";
   function C_Close (Self : System.Address) return Interfaces.C.int with Import, Convention => C, External_Name => "cenv_unsubscribe";
   function C_Wait (Self : System.Address; Ms : Interfaces.Unsigned_32) return Interfaces.C.int with Import, Convention => C, External_Name => "cenv_wait_closed";
   function C_Stats (Self : System.Address) return Interfaces.C.Strings.chars_ptr with Import, Convention => C, External_Name => "cenv_subscription_stats";
   procedure Open (Self : in out Publisher; Owner : Client; Topic : String) is
   begin
      if Self.Owner /= System.Null_Address then raise Client_Error with "publisher already opened"; end if;
      Self.Encoder := SDL_Runtime.Prepare (Catalogue, Catalogue);
      Self.Topic := To_Unbounded_String (Topic);
      Check (C_Declare (Raw (Owner.Native), C_String (Topic), C_String (Root), C_String (Catalogue)));
      Hold (Owner.Native); Self.Owner := Owner.Native; Self.Closed := False;
   end Open;
   function Publish (Self : Publisher; Message : Message_Type) return String is
   begin
      if Self.Closed then raise Client_Error with "publisher closed"; end if;
      declare Wire : constant SDL_Runtime.Bytes := SDL_Runtime.Pack_Envelope (SDL_Runtime.Catalogue_Id (Catalogue), Encode (Self.Encoder, Message));
      begin return Take_String (C_Publish (Raw (Self.Owner), C_String (To_String (Self.Topic)), Wire'Address, Interfaces.C.size_t (Wire'Length))); end;
   end Publish;
   procedure Close (Self : in out Publisher) is
   begin
      if not Self.Closed then
         Self.Closed := True;
         Check (C_Release (Raw (Self.Owner), C_String (To_String (Self.Topic))));
      end if;
   end Close;
   overriding procedure Finalize (Self : in out Publisher) is
   begin
      begin Close (Self); exception when others => null; end;
      Release (Self.Owner); Self.Owner := System.Null_Address;
   end Finalize;
   type Consumer is record
      Owner : Handle;
      Callback : On_Message;
      User : System.Address;
      Remote : Unbounded_String;
      Decoder : SDL_Runtime.Context;
   end record;
   type Consumer_Access is access all Consumer;
   type Value_Access is access all Message_Type;
   function Consumer_Ptr is new Ada.Unchecked_Conversion (System.Address, Consumer_Access);
   function Value_Ptr is new Ada.Unchecked_Conversion (System.Address, Value_Access);
   procedure Free_Consumer is new Ada.Unchecked_Deallocation (Consumer, Consumer_Access);
   procedure Free_Value is new Ada.Unchecked_Deallocation (Message_Type, Value_Access);
   package Active is new Ada.Task_Attributes (System.Address, System.Null_Address);
   type Raw_Info is record
      Publication_Id, Topic, Epoch : Interfaces.C.Strings.chars_ptr;
      Sequence : Interfaces.Unsigned_64;
   end record with Convention => C;
   type Decode_Hook is access function (User : System.Address; Cat : Interfaces.C.Strings.chars_ptr; Data : System.Address; Size : Interfaces.C.size_t) return System.Address
     with Convention => C;
   type Free_Hook is access procedure (Value : System.Address) with Convention => C;
   type Message_Hook is access function (User, Value : System.Address; Info : access constant Raw_Info) return Interfaces.C.int with Convention => C;
   type Raw_Hooks is record
      User : System.Address;
      Decoder : Decode_Hook;
      Free : Free_Hook;
      Callback : Message_Hook;
      Destroy : Free_Hook;
   end record with Convention => C_Pass_By_Copy;
   function C_Subscribe (Owner : System.Address; Topic, Root, Cat, Policy : Interfaces.C.char_array; Hooks : Raw_Hooks) return System.Address
     with Import, Convention => C, External_Name => "chain_ada_subscribe";
   function Decode_Message (User : System.Address; Cat : Interfaces.C.Strings.chars_ptr; Data : System.Address; Size : Interfaces.C.size_t) return System.Address with Convention => C;
   -- The outer handled sequence also catches failures while elaborating local
   -- strings/envelopes; Ada declarative-part exceptions bypass their own handler.
   procedure Remember_Failure (User : System.Address; Error : Ada.Exceptions.Exception_Occurrence) is
   begin
      declare Detail : String := Ada.Exceptions.Exception_Information (Error);
      begin
         for C of Detail loop if C = Character'Val (0) then C := '?'; end if; end loop;
         begin
            Set_Failure (Consumer_Ptr (User).Owner, C_String (Detail));
         exception when others =>
            Set_Failure (Consumer_Ptr (User).Owner, Interfaces.C.To_C ("Ada callback failed"));
         end;
      end;
   exception when others => null;
   end Remember_Failure;
   function Decode_Message (User : System.Address; Cat : Interfaces.C.Strings.chars_ptr; Data : System.Address; Size : Interfaces.C.size_t) return System.Address is
   begin
      declare
         Self : constant Consumer_Access := Consumer_Ptr (User);
         Text : constant String := Interfaces.C.Strings.Value (Cat);
         Bytes : SDL_Runtime.Bytes (1 .. Natural (Size)) with Import, Address => Data;
         Envelope : constant SDL_Runtime.Envelope := SDL_Runtime.Unpack_Envelope (Bytes);
         Value : Value_Access;
      begin
         if Envelope.Catalogue /= SDL_Runtime.Catalogue_Id (Text) then raise Client_Error with "catalogue identity mismatch"; end if;
         if To_String (Self.Remote) /= Text then
            if not SDL_Runtime.Is_Compatible (SDL_Runtime.Compare_Catalogues (Text, Catalogue, Root, True)) then raise Client_Error with "incompatible catalogue"; end if;
            Self.Decoder := SDL_Runtime.Prepare (Text, Catalogue); Self.Remote := To_Unbounded_String (Text);
         end if;
         Value := new Message_Type'(Decode (Self.Decoder, Envelope.Payload)); return Value.all'Address;
      end;
   exception when Error : others =>
      Remember_Failure (User, Error); return System.Null_Address;
   end Decode_Message;
   procedure Release_Message (Pointer : System.Address) with Convention => C;
   procedure Release_Message (Pointer : System.Address) is Value : Value_Access := Value_Ptr (Pointer);
   begin Free_Value (Value); exception when others => null; end;
   function Deliver (User, Value : System.Address; Info : access constant Raw_Info) return Interfaces.C.int with Convention => C;
   function Deliver (User, Value : System.Address; Info : access constant Raw_Info) return Interfaces.C.int is
   begin
      declare
         Self : constant Consumer_Access := Consumer_Ptr (User);
         Metadata : constant Message_Info := (To_Unbounded_String (Interfaces.C.Strings.Value (Info.Publication_Id)),
             To_Unbounded_String (Interfaces.C.Strings.Value (Info.Topic)), To_Unbounded_String (Interfaces.C.Strings.Value (Info.Epoch)), Info.Sequence);
      begin
         Active.Set_Value (User); Self.Callback (Value_Ptr (Value).all, Metadata, Self.User); Active.Set_Value (System.Null_Address); return 1;
      end;
   exception when Error : others =>
      begin Active.Set_Value (System.Null_Address); exception when others => null; end;
      Remember_Failure (User, Error); return 0;
   end Deliver;
   procedure Destroy (Pointer : System.Address) with Convention => C;
   procedure Destroy (Pointer : System.Address) is Self : Consumer_Access := Consumer_Ptr (Pointer);
   begin Release (Self.Owner); Free_Consumer (Self); exception when others => null; end;
   procedure Subscribe (Self : in out Subscription; Owner : Client; Topic : String;
                        Callback : not null On_Message; QoS : Options := (others => <>);
                        User : System.Address := System.Null_Address) is
      State : Consumer_Access;
      Native : System.Address;
      -- Validate all potentially raising string conversions before transferring ownership.
      T : constant Interfaces.C.char_array := C_String (Topic);
      R : constant Interfaces.C.char_array := C_String (Root);
      C : constant Interfaces.C.char_array := C_String (Catalogue);
      P : constant Interfaces.C.char_array := C_String (Policy (QoS));
   begin
      if Self.Native /= System.Null_Address then raise Client_Error with "subscription already opened"; end if;
      State := new Consumer'(Owner.Native, Callback, User, Null_Unbounded_String, SDL_Runtime.Prepare (Catalogue, Catalogue));
      Hold (Owner.Native); Self.State := State.all'Address;
      Native := C_Subscribe (Raw (Owner.Native), T, R, C, P,
        (State.all'Address, Decode_Message'Access, Release_Message'Access, Deliver'Access, Destroy'Access));
      if Native = System.Null_Address then Self.State := System.Null_Address; Check (0); end if;
      Self.Native := Wrap (Native, 1);
      if Self.Native = System.Null_Address then raise Storage_Error; end if;
   end Subscribe;
   procedure Unsubscribe (Self : in out Subscription) is Result : Interfaces.C.int;
   begin
      if Self.Native /= System.Null_Address then
         Enter_Ada; Result := C_Close (Raw (Self.Native)); Leave_Ada; Check (Result);
      end if;
   end Unsubscribe;
   function Wait_Closed (Self : Subscription; Timeout_Ms : Interfaces.Unsigned_32) return Boolean is
   begin
      if Active.Value = Self.State and Self.State /= System.Null_Address then raise Client_Error with "cannot wait from own callback"; end if;
      if Self.Native = System.Null_Address then return True; end if;
      return C_Wait (Raw (Self.Native), Timeout_Ms) /= 0;
   end Wait_Closed;
   function Stats (Self : Subscription) return String is
   begin return Take_String (C_Stats (Raw (Self.Native))); end;
   overriding procedure Finalize (Self : in out Subscription) is
   begin
      begin Unsubscribe (Self); exception when others => null; end;
      -- Join before Ada finalization destroys user state. Explicit Close stays nonblocking.
      if Active.Value /= Self.State then
         while not Wait_Closed (Self, 100) loop null; end loop;
      end if;
      Release (Self.Native); Self.Native := System.Null_Address; Self.State := System.Null_Address;
   end Finalize;
end Chain_Client.Typed;
