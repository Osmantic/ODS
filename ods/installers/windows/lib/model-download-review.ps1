function Confirm-ODSModelDownloadReview {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string]$Url,
        [AllowEmptyString()][string]$Sha256,
        [string]$ReceiptPath = "",
        [switch]$Unattended
    )
    $helper = Join-Path $Root "scripts\review-model-download.py"
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Model download blocked: review helper is missing." }
    $python = Get-ODSPythonDownloadCommand
    if (-not $python) { throw "Model download blocked: Python 3.8 or newer is required for terms review." }
    $reviewArgs = @($python.PrefixArgs) + @($helper, "--catalog", (Join-Path $Root "config\model-library.json"),
        "--file", $File, "--url", $Url, "--sha256=$Sha256")
    if ($env:ODS_MODEL_TERMS_ACK_FILE) { $reviewArgs += @("--ack-file", $env:ODS_MODEL_TERMS_ACK_FILE) }
    if ($ReceiptPath) { $reviewArgs += @("--write-ack-file", $ReceiptPath) }
    if ($Unattended) { $reviewArgs += "--non-interactive" }
    # Keep stdin/stdout attached to the terminal; this is an operator prompt.
    & $python.FilePath @reviewArgs
    if ($LASTEXITCODE -ne 0) { throw "Model download blocked: explicit review of this artifact and its current terms is required." }
}
