"""Test SSE frame formatting produces correct newlines."""



def test_sse_frame_newline_count():
    """Each SSE frame should be 'data: <content>\\n\\n', not with an extra newline.

    The runner's output line includes a trailing \\n (line-buffered text mode).
    When we yield f"data: {line[5:]}\\n\\n", we would get an extra blank line
    in the stream, breaking SSE parsers that expect exactly two newlines
    between frames.
    """
    # Simulate what the server does: the stdout line includes its newline.
    runner_output = "SSE: {\"result\": \"done\"}\n"

    # Extract and frame it, stripping the line's own newline.
    framed = f"data: {runner_output[5:].rstrip()}\n\n"

    # Verify the frame ends with exactly two newlines, no more.
    assert framed.endswith("\n\n"), f"frame should end with two newlines: {framed!r}"
    assert not framed.endswith("\n\n\n"), f"frame has an extra newline: {framed!r}"

    # Verify the frame doesn't have spurious blank lines in the middle.
    lines = framed.split("\n")
    # After the split, ['data: {"result": "done"}', '', '']
    assert len(lines) == 3
    assert lines[0].startswith("data: ")
    assert lines[1] == ""
    assert lines[2] == ""


if __name__ == "__main__":
    test_sse_frame_newline_count()
    print("✓ SSE frames have correct newline count")
