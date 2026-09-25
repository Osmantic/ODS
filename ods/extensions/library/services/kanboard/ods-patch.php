<?php
$path = '/var/www/app/app/Schema/Sqlite.php';
$text = file_get_contents($path);
$needle = "\\password_hash('admin', PASSWORD_BCRYPT)";
if (substr_count($text, $needle) !== 1) {
    throw new RuntimeException('Unsupported Kanboard SQLite initializer; refusing default credentials.');
}
file_put_contents($path, str_replace($needle, "\\password_hash(getenv('KANBOARD_INITIAL_PASSWORD'), PASSWORD_BCRYPT)", $text));
