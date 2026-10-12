function failure = sdl_recording_callback(consumer, record)
% Preserve the application exception before crossing the C MEX boundary.
% Octave's mexCallMATLABWithTrap replaces it with a generic Octave:MEX error.
failure = [];
try
    consumer(record);
catch err
    failure = err;
end
end
