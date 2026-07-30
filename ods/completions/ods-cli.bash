#!/bin/bash
# Bash completion for ods-cli
# Source this file or place in /etc/bash_completion.d/ or ~/.local/share/bash-completion/completions/

_ods_completion() {
    local cur prev words cword
    _init_completion || return

    # Main commands and their aliases.
    # Keep in sync with the dispatch table at the bottom of ods-cli;
    # tests/test-cli-completion-parity.sh fails the build when they drift.
    local main_commands="gpu status status-json list enable disable purge preset mode model remote-provider stt backup restore rollback logs restart repair start stop update shell config chat benchmark doctor audit template agent help version"
    local aliases="g s ls p m l log r fix u sh cfg c bench b diag d tmpl h v"

    # Service ids and aliases, from extensions/services/*/manifest.yaml.
    # tests/test-cli-completion-parity.sh fails the build when they drift.
    local services="agent ape brave brave-search comfyui dashboard dashboard-api embed embeddings gateway guard hermes hermes-agent hermes-gate hermes-proxy kokoro langfuse litellm llama-server llm model-router n8n observability ods-proxy open-webui openclaw opencode opencode-web perplexica policy privacy-shield proxy qdrant remote remote-provider-egress remote-provider-ssh-tunnel search searxng stt tailscale token-spy traces tts ui vector voice vpn web webui whisper workflows"

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
                remote-provider)
                    COMPREPLY=($(compgen -W "status plan configure test disable remove peer-models help" -- "$cur"))
                    return 0
                    ;;
                stt)
                    COMPREPLY=($(compgen -W "current status download" -- "$cur"))
                    return 0
                    ;;
                repair|fix)
                    COMPREPLY=($(compgen -W "voice stt tts hermes-workers slash-workers" -- "$cur"))
                    return 0
                    ;;
                template|tmpl)
                    COMPREPLY=($(compgen -W "list preview apply" -- "$cur"))
                    return 0
                    ;;
                agent)
                    COMPREPLY=($(compgen -W "status start stop restart logs" -- "$cur"))
                    return 0
                    ;;
                audit)
                    COMPREPLY=($(compgen -W "--json --strict $services" -- "$cur"))
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
                        save|load|delete|diff)
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