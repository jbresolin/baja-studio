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
    5. the tray: its status answer (no token, this laptop only), port.txt, the
       camera icon, its Run entry, and one poll (amber: no camera yet);
    6. install again over it (an upgrade): still no failed step, same data folder,
       the service answers again;
    7. uninstall silently; service, firewall rule and the tray's Run entry gone, data kept.

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
$runKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
$trayRunValue = 'BajaCameraNodeTray'

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
# All of the output first, then its first line: `| Select-Object -First 1` straight on
# the command stops ffmpeg mid-output, and then its exit code is not 0.
$versionLines = & $ffmpeg -hide_banner -version
if ($LASTEXITCODE -ne 0) { throw "$ffmpeg did not run (exit code $LASTEXITCODE)" }
$banner = $versionLines | Select-Object -First 1
if ($banner -notmatch '^ffmpeg version') { throw "$ffmpeg says '$banner'" }
Write-Host "ok: $banner"
# The live crop needs zmq; DirectShow capture and SRT out are the rest of the pipeline.
$filters = & $ffmpeg -hide_banner -filters 2>$null | Out-String
if ($filters -notmatch '\szmq\s') { throw "$ffmpeg has no zmq filter (the live crop needs it)" }
$protocols = & $ffmpeg -hide_banner -protocols 2>$null | Out-String
if ($protocols -notmatch '\ssrt\s') { throw "$ffmpeg has no SRT" }
Write-Host 'ok: the zmq filter and SRT are there'
if (-not (Test-Path (Join-Path $programDirectory 'ffmpeg\LICENSE.txt'))) { throw 'ffmpeg LICENSE.txt is missing' }
$rule = Get-NetFirewallRule -DisplayName $firewallRule -ErrorAction SilentlyContinue
if (-not $rule) { throw "the firewall rule '$firewallRule' is missing" }
$ports = ($rule | Get-NetFirewallPortFilter).LocalPort
if ($ports -ne '8891') { throw "the firewall rule opens $ports" }
Write-Host 'ok: firewall rule for TCP 8891'

Write-Host '--- the tray: its status answer, its Run entry, one poll'
# The answer meant for the tray: no token, this laptop only. A fresh node has no camera.
$trayStatus = Invoke-RestMethod "$nodeUrl/api/node/tray-status" -TimeoutSec 5
if ($trayStatus.version -ne $Version) { throw "the tray status reports version $($trayStatus.version)" }
if (@($trayStatus.cameras).Count -ne 0) { throw 'the tray status lists cameras on a fresh node' }
# The node writes its port for the tray at each start.
$portFile = (Get-Content (Join-Path $dataDirectory 'port.txt') -Raw).Trim()
if ($portFile -ne '8891') { throw "port.txt says '$portFile'" }
if (-not (Test-Path (Join-Path $programDirectory 'camera-node.ico'))) { throw 'camera-node.ico is missing' }
$runEntry = (Get-ItemProperty $runKey -Name $trayRunValue -ErrorAction SilentlyContinue).$trayRunValue
if ($runEntry -notlike '*pythonw.exe" -m baja_studio_tray --node') { throw "the tray's Run entry is '$runEntry'" }
Write-Host "ok: Run entry $runEntry"
# python.exe rather than pythonw.exe, so its report reaches the log.
$trayOutput = Join-Path $output 'tray.txt'
$trayProcess = Start-Process (Join-Path $programDirectory 'python\python.exe') -PassThru -NoNewWindow `
    -ArgumentList '-m', 'baja_studio_tray', '--node', '--exit-after-first-poll' `
    -RedirectStandardOutput $trayOutput -RedirectStandardError (Join-Path $output 'tray-stderr.txt')
if (-not $trayProcess.WaitForExit(60000)) { $trayProcess.Kill(); throw 'the tray did not exit after its first poll' }
$report = Get-Content $trayOutput -Raw
# A fresh node with no camera is amber ("no cameras set up"), and reports this version.
if ($trayProcess.ExitCode -ne 0 -or $report -notmatch "badge=amber version=$([regex]::Escape($Version))") {
    throw "the tray exited with $($trayProcess.ExitCode): $report $(Get-Content (Join-Path $output 'tray-stderr.txt') -Raw)"
}
Write-Host "ok: tray $($report.Trim() -replace "`r?`n", '; ')"

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
if (Get-ItemProperty $runKey -Name $trayRunValue -ErrorAction SilentlyContinue) { throw "the tray's Run entry is still there" }
if (-not (Test-Path (Join-Path $dataDirectory 'baja-studio.sqlite3'))) { throw 'the data was not kept' }
Write-Host 'ok: uninstalled, data kept'
