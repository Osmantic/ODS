#!/bin/bash
# Bash completion for ods-cli
# Source this file or place in /etc/bash_completion.d/ or ~/.local/share/bash-completion/completions/

# Remember the installed copy even if the user later changes directory.
_ods_completion_source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"

_ods_complete_presets() {
    local install_root="${INSTALL_DIR:-${ODS_HOME:-}}" path name
    if [[ -z "$install_root" && -n "${ODS_SCRIPT_HINT:-}" && -f "$ODS_SCRIPT_HINT/.env" ]]; then
        install_root="$ODS_SCRIPT_HINT"
    fi
    install_root="${install_root:-${ODS_INSTALL_DIR:-}}"
    if [[ -z "$install_root" ]]; then
        if [[ -f "$_ods_completion_source_root/docker-compose.base.yml" || -f "$_ods_completion_source_root/docker-compose.yml" ]]; then
            install_root="$_ods_completion_source_root"
        else
            install_root="$HOME/ods"
        fi
    fi
    local preset_dir="$install_root/presets"
    COMPREPLY=()
    # Names are directory names, not .preset files. Keep each array element
    # intact (including spaces), without changing the user's glob options.
    for path in "$preset_dir"/[!.]* "$preset_dir"/.[!.]* "$preset_dir"/..?*; do
        [[ -d "$path" ]] || continue
        name="${path##*/}"
        [[ "$name" == "$cur"* ]] && COMPREPLY+=("$name")
    done
    compopt -o filenames 2>/dev/null || true
}

_ods_completion() {
    local cur prev words cword
    _init_completion || return

    # Main commands and their aliases
    local main_commands="gpu status status-json list enable disable preset mode model backup restore logs restart start stop update shell config chat benchmark doctor help version"
    local aliases="g s ls p m l r u sh cfg c bench b diag d h v"

    # Service names (from ods-cli aliases section)
    local services="ape policy guard embeddings embed llama-server llm n8n workflows open-webui web ui webui opencode opencode-web qdrant vector searxng search tts kokoro whisper voice stt"

    case $cword in
        1)
            # Complete main commands and aliases
            COMPREPLY=($(compgen -W "$main_commands $aliases" -- "$cur"))
            return 0
            ;;
        2)
            case $prev in
                gpu|g)
                    COMPREPLY=($(compgen -W "status topology assignment validate reassign help" -- "$cur"))
                    return 0
                    ;;
                preset|p)
                    COMPREPLY=($(compgen -W "save load list delete export import diff" -- "$cur"))
                    return 0
                    ;;
                mode|m)
                    COMPREPLY=($(compgen -W "local cloud hybrid" -- "$cur"))
                    return 0
                    ;;
                model)
                    COMPREPLY=($(compgen -W "current list swap" -- "$cur"))
                    return 0
                    ;;
                config|cfg)
                    COMPREPLY=($(compgen -W "show edit validate" -- "$cur"))
                    return 0
                    ;;
                backup)
                    COMPREPLY=($(compgen -W "verify -c -l --compress --list" -- "$cur"))
                    return 0
                    ;;
                doctor|diag|d)
                    COMPREPLY=($(compgen -W "--json" -- "$cur"))
                    return 0
                    ;;
                enable|disable|logs|log|l|restart|r|start|stop|shell|sh)
                    # Complete with service names
                    COMPREPLY=($(compgen -W "$services" -- "$cur"))
                    return 0
                    ;;
                restore)
                    # Complete with backup IDs (if .backups directory exists)
                    local backup_dir="${ODS_HOME:-$HOME/ods}/.backups"
                    if [[ -d "$backup_dir" ]]; then
                        local backup_ids=$(ls -1 "$backup_dir" 2>/dev/null | grep -E '^[0-9]{8}-[0-9]{6}' | sort -r)
                        COMPREPLY=($(compgen -W "$backup_ids" -- "$cur"))
                    fi
                    return 0
                    ;;
            esac
            ;;
        3)
            case "${words[1]}" in
                gpu|g)
                    case $prev in
                        reassign)
                            COMPREPLY=($(compgen -W "--auto --manual --dry-run" -- "$cur"))
                            return 0
                            ;;
                        topology|topo|t)
                            COMPREPLY=($(compgen -W "--force" -- "$cur"))
                            return 0
                            ;;
                    esac
                    ;;
                preset|p)
                    case $prev in
                        save|s|load|l|delete|rm|export|e|diff|d)
                            _ods_complete_presets
                            return 0
                            ;;
                        import)
                            # Complete with .tar.gz files
                            COMPREPLY=($(compgen -f -X '!*.tar.gz' -- "$cur"))
                            return 0
                            ;;
                    esac
                    ;;
                model)
                    case $prev in
                        swap)
                            # Complete with available tiers (0-4)
                            COMPREPLY=($(compgen -W "0 1 2 3 4" -- "$cur"))
                            return 0
                            ;;
                    esac
                    ;;
                backup)
                    case $prev in
                        verify)
                            # Complete with backup IDs for verification
                            local backup_dir="${ODS_HOME:-$HOME/ods}/.backups"
                            if [[ -d "$backup_dir" ]]; then
                                local backup_ids=$(ls -1 "$backup_dir" 2>/dev/null | grep -E '^[0-9]{8}-[0-9]{6}' | sort -r)
                                COMPREPLY=($(compgen -W "$backup_ids" -- "$cur"))
                            fi
                            return 0
                            ;;
                    esac
                    ;;
            esac
            ;;
        4)
            case "${words[1]}" in
                preset|p)
                    case "${words[2]}" in
                        diff|d)
                            _ods_complete_presets
                            return 0
                            ;;
                        export|e)
                            # Complete with .tar.gz filename for export destination
                            COMPREPLY=($(compgen -f -X '!*.tar.gz' -- "$cur"))
                            return 0
                            ;;
                    esac
                    ;;
            esac
            ;;
    esac

    # Default to no completion
    return 0
}

# Register the completion function
complete -F _ods_completion ods
complete -F _ods_completion ./ods-cli

# Also register for common installation paths
complete -F _ods_completion ~/ods/ods-cli
complete -F _ods_completion /opt/ods/ods-cli
