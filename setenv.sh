#!/usr/bin/env bash
# From the application root: source /path/to/framework/setenv.sh
# Optional settings before sourcing: OSE_APP_ROOT and OSE_PYTHON.

if [ -z "${BASH_VERSION:-}" ]; then
    printf '%s\n' 'setenv.sh requires Bash: source it from a Bash terminal.' >&2
    return 1 2>/dev/null || exit 1
fi
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf '%s\n' 'Use: source /path/to/framework/setenv.sh' >&2
    exit 1
fi

export FRAMEWORK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export SDL_ROOT="$FRAMEWORK_ROOT/sdl"
export OSE_ROOT="$FRAMEWORK_ROOT/ose"
export OSE_APP_ROOT="${OSE_APP_ROOT:-$PWD}"
export OSE_PYTHON="${OSE_PYTHON:-python3}"

if [[ ! -f "$OSE_ROOT/src/ose/cli.py" || ! -f "$SDL_ROOT/runtime/python/sdl_runtime.py" ]]; then
    printf 'Invalid framework checkout: %s\n' "$FRAMEWORK_ROOT" >&2
    return 1
fi
if [[ "$OSE_APP_ROOT" != /* ]]; then
    export OSE_APP_ROOT="$PWD/$OSE_APP_ROOT"
fi

# Include optional build directories even before they exist, so record can
# prepare them without requiring another source. Never add an empty path entry.
for _ose_env_path in \
    "$SDL_ROOT/build/recording-deps" \
    "$SDL_ROOT/build/filter/release" \
    "$SDL_ROOT/runtime/filter/python" \
    "$SDL_ROOT/runtime/python" \
    "$OSE_ROOT/src"; do
    case ":${PYTHONPATH-}:" in
        *":$_ose_env_path:"*) ;;
        *) export PYTHONPATH="$_ose_env_path${PYTHONPATH:+:$PYTHONPATH}" ;;
    esac
done
case ":$PATH:" in
    *":$SDL_ROOT/tools/recording:"*) ;;
    *) export PATH="$SDL_ROOT/tools/recording:$PATH" ;;
esac
unset _ose_env_path

_ose_env_cli() {
    "$OSE_PYTHON" -m ose.cli "$@"
}

_ose_env_make() (
    cd -- "$OSE_APP_ROOT" || return
    make "PYTHON=$OSE_PYTHON" "$@"
)

_ose_env_app_cd() {
    cd -- "$OSE_APP_ROOT"
}

_ose_env_app_run() (
    cd -- "$OSE_APP_ROOT" || return
    local argument
    local -a build_options=() run_options=()
    for argument in "$@"; do
        case "$argument" in
            DURATION=*) run_options=(--duration "${argument#DURATION=}") ;;
            -h|--help)
                printf '%s\n' 'Usage: app-run [DURATION=SECONDS] [MAKE_VARIABLE=VALUE ...]' \
                    'Build the application, then run until Ctrl+C unless DURATION is set.'
                return 0 ;;
            -*) printf 'Unsupported app-run option: %s\n' "$argument" >&2; return 2 ;;
            *=*) build_options+=("$argument") ;;
            *) printf 'Expected a Makefile assignment, got: %s\n' "$argument" >&2; return 2 ;;
        esac
    done
    make "PYTHON=$OSE_PYTHON" build "${build_options[@]}" || return
    "$OSE_PYTHON" -m ose.cli run --plan build/plan.json "${run_options[@]}"
)

_ose_env_record() (
    cd -- "$OSE_APP_ROOT" || return
    local prepare=1 argument
    for argument in "$@"; do
        case "$argument" in
            -h|--help) prepare=0; break ;;
        esac
    done
    if [[ "$prepare" == 1 ]]; then
        _ose_env_ensure_record_deps || return
    fi
    "$OSE_PYTHON" -m ose.cli record --sdl-root "$SDL_ROOT" "$@"
)

_ose_env_ensure_record_deps() {
    local -a targets=()
    if ! "$OSE_PYTHON" -c 'import sdl_recording' >/dev/null 2>&1; then
        targets+=(recording-deps)
    fi
    if ! "$OSE_PYTHON" -c 'import sdl_filter' >/dev/null 2>&1; then
        targets+=(filter-python)
    fi
    if [[ ${#targets[@]} == 0 ]]; then
        return 0
    fi
    printf 'Preparing recording services: %s\n' "${targets[*]}"
    make -C "$SDL_ROOT" "PYTHON=$OSE_PYTHON" "${targets[@]}" || return
    # Fail before connecting if the freshly prepared modules still cannot load.
    "$OSE_PYTHON" -c 'import sdl_recording, sdl_filter'
}

_ose_env_record_deps() {
    make -C "$SDL_ROOT" "PYTHON=$OSE_PYTHON" recording-deps filter-python "$@"
}

_ose_env_recording_tool() (
    cd -- "$OSE_APP_ROOT" || return
    "$OSE_PYTHON" "$SDL_ROOT/tools/recording/sdl-recording" "$@"
)

_ose_env_help() {
    printf 'Framework: %s\nApplication: %s\nPython: %s\n' \
        "$FRAMEWORK_ROOT" "$OSE_APP_ROOT" "$OSE_PYTHON"
    cat <<'EOF'

ose-ctl ...             OSE CLI from this framework checkout
app-cd                  enter the application repository
app-make ...            application Makefile (arguments passed through)
app-validate            validate the application
app-build               build the application
app-run [DURATION=...]   build and run locally; unlimited by default, Ctrl+C stops
app-simulate ...        run the application's simulation target
app-distributed ...     run its local distributed demo; default 300 seconds
record [OPTIONS...]     prepare missing services and open an external recorder
record-deps             manually rebuild/install recording and filter services
record-info FILE        show recording metadata
record-inspect FILE ... display all messages as text; --limit N bounds the count
record-check FILE       validate a recording
ose-env                 show these paths and shortcuts

Application shortcuts use its Makefile targets. Recorder commands run from the
application directory, including when invoked elsewhere. Each record invocation
opens a separate recorder; extra CLI options (host/port, TLS, commands) pass through.
Dependencies are checked at launch and only missing services are prepared.
The default endpoint is the application's build/endpoint.json when present.
Example commands inside record>:
  select alarm.event
  filter alarm.event machine_id == 2
  start
Stop before changing a filter.
EOF
}

alias ose-ctl='_ose_env_cli'
alias app-cd='_ose_env_app_cd'
alias app-make='_ose_env_make'
alias app-validate='_ose_env_make validate'
alias app-build='_ose_env_make build'
alias app-run='_ose_env_app_run'
alias app-simulate='_ose_env_make simulate'
alias app-distributed='_ose_env_make demo-distributed DURATION=300'
alias record-deps='_ose_env_record_deps'
alias record='_ose_env_record'
alias record-info='_ose_env_recording_tool info'
alias record-inspect='_ose_env_recording_tool inspect --format text'
alias record-check='_ose_env_recording_tool validate'
alias ose-env='_ose_env_help'

if [[ $- == *i* ]]; then
    printf 'SDL/OSE ready. Application: %s\nType ose-env for shortcuts.\n' "$OSE_APP_ROOT"
fi
