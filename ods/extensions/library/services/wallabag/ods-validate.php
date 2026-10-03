<?php
if (!preg_match('/\A[0-9a-fA-F]{64}\z/', getenv('SYMFONY__ENV__SECRET') ?: '')) {
    fwrite(STDERR, "WALLABAG_SECRET must contain exactly 64 hexadecimal characters.\n"); exit(1);
}
if (strlen(getenv('WALLABAG_INITIAL_PASSWORD') ?: '') < 12 ||
    !filter_var(getenv('WALLABAG_INITIAL_EMAIL'), FILTER_VALIDATE_EMAIL)) {
    fwrite(STDERR, "Provide a valid initial admin email and a password of at least 12 bytes.\n"); exit(1);
}
