<#
.SYNOPSIS
    Assembles dist\app, the folder the installer installs to %ProgramFiles%\BajaStudio.

.DESCRIPTION
    dist\app\python     python.org embeddable Python, with the service and its
                        dependencies installed from a wheelhouse (no system Python)
    dist\app\frontend   the built web pages
    dist\app\caddy      Caddy with the Cloudflare DNS module, built with xcaddy
    dist\app\nssm       NSSM, which runs both as Windows services
    dist\app\VERSION, dist\app\wheelhouse-manifest.json

    Every download and the Caddy build are checked against the pins in
    installer\python, installer\nssm and installer\caddy. The version is the one
    already stamped in server\baja_studio\__init__.py
    (python -m baja_studio.release_tools stamp-version ...).

    Needs Windows, Python 3.12 (64-bit) and, unless -SkipCaddy, Go at the pinned
    version. Run with -WhatIf for a dry run that lists the steps.

.EXAMPLE
    pwsh installer\build.ps1
    pwsh installer\build.ps1 -SkipCaddy        # dist\ only, to check the wheelhouse
    pwsh installer\build.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    # Where dist\ goes; the default is dist\ at the repository root.
    [string] $OutputDirectory = (Join-Path $PSScriptRoot '..\dist'),
    # Leave Caddy out (no Go needed); the result cannot become an installer.
    [switch] $SkipCaddy,
    # Use frontend\dist as it is instead of running npm ci and npm run build.
    [switch] $SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$server = Join-Path $repository 'server'
$frontend = Join-Path $repository 'frontend'
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
$app = Join-Path $output 'app'
$cache = Join-Path $output 'downloads'
$wheelhouse = Join-Path $output 'wheelhouse'

