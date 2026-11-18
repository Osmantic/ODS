# Contract: `ods.ps1 chat` must send the user's message to the local LLM as
# UTF-8 JSON. Windows PowerShell 5.1 encodes a [string] -Body as ISO-8859-1,
# which turns non-Latin text (e.g. Japanese, Cyrillic, emoji) into "?".
$ErrorActionPreference = "Stop"

$rootDir = Resolve-Path (Join-Path $PSScriptRoot "../..")
$windowsCli = Join-Path $rootDir "installers/windows/ods.ps1"

$ast = [System.Management.Automation.Language.Parser]::ParseFile($windowsCli, [ref]$null, [ref]$null)
$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq "Invoke-Chat" }, $true)
if ($fn.Count -ne 1) { throw "Invoke-Chat not found in ods.ps1" }
. ([scriptblock]::Create($fn[0].Extent.Text))

$port = Get-Random -Minimum 20000 -Maximum 40000
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$port/")
$listener.Start()


function Get-NativeInferenceBackend { "llama-server" }
function Get-WindowsLocalLlmEndpoint { @{ ChatCompletionsUrl = "http://127.0.0.1:$port/v1/chat/completions" } }
function Write-AI { param($m) Write-Host $m }
function Write-AIError { param($m) Write-Host $m }

# Built from code points so the test file stays ASCII under PS 5.1.
$message = "Privet " + [string][char]0x041F + [char]0x0440 + " " + [char]0x65E5 + [char]0x672C + " caf" + [char]0x00E9
$async = $listener.GetContextAsync()
$ps = [PowerShell]::Create()
# Serve one response from a background runspace so Invoke-Chat can block.
$null = $ps.AddScript({
    param($task)
    $ctx = $task.GetAwaiter().GetResult()
    $ms = New-Object System.IO.MemoryStream
    $ctx.Request.InputStream.CopyTo($ms)
    $raw = $ms.ToArray()
    $reply = [Text.Encoding]::UTF8.GetBytes('{"choices":[{"message":{"content":"ok"}}]}')
    $ctx.Response.ContentType = "application/json; charset=utf-8"
    $ctx.Response.OutputStream.Write($reply, 0, $reply.Length)
    $ctx.Response.Close()
    , $raw
}).AddArgument($async)
$handle = $ps.BeginInvoke()

try {
    Invoke-Chat -Message $message | Out-Null
    $raw = $ps.EndInvoke($handle)[0]
} finally {
    $listener.Stop()
    $ps.Dispose()
}

$received = ([Text.Encoding]::UTF8.GetString([byte[]]$raw) | ConvertFrom-Json).messages[0].content
if ($received -ne $message) {
    throw "chat message was not sent as UTF-8: expected '$message', server received '$received'"
}
Write-Host "PASS: ods.ps1 chat sends non-ASCII messages as UTF-8"
