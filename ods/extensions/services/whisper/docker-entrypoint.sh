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
    """Safely patch transcribe() calls to add VAD parameters."""
    # Newer speaches commits (post mid-2025) include vad_filter= directly in
    # the upstream transcribe() call. Skip patching anywhere in the file
    # already containing those kwargs — the per-call check below only sees
    # the first line of a multi-line call and misses kwargs on continuation
    # lines, which produced "SyntaxError: keyword argument repeated".
    if 'vad_filter=' in source_code or 'vad_parameters=' in source_code:
        return source_code, False
    try:
        # Parse the AST to find function calls
        tree = ast.parse(source_code)

        # Track line numbers of transcribe calls
        transcribe_lines = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and
                node.func.attr == 'transcribe'):
                transcribe_lines.append(node.lineno)

        if not transcribe_lines:
            return source_code, False

        lines = source_code.splitlines()
        modified = False

        for line_num in transcribe_lines:
            # Convert to 0-based index
            idx = line_num - 1
            if idx >= len(lines):
                continue

            line = lines[idx]

            # Check if VAD parameters already exist
            if 'vad_filter=' in line or 'vad_parameters=' in line:
                continue

            # Find the transcribe call and add VAD parameters
            # Handle both single-line and multi-line calls
            if 'transcribe(' in line:
                # Simple single-line case
                if line.strip().endswith(')'):
                    # Insert VAD parameters before the LAST closing paren only
                    # Use rfind to replace only the rightmost ')' to avoid breaking nested calls
                    last_paren_idx = line.rfind(')')
                    if last_paren_idx != -1:
                        new_line = line[:last_paren_idx] + ', vad_filter=True, vad_parameters={"threshold": 0.5}' + line[last_paren_idx:]
                        lines[idx] = new_line
                        modified = True
                else:
                    # Multi-line call - find the closing parenthesis
                    paren_count = line.count('(') - line.count(')')
                    search_idx = idx + 1

                    while search_idx < len(lines) and paren_count > 0:
                        search_line = lines[search_idx]
                        paren_count += search_line.count('(') - search_line.count(')')

                        if paren_count == 0:
                            # Found the closing line
                            if search_line.strip() == ')':
                                # Closing paren on its own line
                                lines.insert(search_idx, '    vad_filter=True,')
                                lines.insert(search_idx + 1, '    vad_parameters={"threshold": 0.5},')
                            else:
                                # Closing paren with other content - replace only the last ')'
                                last_paren_idx = search_line.rfind(')')
                                if last_paren_idx != -1:
                                    lines[search_idx] = search_line[:last_paren_idx] + ', vad_filter=True, vad_parameters={"threshold": 0.5}' + search_line[last_paren_idx:]
                            modified = True
                            break
                        search_idx += 1

        return '\n'.join(lines), modified

    except SyntaxError as e:
        print(f"Syntax error in source file: {e}", file=sys.stderr)
        return source_code, False
    except Exception as e:
        print(f"Error patching transcribe call: {e}", file=sys.stderr)
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
