# ============================================================================
# Detect legacy backticks (`cmd`) inside the body of UNQUOTED here-documents.
# ============================================================================
# Issue #509. Quoted here-documents (<<'EOF' / <<"EOF") suppress shell
# expansion, so backticks there are literal characters and harmless. Unquoted
# here-documents (<<EOF / <<-EOF) DO expand backticks as command substitutions
# — and unlike normal `$(...)`, those expansions can't be nested cleanly and
# are easy to misread as plain text. Forbid the pattern outright in installer
# scripts where the value of the heredoc body is what gets shipped to users
# (systemd unit files, generated config snippets, README fragments, …).
#
# Run via: awk -f scripts/check-heredoc-backticks.awk <files>
# Exit codes:
#   0 — no offending heredocs found
#   1 — at least one offending line; details on stderr
#
# This is awk (POSIX) on purpose: the pre-commit hook runs on every developer
# workstation including macOS BSD awk, so we avoid GNU-only features.
# ============================================================================

BEGIN { in_heredoc = 0; found = 0 }

{
    if (in_heredoc) {
        # End of heredoc: line whose only content is the marker (optionally
        # indented when the opener used `<<-`). Body lines may themselves
        # equal the marker if they are indented differently — the closing
        # rule matches the *trimmed* line.
        if ($0 ~ ("^[ \t]*" heredoc_marker "[ \t]*$")) {
            in_heredoc = 0
            next
        }
        if (index($0, "`") > 0) {
            print FILENAME ":" FNR ": backtick in unquoted heredoc body: " $0 > "/dev/stderr"
            found = 1
        }
        next
    }

    # Detect an UNQUOTED heredoc opener of the form `<<MARKER` or `<<-MARKER`
    # where MARKER starts with [A-Za-z_]. This regex deliberately rejects
    # `<<'EOF'` and `<<"EOF"` because the quote character appears between
    # `<<-?` and the alphanumeric marker, breaking the match.
    if (match($0, /<<-?[A-Za-z_][A-Za-z0-9_]*/)) {
        marker = substr($0, RSTART, RLENGTH)
        sub(/^<<-?/, "", marker)
        in_heredoc = 1
        heredoc_marker = marker
    }
}

END { exit found ? 1 : 0 }
