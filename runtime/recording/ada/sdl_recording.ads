with Ada.Finalization;
with Ada.Containers.Vectors;
with Ada.Strings.Unbounded;
with Interfaces;
with System;
package SDL_Recording is
   Recording_Error : exception;
   subtype Text is Ada.Strings.Unbounded.Unbounded_String;
   subtype UInt64 is Interfaces.Unsigned_64;
   subtype UInt32 is Interfaces.Unsigned_32;
   subtype UInt16 is Interfaces.Unsigned_16;
   type Bytes is array (Positive range <>) of Interfaces.Unsigned_8;
   package Byte_Vectors is new Ada.Containers.Vectors
     (Positive, Interfaces.Unsigned_8, Interfaces."=");
   type Key_Value is record
      Key, Value : Text;
   end record;
   type Metadata_Array is array (Positive range <>) of Key_Value;
   Empty_Metadata : constant Metadata_Array (1 .. 0) := (others => (others => <>));
   package Metadata_Vectors is new Ada.Containers.Vectors (Positive, Key_Value);
   type Recording_Record is record
      Channel_Id, Schema_Id : UInt16 := 0;
      Topic, Root_Type, Catalogue : Text;
      Payload : Byte_Vectors.Vector;
      Metadata : Metadata_Vectors.Vector;
      Log_Time_Ns, Publish_Time_Ns : UInt64 := 0;
      Sequence : UInt32 := 0;
   end record;
   type Writer is new Ada.Finalization.Limited_Controlled with private;
   type Reader is new Ada.Finalization.Limited_Controlled with private;
   type Recording_Format is (MCAP, PCAPNG);
   function Create (Path : String; Overwrite : Boolean := False;
                    Chunk_Size : UInt64 := 1_048_576;
                    Format : Recording_Format := MCAP) return Writer;
   function Open (Path : String) return Reader;
   function Now return UInt64;
   function Validate (Path : String) return UInt64;
   function Register_Channel (W : in out Writer; Topic, Catalogue, Root_Type : String;
                              Metadata : Metadata_Array := Empty_Metadata) return UInt16;
   procedure Write_Metadata (W : in out Writer; Name : String; Metadata : Metadata_Array);
   procedure Validate_Payload (W : in out Writer; Enabled : Boolean);
   procedure Write_Encoded (W : in out Writer; Channel : UInt16; Payload : Bytes;
                            Log_Time_Ns, Publish_Time_Ns : UInt64; Sequence : UInt32 := 0);
   procedure Write_Encoded (W : in out Writer; Channel : UInt16; Payload : Bytes);
   procedure Flush (W : in out Writer; Sync : Boolean := False);
   procedure Close (W : in out Writer);
   -- UTF-8 Strings are counted bytes; payloads are complete SDL2, without S2PS.
   -- An empty topic selects all topics; times describe [start,end).
   procedure Filter (R : in out Reader; Topic : String := "";
                     Has_Start : Boolean := False; Start_Time_Ns : UInt64 := 0;
                     Has_End : Boolean := False; End_Time_Ns : UInt64 := 0);
   function Next (R : in out Reader; Item : out Recording_Record) return Boolean;
   function Replay (R : in out Reader; Speed : Long_Float;
                    Consumer : not null access procedure (Item : Recording_Record)) return UInt64;
   procedure Close (R : in out Reader);
   function To_Bytes (Value : Byte_Vectors.Vector) return Bytes;
   -- Explicit Close reports IO errors; finalization only performs best-effort cleanup.
   overriding procedure Finalize (W : in out Writer);
   overriding procedure Finalize (R : in out Reader);
private
   type Writer is new Ada.Finalization.Limited_Controlled with record
      Handle : System.Address := System.Null_Address;
   end record;
   type Reader is new Ada.Finalization.Limited_Controlled with record
      Handle : System.Address := System.Null_Address;
      Busy : Boolean := False;
   end record;
end SDL_Recording;
