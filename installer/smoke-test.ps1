<#
.SYNOPSIS
    Installs Baja Studio silently on this machine, checks it runs, and uninstalls it.

.DESCRIPTION
    The acceptance test of the installer (LIVESTACK-11), run by the release
    workflow on a fresh Windows runner:

    1. install silently;
    2. wait for /api/health to answer with this version, home and gateway ok
       (playout and audio stay "offline: not configured" on a runner);
    3. both services running; the gateway (Caddy, TLS off) serves the studio on port 80;
    4. build a diagnostics bundle and check it holds no secret and the Caddy version;
    5. uninstall silently; services and firewall rule gone, data kept.

    Leaves install.log, uninstall.log and the bundle in -OutputDirectory, and,
    whatever happens, a copy of the data folder's logs and Caddyfile with
    NSSM's view of both services (in data\), for when something fails.
#>
param(
    [Parameter(Mandatory)] [string] $Installer,
    [Parameter(Mandatory)] [string] $Version,
    [string] $OutputDirectory = '.'
)

$ErrorActionPreference = 'Stop'
$output = (New-Item -ItemType Directory -Force $OutputDirectory).FullName
$programDirectory = Join-Path $env:ProgramFiles 'BajaStudio'
$dataDirectory = Join-Path $env:ProgramData 'BajaStudio'

function Wait-Until([scriptblock] $Condition, [string] $What, [int] $Seconds = 120) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { if (& $Condition) { Write-Host "ok: $What"; return } } catch { $lastError = $_ }
        Start-Sleep -Seconds 2
    }
    $lastHealth = if ($script:health) { $script:health | ConvertTo-Json -Depth 5 -Compress } else { 'none' }
    throw "timed out waiting for: $What. Last error: $lastError. Last health: $lastHealth"
}

function Get-ModuleHealth($Health, [string] $Name) {
    ($Health.modules | Where-Object name -eq $Name).state
}

function Save-ServiceEvidence {
    <# Copy what the services wrote, before the uninstaller (or the runner) removes it. #>
    $evidence = New-Item -ItemType Directory -Force (Join-Path $output 'data')
    foreach ($item in 'logs', 'caddy\Caddyfile') {
        $source = Join-Path $dataDirectory $item
        if (Test-Path $source) { Copy-Item $source $evidence -Recurse -Force }
    }
    $nssm = Join-Path $programDirectory 'nssm\nssm.exe'
    if (Test-Path $nssm) {
        foreach ($service in 'BajaStudio', 'BajaStudioGateway') {
            "$service status: $(& $nssm status $service 2>&1)" | Add-Content (Join-Path $evidence 'services.txt')
            "$service params: $(& $nssm get $service AppParameters 2>&1)" | Add-Content (Join-Path $evidence 'services.txt')
        }
    }
}

trap { Save-ServiceEvidence; break }

Write-Host '--- port 80 must be free for Caddy'
# GitHub's Windows images run IIS (W3SVC), which holds port 80 through HTTP.sys.
foreach ($web in 'W3SVC', 'WAS') {
    $service = Get-Service $web -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running') {
        Write-Host "stopping $web, which holds port 80"
        Stop-Service $web -Force
    }
}
$listeners = Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    $owners = $listeners | ForEach-Object { (Get-Process -Id $_.OwningProcess).ProcessName } | Sort-Object -Unique
    netsh http show servicestate view=requestq | Out-Host
    throw "port 80 is still taken by: $($owners -join ', ')"
}
netsh int ipv4 show excludedportrange protocol=tcp | Out-Host

Write-Host "--- install $Installer"
$install = Start-Process $Installer -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $output 'install.log')")
if ($install.ExitCode -ne 0) { throw "the installer exited with $($install.ExitCode)" }

Write-Host '--- the service answers, with this version'
Wait-Until {
    $script:health = Invoke-RestMethod 'http://localhost:8888/api/health' -TimeoutSec 5
    $script:health.version -eq $Version -and
    (Get-ModuleHealth $script:health 'home') -eq 'ok' -and
    (Get-ModuleHealth $script:health 'gateway') -eq 'ok'
} "health answers $Version with home and gateway ok"
$health | ConvertTo-Json -Depth 5 | Write-Host

foreach ($service in 'BajaStudio', 'BajaStudioGateway') {
    $status = (Get-Service $service).Status
    if ($status -ne 'Running') { throw "$service is $status" }
    Write-Host "ok: $service running"
}

Write-Host '--- the gateway serves the studio on port 80 (TLS off)'
$throughCaddy = Invoke-RestMethod 'http://localhost/api/health' -TimeoutSec 5
if ($throughCaddy.version -ne $Version) { throw "port 80 answered version $($throughCaddy.version)" }
Write-Host 'ok: port 80 reaches the studio'

Write-Host '--- diagnostics bundle'
$body = @{ module = 'home'; command = 'diagnostics_bundle'; caller = 'smoke-test' } | ConvertTo-Json
$built = Invoke-RestMethod 'http://localhost:8888/api/commands' -Method Post -Body $body -ContentType 'application/json'
$bundle = Join-Path $output $built.result.file_name
Invoke-WebRequest "http://localhost:8888$($built.result.url)" -OutFile $bundle
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($bundle)
try {
    $names = $zip.Entries.FullName
    foreach ($expected in 'settings.json', 'health.json', 'state.json', 'versions.json', 'services.json', 'Caddyfile') {
        if ($names -notcontains $expected) { throw "the bundle lacks $expected" }
    }
    if ($names | Where-Object { $_ -like '*.sqlite3' }) { throw 'the bundle contains the database' }
    $reader = [System.IO.StreamReader]::new($zip.GetEntry('versions.json').Open())
    $versions = $reader.ReadToEnd() | ConvertFrom-Json
    $reader.Dispose()
    if (-not $versions.caddy) { throw 'versions.json has no Caddy version' }
    Write-Host "ok: bundle $($built.result.file_name) (Caddy $($versions.caddy))"
}
finally { $zip.Dispose() }

Save-ServiceEvidence
Write-Host '--- uninstall (keeping the data)'
$uninstaller = Join-Path $programDirectory 'unins000.exe'
Start-Process $uninstaller -Wait -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $output 'uninstall.log')")
# The uninstaller re-launches itself from %TEMP% and returns at once: wait for its work.
Wait-Until { -not (Test-Path $uninstaller) } 'the program folder is removed'
Wait-Until { -not (Get-Service 'BajaStudio', 'BajaStudioGateway' -ErrorAction SilentlyContinue) } 'both services are removed'
$rule = Get-NetFirewallRule -DisplayName 'Baja Studio (LAN)' -ErrorAction SilentlyContinue
if ($rule) { throw 'the firewall rule is still there' }
if (-not (Test-Path (Join-Path $dataDirectory 'baja-studio.sqlite3'))) { throw 'the data was not kept' }
Write-Host 'ok: uninstalled, data kept'
