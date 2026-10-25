#!/usr/bin/env bash
# Serialize one value for the single-line .env grammar shared by Bash, Docker
# Compose, and ODS's safe environment reader. Newlines cannot be represented
# portably in that grammar, so normalize them to spaces rather than joining
# words or allowing a second assignment line.
dotenv_quote() {
    local value="$1"
    value="${value//$'\r'/ }"
    value="${value//$'\n'/ }"

    # Single quotes are literal in Bash and, except before the closing quote,
    # in Compose: its parser reads \' as an escaped quote even there, so
    # 'abc\' never terminates. When the value contains a single quote or ends
    # in a backslash, use their common double-quoted escape set. Bash requires
    # a backslash before a literal backtick there, while Compose preserves that
    # backslash, so normalize backticks to the visually equivalent modifier
    # grave accent only in this rare fallback instead of corrupting one reader.
    if [[ "$value" == *"'"* || "$value" == *\\ ]]; then
        value="${value//\`/ˋ}"
        value="${value//\\/\\\\}"
        value="${value//\"/\\\"}"
        value="${value//\$/\\\$}"
        printf '"%s"\n' "$value"
    else
        printf "'%s'\n" "$value"
    fi
}

# Serialize one value for a .env that Docker Compose and lib/safe-env.sh read,
# keeping it bare whenever Compose already reads the bare text literally.
# Quotes are added only for what Compose would otherwise change: surrounding
# whitespace, a leading quote, '$' interpolation, and a whitespace-led '#'
# comment. Values that read correctly today keep their exact bytes, so simple
# grep/cut readers still see them unchanged. The quoted form is lossless for
# those readers; unlike dotenv_quote, it is not meant to be sourced by Bash.
dotenv_value() {
    local value="$1"
    value="${value//$'\r'/ }"
    value="${value//$'\n'/ }"
    if [[ "$value" != [[:space:]]* && "$value" != *[[:space:]] \
        && "$value" != [\"\']* && "$value" != *'$'* \
        && "$value" != *[[:space:]]'#'* ]]; then
        printf '%s\n' "$value"
    elif [[ "$value" != *"'"* && "$value" != *\\ ]]; then
        # Compose reads a backslash before the closing quote as an escaped
        # quote, so a value ending in one takes the double-quoted form below.
        printf "'%s'\n" "$value"
    else
        value="${value//\\/\\\\}"
        value="${value//\"/\\\"}"
        value="${value//\$/\\\$}"
        printf '"%s"\n' "$value"
    fi
}
