<?php
$length = strlen(getenv('KANBOARD_INITIAL_PASSWORD') ?: '');
if ($length < 12 || $length > 72 || strpos(getenv('KANBOARD_INITIAL_PASSWORD'), "\0") !== false) {
    fwrite(STDERR, "KANBOARD_INITIAL_PASSWORD must contain 12–72 UTF-8 bytes and no null bytes.\n");
    exit(1);
}
