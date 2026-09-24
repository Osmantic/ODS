#!/bin/sh
# ============================================================================
# ODS Whisper Entrypoint
# ============================================================================
# VAD patch implementation with safe multi-line function call handling.
# Uses Python AST parsing to safely modify transcribe() calls.
# ============================================================================

apply_vad_patch() {
    local stt_file="$1"
    echo "[ods-whisper] Applying VAD patch to $stt_file"

    if [ ! -f "$stt_file" ]; then
        echo "[ods-whisper] Warning: STT file not found: $stt_file"
        return 1
    fi

    # Create Python script to safely patch the transcribe call
    cat > /tmp/vad_patcher.py << 'PYTHON_EOF'
import ast
import sys
import re

def patch_transcribe_call(source_code):
    """Patch every transcribe() call without duplicating existing VAD kwargs."""
    try:
        # Parse the AST to find function calls
        tree = ast.parse(source_code)

        calls = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and
                node.func.attr == 'transcribe'):
                present = {keyword.arg for keyword in node.keywords}
                missing = []
                if 'vad_filter' not in present:
                    missing.append('vad_filter=True')
                if 'vad_parameters' not in present:
                    missing.append('vad_parameters={"threshold": 0.5}')
                if missing:
                    calls.append((node, missing))

        if not calls:
            return source_code, False

        lines = source_code.splitlines(keepends=True)
        line_starts = []
        offset = 0
        for line in lines:
            line_starts.append(offset)
            offset += len(line)

        def absolute_offset(line_number, byte_column):
            line = lines[line_number - 1]
            char_column = len(
                line.encode('utf-8')[:byte_column].decode('utf-8')
            )
            return line_starts[line_number - 1] + char_column

        edits = []
        for node, missing in calls:
            close_offset = absolute_offset(node.end_lineno, node.end_col_offset) - 1
            close_line = lines[node.end_lineno - 1]
            close_column = close_offset - line_starts[node.end_lineno - 1]
            before_close = close_line[:close_column]
            call_items = [*node.args, *node.keywords]

            if node.lineno == node.end_lineno or before_close.strip():
                separator = ', ' if call_items else ''
                edits.append((close_offset, separator + ', '.join(missing)))
                continue

            if call_items:
                last_item = max(
                    call_items,
                    key=lambda item: (item.end_lineno, item.end_col_offset),
                )
                item_end = absolute_offset(
                    last_item.end_lineno, last_item.end_col_offset
                )
                between = source_code[item_end:close_offset]
                if not between.lstrip().startswith(','):
                    edits.append((item_end, ','))
                item_line = lines[last_item.lineno - 1]
                indentation = re.match(r'[ \t]*', item_line).group(0)
            else:
                indentation = before_close + '    '

            keyword_lines = ''.join(
                f'{indentation}{keyword},\n' for keyword in missing
            )
            edits.append((line_starts[node.end_lineno - 1], keyword_lines))

        patched = source_code
        for edit_offset, replacement in sorted(edits, reverse=True):
            patched = patched[:edit_offset] + replacement + patched[edit_offset:]
        return patched, True

    except SyntaxError as e:
        print(f"Syntax error in source file: {e}", file=sys.stderr)
        return source_code, False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python vad_patcher.py <file_path>", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    try:
        with open(file_path, 'r') as f:
            original_code = f.read()

        patched_code, was_modified = patch_transcribe_call(original_code)

        if was_modified:
            with open(file_path, 'w') as f:
                f.write(patched_code)
            print("VAD patch applied successfully")
        else:
            print("No transcribe calls found or already patched")

    except Exception as e:
        print(f"Error processing file: {e}", file=sys.stderr)
        sys.exit(1)
PYTHON_EOF

    # Apply the patch using Python
    if "$PYTHON_CMD" /tmp/vad_patcher.py "$stt_file"; then
        echo "[ods-whisper] VAD patch applied successfully"
        rm -f /tmp/vad_patcher.py
        return 0
    else
        echo "[ods-whisper] VAD patch failed, continuing with defaults"
        rm -f /tmp/vad_patcher.py
        return 1
    fi
}

PYTHON_CMD="python3"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

STT_FILE=$($PYTHON_CMD -c "import speaches.routers.stt as m; print(m.__file__)" 2>/dev/null || true)

if [ -n "$STT_FILE" ]; then
    # Apply VAD patch with safe multi-line handling
    apply_vad_patch "$STT_FILE"
else
    echo "[ods-whisper] Could not locate STT module, skipping VAD patch"
fi

# Always start uvicorn (patch failure is non-fatal but logged)
exec uvicorn --factory speaches.main:create_app --host 0.0.0.0 --port 8000
