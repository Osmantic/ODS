# Native Fish completions. Sourcing this file never invokes ODS or Docker.
function __ods_at
    set -l words (commandline -opc)
    test (count $words) -eq (math (count $argv) + 1); or return 1
    for index in (seq (count $argv))
        set -l word_index (math $index + 1)
        test "$words[$word_index]" = "$argv[$index]"; or return 1
    end
end

function __ods_presets
    set -l root "$HOME/ods"
    if set -q INSTALL_DIR; and test -n "$INSTALL_DIR"
        set root "$INSTALL_DIR"
    else if set -q ODS_HOME; and test -n "$ODS_HOME"
        set root "$ODS_HOME"
    else if set -q ODS_INSTALL_DIR; and test -n "$ODS_INSTALL_DIR"
        set root "$ODS_INSTALL_DIR"
    end
    for directory in "$root"/presets/*/
        if test -f "$directory/meta.txt"; and test -f "$directory/extensions.list"
            string escape -- (path basename "$directory")
        end
    end
end

set -l commands gpu status status-json list enable disable purge preset mode model remote-provider stt backup restore rollback logs restart repair start stop update shell config chat benchmark doctor audit template agent help version
set -l services ape embeddings llama-server n8n open-webui opencode qdrant searxng tts whisper llm webui vector search kokoro voice stt
for executable in ods ods-cli
    complete -c $executable -f
    complete -c $executable -n '__ods_at' -a "$commands"
    complete -c $executable -n '__ods_at gpu' -a 'status topology assignment validate reassign help'
    complete -c $executable -n '__ods_at preset' -a 'save load list delete export import diff'
    complete -c $executable -n '__ods_at mode' -a 'local cloud hybrid'
    complete -c $executable -n '__ods_at model' -a 'current list swap'
    complete -c $executable -n '__ods_at config' -a 'show edit validate'
    complete -c $executable -n '__ods_at template' -a 'list preview apply'
    complete -c $executable -n '__ods_at agent' -a 'status start stop restart logs'
    complete -c $executable -n '__ods_at remote-provider' -a 'status plan configure test disable remove peer-models'
    complete -c $executable -n '__ods_at remote-provider peer-models' -a 'list'
    for operation in enable disable purge logs restart start stop shell
        complete -c $executable -n "__ods_at $operation" -a "$services"
    end
    for operation in load delete export diff
        complete -c $executable -n "__ods_at preset $operation" -a '(__ods_presets)'
    end
    complete -c $executable -n '__ods_at preset import' -F
    complete -c $executable -n '__ods_at restore' -F
    complete -c $executable -n '__ods_at doctor' -l json -d 'Print a JSON diagnostic report'
    complete -c $executable -n '__ods_at gpu reassign' -l dry-run -d 'Preview GPU reassignment'
    complete -c $executable -n '__ods_at update' -l dry-run -d 'Preview update changes'
end
