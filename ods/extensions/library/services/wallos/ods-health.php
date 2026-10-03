<?php
header('Content-Type: text/plain');
header('Cache-Control: no-store');
try {
    $path = __DIR__ . '/db/wallos.db';
    if (!is_file($path) || !is_readable($path)) {
        throw new RuntimeException('Database unavailable');
    }
    $db = new PDO('sqlite:file:' . $path . '?mode=ro', null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_TIMEOUT => 1,
    ]);
    $tables = $db->query("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")->fetchColumn();
    if ((int) $tables === 0) {
        throw new RuntimeException('Schema unavailable');
    }
    echo "OK\n";
} catch (Throwable $error) {
    http_response_code(503);
    echo "Not ready\n";
}
