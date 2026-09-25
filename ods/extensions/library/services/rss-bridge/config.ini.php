; <?php exit; ?> DO NOT REMOVE THIS LINE
[system]
env = "prod"
enabled_bridges[] = CssSelectorBridge
enabled_bridges[] = XPathBridge
enabled_bridges[] = FeedMerge
enabled_bridges[] = FeedReducerBridge
enabled_bridges[] = Filter
timezone = "UTC"
[http]
timeout = 10
retries = 1
max_filesize = 10
[cache]
type = "file"
custom_timeout = false
[FileCache]
path = "/app/cache"
enable_purge = true
[error]
output = "http"
