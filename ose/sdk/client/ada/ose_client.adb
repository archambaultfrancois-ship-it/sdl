with Ada.Strings.Fixed;
with GNAT.Threads;
with Ose_Text;
package body Ose_Client is
   use type Interfaces.C.int;
   use type Interfaces.C.Strings.chars_ptr;
   use type System.Address;
   function C_Connect (Host : Interfaces.C.char_array; Port : Interfaces.Unsigned_16; Id : Interfaces.C.char_array) return System.Address
     with Import, Convention => C, External_Name => "cenv_connect";
   procedure C_Close (Value : System.Address) with Import, Convention => C, External_Name => "cenv_close";
   function C_Run (Value : System.Address) return Interfaces.C.int with Import, Convention => C, External_Name => "cenv_run";
   function C_Topics (Value : System.Address) return Interfaces.C.Strings.chars_ptr with Import, Convention => C, External_Name => "cenv_list_topics";
   function C_Error return Interfaces.C.Strings.chars_ptr with Import, Convention => C, External_Name => "cenv_last_error";
   procedure C_Free (Value : Interfaces.C.Strings.chars_ptr) with Import, Convention => C, External_Name => "cenv_string_free";
   procedure Check (Result : Interfaces.C.int) is
   begin
      if Result = 0 then raise Client_Error with Interfaces.C.Strings.Value (C_Error); end if;
   end Check;
   function C_String (Text : String) return Interfaces.C.char_array is
   begin
      if not Ose_Text.Valid_UTF8 (Text) then raise Client_Error with "invalid UTF-8"; end if;
      for C of Text loop if C = Character'Val (0) then raise Client_Error with "NUL in string"; end if; end loop;
      return Interfaces.C.To_C (Text);
   end C_String;
   function Take_String (Value : Interfaces.C.Strings.chars_ptr) return String is
   begin
      if Value = Interfaces.C.Strings.Null_Ptr then Check (0); end if;
      declare Result : constant String := Interfaces.C.Strings.Value (Value);
      begin C_Free (Value); return Result; end;
   end Take_String;
   procedure Connect (Self : in out Client; Host : String; Port : Interfaces.Unsigned_16; Id : String) is
      P : System.Address;
   begin
      if Self.Native /= System.Null_Address then raise Client_Error with "client already connected"; end if;
      P := C_Connect (C_String (Host), Port, C_String (Id));
      if P = System.Null_Address then Check (0); end if;
      Self.Native := Wrap (P, 0);
      if Self.Native = System.Null_Address then raise Storage_Error; end if;
   end Connect;
   procedure Close (Self : in out Client) is
   begin
      if Self.Native /= System.Null_Address then
         Enter_Ada; C_Close (Raw (Self.Native)); Leave_Ada;
      end if;
   end Close;
   overriding procedure Finalize (Self : in out Client) is
   begin
      Close (Self); Release (Self.Native); Self.Native := System.Null_Address;
   end Finalize;
   procedure Run (Self : in out Client) is
      Result : Interfaces.C.int;
   begin
      Result := C_Run (Raw (Self.Native));
      if Result = 0 then
         declare Detail : constant String := Take_String (Failure (Self.Native));
         begin if Detail'Length > 0 then raise Client_Error with Detail; end if; end;
      end if;
      Check (Result);
   end Run;
   function List_Topics (Self : Client) return String is
   begin return Take_String (C_Topics (Raw (Self.Native))); end List_Topics;
   function Latest_Options return Options is
      Result : Options;
   begin Result.Buffer := Latest; Result.Capacity := 1; Result.Overflow := Drop_Oldest; return Result; end;
   function Policy (Value : Options) return String is
      function N (V : Natural) return String is (Ada.Strings.Fixed.Trim (Natural'Image (V), Ada.Strings.Both));
      function Overflow return String is
      begin case Value.Overflow is when Reject_New => return "reject_new"; when Drop_Oldest => return "drop_oldest"; when Wait => return "wait"; end case; end;
   begin
      return "{""buffer"":""" & (if Value.Buffer = Latest then "latest" else "fifo") & """,""capacity"":" & N (Value.Capacity) &
        ",""overflow"":""" & Overflow & """,""ack"":""" & (if Value.Ack_Received then "received" else "none") &
        """,""max_retries"":" & N (Value.Max_Retries) & ",""ack_timeout_ms"":" & N (Value.Ack_Timeout_Ms) &
        ",""backoff_multiplier"":" & N (Value.Backoff_Multiplier) & ",""wait_timeout_ms"":" & N (Value.Wait_Timeout_Ms) &
        ",""transport"":""" & (if Value.UDP then "udp" else "tcp") & """}";
   end Policy;
end Ose_Client;
