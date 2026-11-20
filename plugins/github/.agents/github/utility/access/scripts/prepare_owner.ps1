param(
    [Parameter(Mandatory)][string]$Repository,
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9-]{0,62}$')][string]$Name,
    [Parameter(Mandatory)][ValidateSet('onboard', 'rotate', 'publish')][string]$Operation,
    [Parameter(Mandatory)][string]$AttemptDirectory
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Prepare this desktop shortcut on Windows.'
}
$checkout = (Resolve-Path -LiteralPath $Repository).ProviderPath
$broker = (Get-Command tj-credentials.exe -CommandType Application -ErrorAction Stop).Source
$attempt = [IO.Path]::GetFullPath($AttemptDirectory)
# A prepared handoff has one fixed owner action and one new receipt.
$null = New-Item -ItemType Directory -Path $attempt -ErrorAction Stop
$open = [IO.Directory]::CreateDirectory((Join-Path $attempt 'Open')).FullName
$result = Join-Path $attempt 'result.json'
$shortcutPath = Join-Path $open "$Operation $Name.lnk"
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $broker
$shortcut.WorkingDirectory = $checkout
$shortcut.Arguments = "$Operation --name $Name --result-file `"$result`""
$shortcut.Description = "$Operation $Name using the repository's declared credential broker"
$shortcut.Save()

# The native ! command opens a folder containing just the shortcut. The owner
# opens the shortcut from Explorer, with their own desktop environment.
$commandFile = Join-Path $attempt 'owner-command.txt'
$escaped = $open.Replace("'", "''")
[IO.File]::WriteAllText($commandFile, "! explorer.exe '$escaped'", [Text.UTF8Encoding]::new($false))
[ordered]@{
    credential = $Name
    operation = $Operation
    checkout = $checkout
    shortcut = $shortcutPath
    command_file = $commandFile
    result_file = $result
} | ConvertTo-Json -Compress