function Invoke-Native {
    <# Run a program; stop the build if it fails (PowerShell would carry on). #>
    param([string] $Program, [string[]] $Arguments, [string] $WorkingDirectory = $repository)
    Push-Location $WorkingDirectory
    try {
        & $Program @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Program $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
}

function Read-Pin([string] $Name) {
    Get-Content (Join-Path $PSScriptRoot "$Name\pin.json") -Raw | ConvertFrom-Json
}

function Assert-Hash([string] $Path, [string] $Expected) {
    $actual = (Get-FileHash $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 of $Path is $actual, but the pin says $Expected"
    }
}

function Get-PinnedDownload($Pin, [string] $FileName) {
    <# Download once into dist\downloads (retrying: these hosts are not always up), then verify. #>
    $target = Join-Path $cache $FileName
    if (-not (Test-Path $target)) {
        New-Item -ItemType Directory -Force $cache | Out-Null
        Invoke-WebRequest $Pin.url -OutFile $target -MaximumRetryCount 5 -RetryIntervalSec 10
    }
    Assert-Hash $target $Pin.sha256
    $target
}

$version = (Invoke-Native python @('-m', 'baja_studio.release_tools', 'version') $server | Select-Object -Last 1).Trim()
Write-Host "Building Baja Studio $version into $app"

if ($PSCmdlet.ShouldProcess($app, 'Start from an empty app folder')) {
    if (Test-Path $app) { Remove-Item $app -Recurse -Force }
    New-Item -ItemType Directory -Force $app, $wheelhouse | Out-Null
    Get-ChildItem $wheelhouse -Filter *.whl | Remove-Item -Force
}

# --- Python: the embeddable distribution, with site-packages switched on ---------------
$pythonPin = Read-Pin 'python'
$pythonDirectory = Join-Path $app 'python'
if ($PSCmdlet.ShouldProcess($pythonDirectory, "Unpack embeddable Python $($pythonPin.version)")) {
    $zip = Get-PinnedDownload $pythonPin "python-$($pythonPin.version)-embed-amd64.zip"
    Expand-Archive $zip -DestinationPath $pythonDirectory
    # The embeddable Python ignores site-packages unless its ._pth file says otherwise.
    Set-Content (Join-Path $pythonDirectory $pythonPin.pth_file) -Encoding ascii -Value @(
        'python312.zip', '.', 'Lib\site-packages', 'import site'
    )
}

# --- Wheelhouse: the service's wheel and its pinned dependencies, for win_amd64 -------
if ($PSCmdlet.ShouldProcess($wheelhouse, 'Build the wheelhouse')) {
    $hostVersion = (Invoke-Native python @('-c', 'import sys; print(sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32)'))
    if ($hostVersion.Trim() -ne 'True') { throw 'build.ps1 needs a 64-bit Python 3.12 on PATH' }
    Invoke-Native python @('-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', $wheelhouse, $server)
    $requirements = Invoke-Native python @('-m', 'baja_studio.release_tools', 'requirements') $server
    Invoke-Native python (@('-m', 'pip', 'download', '--dest', $wheelhouse, '--only-binary=:all:',
            '--platform', 'win_amd64', '--python-version', '3.12', '--implementation', 'cp') + $requirements)
    Invoke-Native python @('-m', 'baja_studio.release_tools', 'wheelhouse-manifest',
        '--wheelhouse', $wheelhouse, '--output', (Join-Path $app 'wheelhouse-manifest.json')) $server
    # Offline install from the wheelhouse only: what was downloaded and listed is what ships.
    Invoke-Native python @('-m', 'pip', 'install', '--no-index', '--find-links', $wheelhouse,
        '--target', (Join-Path $pythonDirectory 'Lib\site-packages'), "baja_studio==$version")
    $installed = Invoke-Native (Join-Path $pythonDirectory 'python.exe') @('-c',
        'import baja_studio, fastapi, uvicorn, serial, rtmidi; print(baja_studio.__version__)')
    if ($installed.Trim() -ne $version) { throw "the embedded Python reports $installed, not $version" }
}

# --- Frontend -------------------------------------------------------------------------
if ($PSCmdlet.ShouldProcess((Join-Path $app 'frontend'), 'Build and copy the web pages')) {
    if (-not $SkipFrontendBuild) {
        Invoke-Native npm @('ci') $frontend
        Invoke-Native npm @('run', 'build') $frontend
    }
    if (-not (Test-Path (Join-Path $frontend 'dist\index.html'))) { throw 'frontend\dist is not built' }
    Copy-Item (Join-Path $frontend 'dist') (Join-Path $app 'frontend') -Recurse
}

# --- NSSM -----------------------------------------------------------------------------
$nssmPin = Read-Pin 'nssm'
if ($PSCmdlet.ShouldProcess((Join-Path $app 'nssm'), "Unpack NSSM $($nssmPin.version)")) {
    $zip = Get-PinnedDownload $nssmPin "nssm-$($nssmPin.version).zip"
    $unpacked = Join-Path $cache 'nssm'
    if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
    Expand-Archive $zip -DestinationPath $unpacked
    New-Item -ItemType Directory -Force (Join-Path $app 'nssm') | Out-Null
    Copy-Item (Join-Path $unpacked $nssmPin.executable_in_zip) (Join-Path $app 'nssm\nssm.exe')
}

# --- Caddy: built with xcaddy from pinned versions, then checked ------------------------
$caddyPin = Read-Pin 'caddy'
$caddy = Join-Path $app 'caddy\caddy.exe'
if ($SkipCaddy) {
    Write-Warning 'Caddy left out (-SkipCaddy): this dist\ cannot become an installer.'
}
elseif ($PSCmdlet.ShouldProcess($caddy, "Build Caddy $($caddyPin.caddy_version) with xcaddy")) {
    $goVersion = Invoke-Native go @('env', 'GOVERSION')
    if ($goVersion.Trim() -ne "go$($caddyPin.go_version)") {
        throw "Caddy is pinned to Go $($caddyPin.go_version), but go is $goVersion"
    }
    # Never let Go fetch another toolchain: the pin is the toolchain.
    $env:GOTOOLCHAIN = 'local'
    $env:CGO_ENABLED = '0'
    $withModules = $caddyPin.modules | ForEach-Object { '--with', "$($_.module)@$($_.version)" }
    New-Item -ItemType Directory -Force (Split-Path $caddy) | Out-Null
    Invoke-Native go (@('run', "github.com/caddyserver/xcaddy/cmd/xcaddy@$($caddyPin.xcaddy_version)",
            'build', $caddyPin.caddy_version) + $withModules + @('--output', $caddy))
    $hash = (Get-FileHash $caddy -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $caddyPin.sha256) {
        throw "Caddy built with SHA-256 $hash; put it in installer\caddy\pin.json after checking the build"
    }
    Assert-Hash $caddy $caddyPin.sha256
    $modules = Invoke-Native $caddy @('list-modules')
    foreach ($module in $caddyPin.modules) {
        if ($modules -notcontains $module.caddy_module_id) { throw "Caddy lacks $($module.caddy_module_id)" }
    }
    # Every Caddyfile the gateway can render (the tests' golden files) must parse.
    foreach ($golden in Get-ChildItem (Join-Path $server 'tests\modules\gateway_golden') -Filter *.Caddyfile) {
        Invoke-Native $caddy @('adapt', '--config', $golden.FullName, '--adapter', 'caddyfile') | Out-Null
        Write-Host "Caddy accepts $($golden.Name)"
    }
}

if ($PSCmdlet.ShouldProcess((Join-Path $app 'VERSION'), "Write $version")) {
    Set-Content (Join-Path $app 'VERSION') -Encoding ascii -Value $version
}
Write-Host "Done: $app ($version)"
