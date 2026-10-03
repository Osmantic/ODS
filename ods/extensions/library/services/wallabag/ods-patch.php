<?php
$path = '/var/www/wallabag/src/Wallabag/CoreBundle/Command/InstallCommand.php';
$text = file_get_contents($path);
$replacements = [
    "\$this->io->ask('Username', 'wallabag')" => "'ods'",
    "new Question('Password', 'wallabag')" => "new Question('Password', getenv('WALLABAG_INITIAL_PASSWORD'))",
    "\$this->io->ask('Email', 'wallabag@wallabag.io')" => "getenv('WALLABAG_INITIAL_EMAIL')",
];
foreach ($replacements as $old => $new) {
    if (substr_count($text, $old) !== 1) {
        throw new RuntimeException('Unsupported Wallabag installer source; refusing default credentials.');
    }
    $text = str_replace($old, $new, $text);
}
file_put_contents($path, $text);

// Keep an all-digit hexadecimal secret a YAML string, not a numeric scalar.
$template = '/etc/wallabag/parameters.template.yml';
$config = file_get_contents($template);
$needle = 'secret: ${SYMFONY__ENV__SECRET:-ovmpmAWXRCabNlMgzlzFXDYmCFfzGv}';
if (substr_count($config, $needle) !== 1) {
    throw new RuntimeException('Unsupported Wallabag secret template.');
}
file_put_contents($template, str_replace($needle, "secret: '\${SYMFONY__ENV__SECRET}'", $config));
