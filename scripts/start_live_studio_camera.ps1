param(
    [string]$VastHost = '70.48.172.113',
    [int]$SshPort = 28026,
    [string]$IdentityFile = "$HOME\.ssh\id_ed25519_vastai"
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment missing. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r scripts\live_studio_requirements.txt"
}
if (-not (Test-Path -LiteralPath $IdentityFile)) { throw "SSH key missing: $IdentityFile" }
if (Get-NetTCPConnection -LocalPort 1935 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Local port 1935 is already in use.'
}

$sshArgs = @('-i', $IdentityFile, '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
    '-N', '-L', '127.0.0.1:1935:127.0.0.1:1935', '-p', "$SshPort", "root@$VastHost")
$tunnel = Start-Process ssh -ArgumentList $sshArgs -PassThru -WindowStyle Hidden
$app = $null
try {
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if ($tunnel.HasExited) { throw 'SSH tunnel failed to connect to Vast.' }
        if (Get-NetTCPConnection -LocalPort 1935 -State Listen -ErrorAction SilentlyContinue) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw 'SSH tunnel did not open RTMP port 1935.' }
    $app = Start-Process -FilePath $python -ArgumentList (Join-Path $PSScriptRoot 'live_studio_camera.py') -NoNewWindow -PassThru
    while (-not $app.HasExited) {
        if ($tunnel.HasExited) {
            Write-Host 'SSH tunnel disconnected; reconnecting.'
            $tunnel = Start-Process ssh -ArgumentList $sshArgs -PassThru -WindowStyle Hidden
        }
        Start-Sleep -Seconds 2
    }
    if ($app.ExitCode -ne 0) { throw "Camera app exited with code $($app.ExitCode)" }
}
finally {
    if ($app -and -not $app.HasExited) { Stop-Process -Id $app.Id }
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id }
}
