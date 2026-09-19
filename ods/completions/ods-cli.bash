#!/bin/bash
# Bash completion for ods-cli
# Source this file or place in /etc/bash_completion.d/ or ~/.local/share/bash-completion/completions/

_ods_completion() {
    local cur prev words cword
    _init_completion || return

    # Main commands and their aliases (kept in sync with the ods-cli dispatcher)
    local main_commands="gpu status status-json list enable disable purge preset mode model remote-provider stt backup restore rollback logs restart repair start stop update shell config chat benchmark doctor audit template agent help version"
    local aliases="g s ls p m l r u sh cfg c bench b diag d h v log fix tmpl"

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
                    COMPREPLY=($(compgen -W "save load list delete export import" -- "$cur"))
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
                enable|disable|purge|logs|log|l|restart|r|start|stop|shell|sh)
                    # Complete with service names
                    COMPREPLY=($(compgen -W "$services" -- "$cur"))
                    return 0
                    ;;
                remote-provider)
                    COMPREPLY=($(compgen -W "status plan configure test enable disable remove peer-models peer-model" -- "$cur"))
                    return 0
                    ;;
                stt)
                    COMPREPLY=($(compgen -W "current status download" -- "$cur"))
                    return 0
                    ;;
                template|tmpl)
                    COMPREPLY=($(compgen -W "list preview apply" -- "$cur"))
                    return 0
                    ;;
                audit)
                    COMPREPLY=($(compgen -W "extensions --json --strict" -- "$cur"))
                    return 0
                    ;;
                repair|fix)
                    COMPREPLY=($(compgen -W "voice stt tts hermes-workers slash-workers rootless-ownership rootless" -- "$cur"))
                    return 0
                    ;;
                agent)
                    COMPREPLY=($(compgen -W "status start stop restart logs" -- "$cur"))
                    return 0
                    ;;
                rollback)
                    # Rollback targets: pre-update snapshots in data/backups and
                    # general backups in ~/.ods/backups; ods-update.sh accepts
                    # the full name or the name minus its pre-update-/backup-
                    # prefix, so offer both forms.
                    local snap_dir="${ODS_HOME:-$HOME/ods}/data/backups"
                    local backup_dir="$HOME/.ods/backups"
                    local targets=$( {
                        [[ -d "$snap_dir" ]] && ls -1 "$snap_dir" 2>/dev/null
                        [[ -d "$backup_dir" ]] && ls -1 "$backup_dir" 2>/dev/null
                    } | grep -E '^(pre-update|backup)-' | sort -r)
                    local short=$(printf '%s\n' "$targets" | sed -E 's/^pre-update-//; s/^backup-//')
                    COMPREPLY=($(compgen -W "$targets $short" -- "$cur"))
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
                        save|load|delete)
                            # Complete with existing preset names
                            local preset_dir="${ODS_HOME:-$HOME/ods}/.presets"
                            if [[ -d "$preset_dir" ]]; then
                                local presets=$(ls -1 "$preset_dir" 2>/dev/null | sed 's/\.preset$//')
                                COMPREPLY=($(compgen -W "$presets" -- "$cur"))
                            fi
                            return 0
                            ;;
                        export)
                            # Complete with existing preset names for export
                            local preset_dir="${ODS_HOME:-$HOME/ods}/.presets"
                            if [[ -d "$preset_dir" ]]; then
                                local presets=$(ls -1 "$preset_dir" 2>/dev/null | sed 's/\.preset$//')
                                COMPREPLY=($(compgen -W "$presets" -- "$cur"))
                            fi
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
                        export)
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