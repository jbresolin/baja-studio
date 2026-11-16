<#
.SYNOPSIS
    Installs the Baja camera node silently on this machine, checks it runs, and uninstalls it.

.DESCRIPTION
    The acceptance test of the camera node's installer (WP15, ADR 0023), run by
    the release workflow on a fresh Windows runner, like smoke-test.ps1 for
    the studio:

    1. install silently; the setup log records no failed step;
    2. the BajaCameraNode service runs and /agent/info answers on port 8891
       with this version, contract 1.1 and no cameras yet (a fresh node);
    3. its page is served at /, and its settings API answers (no token yet);
    4. its ffmpeg is bundled and runs; the firewall rule for TCP 8891 is there;
    5. install again over it (an upgrade): still no failed step, same data folder,
       the service answers again;
    6. uninstall silently; service and firewall rule gone, data kept.

    Leaves install.log, upgrade.log, uninstall.log in -OutputDirectory and,
    whatever happens, a copy of the data folder's logs with NSSM's view of the
    service (in data\), for when something fails.
#>
param(
    [Parameter(Mandatory)] [string] $Installer,
    [Parameter(Mandatory)] [string] $Version,
    [string] $OutputDirectory = '.'
)

$ErrorActionPreference = 'Stop'
$output = (New-Item -ItemType Directory -Force $OutputDirectory).FullName
$programDirectory = Join-Path $env:ProgramFiles 'BajaCameraNode'
$dataDirectory = Join-Path $env:ProgramData 'BajaCameraNode'
$service = 'BajaCameraNode'
$firewallRule = 'Baja camera node (LAN)'
$nodeUrl = 'http://localhost:8891'

function Wait-Until([scriptblock] $Condition, [string] $What, [int] $Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { if (& $Condition) { Write-Host "ok: $What"; return } } catch { $lastError = $_ }
        Start-Sleep -Seconds 2
    }
    throw "timed out waiting for: $What. Last error: $lastError"
}

function Save-ServiceEvidence {
    <# Copy what the service wrote, before the uninstaller (or the runner) removes it. #>
    $evidence = New-Item -ItemType Directory -Force (Join-Path $output 'data')
    $logs = Join-Path $dataDirectory 'logs'
    if (Test-Path $logs) { Copy-Item $logs $evidence -Recurse -Force }
    $nssm = Join-Path $programDirectory 'nssm\nssm.exe'
    if (Test-Path $nssm) {
        "$service status: $(& $nssm status $service 2>&1)" | Add-Content (Join-Path $evidence 'services.txt')
        "$service params: $(& $nssm get $service AppParameters 2>&1)" | Add-Content (Join-Path $evidence 'services.txt')
    }
}

function Install-Node([string] $LogName) {
    $log = Join-Path $output $LogName
    $install = Start-Process $Installer -Wait -PassThru -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$log")
    if ($install.ExitCode -ne 0) { throw "the installer exited with $($install.ExitCode)" }
    # A silent install shows no message box: the failed steps are only in the log.
    $failed = Select-String -Path $log -Pattern 'Failed steps:' -Context 0, 5
    if ($failed) { throw "the installer reports failed steps:`n$($failed | Out-String)" }
    Write-Host "ok: no failed step in $LogName"
}

function Assert-NodeAnswers {
    Wait-Until {
        $script:info = Invoke-RestMethod "$nodeUrl/agent/info" -TimeoutSec 5
        $script:info.software_version -eq "baja-camera-node $Version"
    } "/agent/info answers with version $Version"
    if ($info.contract_version -ne '1.1') { throw "the node speaks contract $($info.contract_version)" }
    if (@($info.cameras).Count -ne 0) { throw 'a fresh node already serves cameras' }
    $status = (Get-Service $service).Status
    if ($status -ne 'Running') { throw "$service is $status" }
    Write-Host "ok: $service running"
}

trap { Save-ServiceEvidence; break }

Write-Host "--- install $Installer"
Install-Node 'install.log'
Assert-NodeAnswers

Write-Host '--- the page and the settings API'
$page = Invoke-WebRequest "$nodeUrl/" -UseBasicParsing -TimeoutSec 5
if ($page.Content -notmatch 'Baja camera node') { throw "the node's page is not served at /" }
$settings = Invoke-RestMethod "$nodeUrl/api/settings/node" -TimeoutSec 5
if ($settings.values.port -ne 8891) { throw "the node's port is $($settings.values.port)" }
Write-Host 'ok: page and settings'

Write-Host "--- the node's ffmpeg and the firewall rule"
$ffmpeg = Join-Path $programDirectory 'ffmpeg\ffmpeg.exe'
$banner = & $ffmpeg -hide_banner -version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { throw "$ffmpeg did not run" }
Write-Host "ok: $banner"
if (-not (Test-Path (Join-Path $programDirectory 'ffmpeg\LICENSE.txt'))) { throw 'ffmpeg LICENSE.txt is missing' }
$rule = Get-NetFirewallRule -DisplayName $firewallRule -ErrorAction SilentlyContinue
if (-not $rule) { throw "the firewall rule '$firewallRule' is missing" }
$ports = ($rule | Get-NetFirewallPortFilter).LocalPort
if ($ports -ne '8891') { throw "the firewall rule opens $ports" }
Write-Host 'ok: firewall rule for TCP 8891'

Write-Host '--- upgrade: install again over it'
Install-Node 'upgrade.log'
$registered = (Get-ItemProperty 'HKLM:\Software\BajaCameraNode').DataDirectory
if ($registered -ne $dataDirectory) { throw "the data folder moved to $registered" }
Assert-NodeAnswers

Save-ServiceEvidence
Write-Host '--- uninstall (keeping the data)'
$uninstaller = Join-Path $programDirectory 'unins000.exe'
Start-Process $uninstaller -Wait -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $output 'uninstall.log')")
# The uninstaller re-launches itself from %TEMP% and returns at once: wait for its work.
Wait-Until { -not (Test-Path $uninstaller) } 'the program folder is removed'
Wait-Until { -not (Get-Service $service -ErrorAction SilentlyContinue) } 'the service is removed'
if (Get-NetFirewallRule -DisplayName $firewallRule -ErrorAction SilentlyContinue) {
    throw "the firewall rule '$firewallRule' is still there"
}
if (-not (Test-Path (Join-Path $dataDirectory 'baja-studio.sqlite3'))) { throw 'the data was not kept' }
Write-Host 'ok: uninstalled, data kept'
