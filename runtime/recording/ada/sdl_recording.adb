with Ada.Exceptions;
with Ada.Unchecked_Deallocation;
with Interfaces.C;
with Interfaces.C.Strings;
with System.Address_To_Access_Conversions;
with System.Storage_Elements;
package body SDL_Recording is
   use type System.Address;
   use type Interfaces.C.int;
   use type Interfaces.C.size_t;
   use type UInt64;
   use type Interfaces.Unsigned_8;
   use Ada.Strings.Unbounded;
   subtype C_Int is Interfaces.C.int;
   subtype C_Size is Interfaces.C.size_t;
   subtype C_Flag is Interfaces.Unsigned_8;
   type C_Bytes is record
      Data : System.Address;
      Size : C_Size;
   end record;
   pragma Convention (C_Pass_By_Copy, C_Bytes);
   type C_Metadata is record
      Key, Value : C_Bytes;
   end record;
   pragma Convention (C, C_Metadata);
   type C_Metadata_Array is array (Positive range <>) of aliased C_Metadata;
   pragma Convention (C, C_Metadata_Array);
   type C_Record is record
      Channel_Id, Schema_Id : UInt16;
      Topic, Root_Type, Catalogue, Payload : C_Bytes;
      Metadata : System.Address;
      Metadata_Count : C_Size;
      Log_Time_Ns, Publish_Time_Ns : UInt64;
      Sequence : UInt32;
   end record;
   pragma Convention (C, C_Record);
   function Error_Text return Interfaces.C.Strings.chars_ptr;
   pragma Import (C, Error_Text, "sdl_recording_error");
   function C_Now (Result : access UInt64) return C_Int;
   pragma Import (C, C_Now, "sdl_recording_now");
   function C_Validate (Path : C_Bytes; Result : access UInt64) return C_Int;
   pragma Import (C, C_Validate, "sdl_recording_validate");
   function W_Open (Path : C_Bytes; Overwrite : C_Flag; Chunk : UInt64; Format : C_Flag) return System.Address;
   pragma Import (C, W_Open, "sdl_recording_writer_open_format");
   function W_Channel (Handle : System.Address; Topic, Catalogue, Root : C_Bytes;
                       Metadata : System.Address; Count : C_Size; Result : access UInt16) return C_Int;
   pragma Import (C, W_Channel, "sdl_recording_writer_channel");
   function W_Metadata (Handle : System.Address; Name : C_Bytes;
                        Metadata : System.Address; Count : C_Size) return C_Int;
   pragma Import (C, W_Metadata, "sdl_recording_writer_metadata");
   function W_Validate (Handle : System.Address; Flag : C_Flag) return C_Int;
   pragma Import (C, W_Validate, "sdl_recording_writer_validate_payload");
   function W_Write (Handle : System.Address; Channel : UInt16; Payload : C_Bytes;
                     Log, Publish : UInt64; Sequence : UInt32) return C_Int;
   pragma Import (C, W_Write, "sdl_recording_writer_write");
   function W_Flush (Handle : System.Address; Sync : C_Flag) return C_Int;
   pragma Import (C, W_Flush, "sdl_recording_writer_flush");
   function W_Close (Handle : System.Address) return C_Int;
   pragma Import (C, W_Close, "sdl_recording_writer_close");
   function R_Open (Path : C_Bytes) return System.Address;
   pragma Import (C, R_Open, "sdl_recording_reader_open");
   function R_Filter (Handle : System.Address; Topic : C_Bytes; Flags : C_Flag;
                      Start_Time, End_Time : UInt64) return C_Int;
   pragma Import (C, R_Filter, "sdl_recording_reader_filter");
   function R_Next (Handle : System.Address; Result : access System.Address) return C_Int;
   pragma Import (C, R_Next, "sdl_recording_reader_next");
   function R_Close (Handle : System.Address) return C_Int;
   pragma Import (C, R_Close, "sdl_recording_reader_close");
   type C_Consumer is access function (Item, Data : System.Address) return C_Int;
   pragma Convention (C, C_Consumer);
   function R_Replay (Handle : System.Address; Speed : Interfaces.C.double;
                      Consumer : C_Consumer; Data : System.Address; Result : access UInt64) return C_Int;
   pragma Import (C, R_Replay, "sdl_recording_replay");
   procedure Check (Status : C_Int) is
   begin
      if Status < 0 then
         raise Recording_Error with Interfaces.C.Strings.Value (Error_Text);
      end if;
   end Check;
   function View (Value : String) return C_Bytes is
     ((Value'Address, C_Size (Value'Length)));
   function View (Value : Bytes) return C_Bytes is
     ((Value'Address, C_Size (Value'Length)));
   function Flag (Value : Boolean) return C_Flag is (C_Flag (Boolean'Pos (Value)));
   procedure Ready (R : Reader) is
   begin
      if R.Handle = System.Null_Address or else R.Busy then
         raise Recording_Error with "closed or replaying reader";
      end if;
   end Ready;
   function Now return UInt64 is
      Result : aliased UInt64 := 0;
   begin
      Check (C_Now (Result'Access)); return Result;
   end Now;
   function Validate (Path : String) return UInt64 is
      Result : aliased UInt64 := 0;
   begin
      Check (C_Validate (View (Path), Result'Access)); return Result;
   end Validate;
   function Create (Path : String; Overwrite : Boolean := False;
                    Chunk_Size : UInt64 := 1_048_576;
                    Format : Recording_Format := MCAP) return Writer is
   begin
      return Result : Writer do
         Result.Handle := W_Open (View (Path), Flag (Overwrite), Chunk_Size, C_Flag (Recording_Format'Pos (Format)));
         if Result.Handle = System.Null_Address then Check (-1); end if;
      end return;
   end Create;
   function Open (Path : String) return Reader is
   begin
      return Result : Reader do
         Result.Handle := R_Open (View (Path));
         if Result.Handle = System.Null_Address then Check (-1); end if;
      end return;
   end Open;
   -- Keep counted UTF-8 storage alive throughout the C call.
   type String_Access is access String;
   procedure Free is new Ada.Unchecked_Deallocation (String, String_Access);
   type Text_Array is array (Positive range <>) of String_Access;
   procedure Build (Input : Metadata_Array; Texts : in out Text_Array; Values : out C_Metadata_Array) is
      Position : Natural := 0;
   begin
      for Item of Input loop
         Position := Position + 1;
         Texts (2 * Position - 1) := new String'(To_String (Item.Key));
         Texts (2 * Position) := new String'(To_String (Item.Value));
         Values (Position) := (View (Texts (2 * Position - 1).all), View (Texts (2 * Position).all));
      end loop;
   end Build;
   procedure Release (Texts : in out Text_Array) is
   begin
      for Value of Texts loop Free (Value); end loop;
   end Release;
   function Register_Channel (W : in out Writer; Topic, Catalogue, Root_Type : String;
                              Metadata : Metadata_Array := Empty_Metadata) return UInt16 is
      Texts : Text_Array (1 .. 2 * Metadata'Length) := (others => null);
      Values : C_Metadata_Array (1 .. Metadata'Length);
      Result : aliased UInt16 := 0;
   begin
      Build (Metadata, Texts, Values);
      Check (W_Channel (W.Handle, View (Topic), View (Catalogue), View (Root_Type),
                        Values'Address, C_Size (Values'Length), Result'Access));
      Release (Texts); return Result;
   exception when others => Release (Texts); raise;
   end Register_Channel;
   procedure Write_Metadata (W : in out Writer; Name : String; Metadata : Metadata_Array) is
      Texts : Text_Array (1 .. 2 * Metadata'Length) := (others => null);
      Values : C_Metadata_Array (1 .. Metadata'Length);
   begin
      Build (Metadata, Texts, Values);
      Check (W_Metadata (W.Handle, View (Name), Values'Address, C_Size (Values'Length)));
      Release (Texts);
   exception when others => Release (Texts); raise;
   end Write_Metadata;
   procedure Validate_Payload (W : in out Writer; Enabled : Boolean) is
   begin
      Check (W_Validate (W.Handle, Flag (Enabled)));
   end Validate_Payload;
   procedure Write_Encoded (W : in out Writer; Channel : UInt16; Payload : Bytes;
                            Log_Time_Ns, Publish_Time_Ns : UInt64; Sequence : UInt32 := 0) is
   begin
      Check (W_Write (W.Handle, Channel, View (Payload), Log_Time_Ns, Publish_Time_Ns, Sequence));
   end Write_Encoded;
   procedure Write_Encoded (W : in out Writer; Channel : UInt16; Payload : Bytes) is
      Timestamp : constant UInt64 := Now;
   begin
      Write_Encoded (W, Channel, Payload, Timestamp, Timestamp);
   end Write_Encoded;
   procedure Flush (W : in out Writer; Sync : Boolean := False) is
   begin
      Check (W_Flush (W.Handle, Flag (Sync)));
   end Flush;
   procedure Close (W : in out Writer) is
      Handle : constant System.Address := W.Handle;
   begin
      if Handle /= System.Null_Address then
         W.Handle := System.Null_Address; Check (W_Close (Handle));
      end if;
   end Close;
   procedure Filter (R : in out Reader; Topic : String := "";
                     Has_Start : Boolean := False; Start_Time_Ns : UInt64 := 0;
                     Has_End : Boolean := False; End_Time_Ns : UInt64 := 0) is
   begin
      Ready (R);
      Check (R_Filter (R.Handle, View (Topic), Flag (Has_Start) + 2 * Flag (Has_End),
                       Start_Time_Ns, End_Time_Ns));
   end Filter;
   package Byte_Pointers is new System.Address_To_Access_Conversions (Interfaces.Unsigned_8);
   package Record_Pointers is new System.Address_To_Access_Conversions (C_Record);
   package Metadata_Pointers is new System.Address_To_Access_Conversions (C_Metadata);
   use System.Storage_Elements;
   function Copy_Bytes (Value : C_Bytes) return Bytes is
      Result : Bytes (1 .. Natural (Value.Size));
   begin
      for I in Result'Range loop
         Result (I) := Byte_Pointers.To_Pointer (Value.Data + Storage_Offset (I - 1)).all;
      end loop;
      return Result;
   end Copy_Bytes;
   function Copy_Text (Value : C_Bytes) return Text is
      Data : constant Bytes := Copy_Bytes (Value);
      Result : String (Data'Range);
   begin
      for I in Result'Range loop Result (I) := Character'Val (Data (I)); end loop;
      return To_Unbounded_String (Result);
   end Copy_Text;
   function Copy_Record (Pointer : System.Address) return Recording_Record is
      Input : constant C_Record := Record_Pointers.To_Pointer (Pointer).all;
      Result : Recording_Record;
   begin
      Result.Channel_Id := Input.Channel_Id; Result.Schema_Id := Input.Schema_Id;
      Result.Topic := Copy_Text (Input.Topic); Result.Root_Type := Copy_Text (Input.Root_Type);
      Result.Catalogue := Copy_Text (Input.Catalogue);
      for Value of Copy_Bytes (Input.Payload) loop Result.Payload.Append (Value); end loop;
      for I in 1 .. Natural (Input.Metadata_Count) loop
         declare
            Value : constant C_Metadata := Metadata_Pointers.To_Pointer
              (Input.Metadata + Storage_Offset ((I - 1) * (C_Metadata'Size / System.Storage_Unit))).all;
         begin
            Result.Metadata.Append (Key_Value'(Copy_Text (Value.Key), Copy_Text (Value.Value)));
         end;
      end loop;
      Result.Log_Time_Ns := Input.Log_Time_Ns; Result.Publish_Time_Ns := Input.Publish_Time_Ns;
      Result.Sequence := Input.Sequence; return Result;
   end Copy_Record;
   function Next (R : in out Reader; Item : out Recording_Record) return Boolean is
      Pointer : aliased System.Address := System.Null_Address;
      Status : C_Int;
   begin
      Ready (R); Status := R_Next (R.Handle, Pointer'Access); Check (Status);
      Item := (others => <>);
      if Status = 0 then return False; end if;
      Item := Copy_Record (Pointer); return True;
   end Next;
   --  The synchronous replay borrows the callback for the lifetime of State.
   --  An access discriminant preserves the caller's accessibility level.
   type Replay_State (Consumer : not null access procedure (Item : Recording_Record)) is limited record
      Failed : Boolean := False;
      Failure : Ada.Exceptions.Exception_Occurrence;
   end record;
   package State_Pointers is new System.Address_To_Access_Conversions (Replay_State);
   function Consume (Item, Data : System.Address) return C_Int;
   pragma Convention (C, Consume);
   function Consume (Item, Data : System.Address) return C_Int is
      State : constant State_Pointers.Object_Pointer := State_Pointers.To_Pointer (Data);
   begin
      State.Consumer.all (Copy_Record (Item)); return 0;
   exception when E : others =>
      State.Failed := True; Ada.Exceptions.Save_Occurrence (State.Failure, E); return 1;
   end Consume;
   function Replay (R : in out Reader; Speed : Long_Float;
                    Consumer : not null access procedure (Item : Recording_Record)) return UInt64 is
      State : aliased Replay_State (Consumer);
      Count : aliased UInt64 := 0;
      Status : C_Int;
      Entered : Boolean := False;
   begin
      Ready (R); R.Busy := True; Entered := True;
      Status := R_Replay (R.Handle, Interfaces.C.double (Speed), Consume'Access, State'Address, Count'Access);
      R.Busy := False;
      if State.Failed then Ada.Exceptions.Reraise_Occurrence (State.Failure); end if;
      Check (Status); return Count;
   exception when others =>
      if Entered then R.Busy := False; end if;
      raise;
   end Replay;
   procedure Close (R : in out Reader) is
   begin
      if R.Busy then raise Recording_Error with "reader is replaying"; end if;
      if R.Handle /= System.Null_Address then
         Check (R_Close (R.Handle)); R.Handle := System.Null_Address;
      end if;
   end Close;
   function To_Bytes (Value : Byte_Vectors.Vector) return Bytes is
      Result : Bytes (1 .. Natural (Value.Length));
   begin
      for I in Result'Range loop Result (I) := Value.Element (I); end loop;
      return Result;
   end To_Bytes;
   overriding procedure Finalize (W : in out Writer) is
   begin
      Close (W);
   exception when others => null;
   end Finalize;
   overriding procedure Finalize (R : in out Reader) is
   begin
      Close (R);
   exception when others => null;
   end Finalize;
end SDL_Recording;
