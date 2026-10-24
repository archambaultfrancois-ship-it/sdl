with Chain_Text;
with Interfaces.C;
with Interfaces.C.Strings;
package body Chain_Parameters is
   use Ada.Strings.Unbounded;
   use type System.Address;
   use type Interfaces.C.Strings.chars_ptr;
   package CS renames Interfaces.C.Strings;
   function C_Connect (Host : CS.chars_ptr; Port : Interfaces.Unsigned_16; Id, Token, CA, Certificate, Key : CS.chars_ptr) return System.Address
     with Import, Convention => C, External_Name => "cenv_parameters_connect";
   procedure C_Close (Handle : System.Address) with Import, Convention => C, External_Name => "cenv_parameters_free";
   function C_Request (Handle : System.Address; Op, Args : Interfaces.C.char_array; Data : System.Address; Size : Interfaces.C.size_t) return System.Address
     with Import, Convention => C, External_Name => "cenv_parameters_request";
   function C_Header (Handle : System.Address) return CS.chars_ptr with Import, Convention => C, External_Name => "cenv_parameter_packet_header";
   function C_Data (Handle : System.Address) return System.Address with Import, Convention => C, External_Name => "cenv_parameter_packet_data";
   function C_Size (Handle : System.Address) return Interfaces.C.size_t with Import, Convention => C, External_Name => "cenv_parameter_packet_size";
   function C_Text (Handle : System.Address; Key : Interfaces.C.char_array) return CS.chars_ptr
     with Import, Convention => C, External_Name => "cenv_parameter_packet_text";
   procedure C_Free_Text (Text : CS.chars_ptr) with Import, Convention => C, External_Name => "cenv_string_free";
   procedure C_Free (Handle : System.Address) with Import, Convention => C, External_Name => "cenv_parameter_packet_free";
   function C_Error return CS.chars_ptr with Import, Convention => C, External_Name => "cenv_last_error";
   function Checked (Text : String) return Interfaces.C.char_array is
   begin
      if not Chain_Text.Valid_UTF8 (Text) then raise Parameter_Error with "invalid UTF-8"; end if;
      for C of Text loop if C = Character'Val (0) then raise Parameter_Error with "NUL in string"; end if; end loop;
      return Interfaces.C.To_C (Text);
   end Checked;
   function Path_Arguments (Path : String) return String is
   begin
      if Path = "" then raise Parameter_Error with "empty parameter path"; end if;
      for C of Path loop
         if C not in 'A' .. 'Z' | 'a' .. 'z' | '0' .. '9' | '_' | '-' | '/' then
            raise Parameter_Error with "invalid parameter path";
         end if;
      end loop;
      return "{""path"":""" & Path & """}";
   end Path_Arguments;
   function Optional (Text : String) return CS.chars_ptr is
   begin
      if Text = "" then return CS.Null_Ptr; end if;
      return CS.New_Char_Array (Checked (Text));
   end Optional;
   procedure Connect (Self : in out Client; Host : String; Port : Interfaces.Unsigned_16;
                      Id : String; Token : String := ""; CA : String := "";
                      Certificate : String := ""; Key : String := "") is
      Values : array (1 .. 6) of CS.chars_ptr := (others => CS.Null_Ptr);
   begin
      if Self.Native /= System.Null_Address then raise Parameter_Error with "client already connected"; end if;
      Values (1) := Optional (Host); Values (2) := Optional (Id); Values (3) := Optional (Token);
      Values (4) := Optional (CA); Values (5) := Optional (Certificate); Values (6) := Optional (Key);
      Self.Native := C_Connect (Values (1), Port, Values (2), Values (3), Values (4), Values (5), Values (6));
      for V of Values loop CS.Free (V); end loop;
      if Self.Native = System.Null_Address then raise Parameter_Error with CS.Value (C_Error); end if;
   exception when others =>
      for V of Values loop CS.Free (V); end loop;
      raise;
   end Connect;
   procedure Close (Self : in out Client) is
   begin
      if Self.Native /= System.Null_Address then C_Close (Self.Native); Self.Native := System.Null_Address; end if;
   end Close;
   overriding procedure Finalize (Self : in out Client) is begin Close (Self); end Finalize;
   function Request (Self : Client; Operation : String; Arguments : String := "{}";
                     Payload : SDL_Runtime.Bytes := (1 .. 0 => 0)) return Packet is
      Handle : System.Address := System.Null_Address;
      Result : Packet;
      function Field (Name : String) return Unbounded_String is
         Text : CS.chars_ptr := C_Text (Handle, Checked (Name));
         Value : Unbounded_String;
      begin
         if Text /= CS.Null_Ptr then
            Value := To_Unbounded_String (CS.Value (Text)); C_Free_Text (Text);
         end if;
         return Value;
      end Field;
   begin
      if Self.Native = System.Null_Address then raise Parameter_Error with "parameter client closed"; end if;
      Handle := C_Request (Self.Native, Checked (Operation), Checked (Arguments), Payload'Address, Interfaces.C.size_t (Payload'Length));
      if Handle = System.Null_Address then raise Parameter_Error with CS.Value (C_Error); end if;
      Result.Header := To_Unbounded_String (CS.Value (C_Header (Handle)));
      Result.Path := Field ("path"); Result.Root := Field ("root");
      declare
         Data : constant System.Address := C_Data (Handle);
         Bytes : SDL_Runtime.Bytes (1 .. Natural (C_Size (Handle))) with Import, Address => Data;
      begin
         for B of Bytes loop Result.Payload.Append (B); end loop;
      end;
      C_Free (Handle); Handle := System.Null_Address; return Result;
   exception when others =>
      if Handle /= System.Null_Address then C_Free (Handle); end if;
      raise;
   end Request;
   function List (Self : Client) return String is
      Reply : constant Packet := Request (Self, "parameters_list");
   begin return To_String (Reply.Header); end List;
   procedure Subscribe (Self : Client; Paths_JSON : String) is
      Reply : constant Packet := Request (Self, "parameters_subscribe", "{""paths"":" & Paths_JSON & "}");
      pragma Unreferenced (Reply);
   begin null; end Subscribe;
   function Next (Self : Client) return Packet is (Request (Self, "parameters_next"));
   procedure Unsubscribe (Self : Client) is
      Reply : constant Packet := Request (Self, "parameters_unsubscribe");
      pragma Unreferenced (Reply);
   begin null; end Unsubscribe;
end Chain_Parameters;
