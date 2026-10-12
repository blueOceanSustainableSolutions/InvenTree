param(
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'setup', 'admin', 'test')]
    [string]$Action = 'start',

    [switch]$Worker,

    # 'test' only: skip translations and the migration file check (inner loop)
    [switch]$Quick,

    # 'test' only: rebuild the cached test image even when it looks current
    [switch]$RebuildImage,

    # Test labels for the 'test' action (e.g. fleet users.tests); all apps when empty
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Tests
)

$ErrorActionPreference = 'Stop'

$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$RuntimeDir = Join-Path $Root 'dev\runtime'
$PidFile = Join-Path $RuntimeDir 'inventree-local-pids.json'
$VenvActivate = Join-Path $Root 'dev\venv\Scripts\Activate.ps1'
$VenvPython = Join-Path $Root 'dev\venv\Scripts\python.exe'
$VenvInvoke = Join-Path $Root 'dev\venv\Scripts\invoke.exe'
$ManagePy = Join-Path $Root 'src\backend\InvenTree\manage.py'
$LocalDataDir = Join-Path $Root 'dev\data'
$LocalStaticDir = Join-Path $LocalDataDir 'static'
$LocalMediaDir = Join-Path $LocalDataDir 'media'
$LocalBackupDir = Join-Path $LocalDataDir 'backup'
$LocalCacheDir = Join-Path $LocalDataDir 'cache'
$LocalDb = Join-Path $LocalDataDir 'inventree.sqlite3'
$GtkRuntimeBin = 'C:\Program Files\GTK3-Runtime Win64\bin'

function Set-LocalEnvironment {
    New-Item -ItemType Directory -Force $LocalStaticDir, $LocalMediaDir, $LocalBackupDir, $LocalCacheDir | Out-Null

    if ((Test-Path $GtkRuntimeBin) -and (-not ($env:PATH -split ';' | Where-Object { $_ -eq $GtkRuntimeBin }))) {
        $env:PATH = "$GtkRuntimeBin;$env:PATH"
    }

    $env:INVENTREE_STATIC_ROOT = $LocalStaticDir
    $env:INVENTREE_MEDIA_ROOT = $LocalMediaDir
    $env:INVENTREE_BACKUP_DIR = $LocalBackupDir
    $env:INVENTREE_DB_ENGINE = 'sqlite3'
    $env:INVENTREE_DB_NAME = $LocalDb
    $env:INVENTREE_DEBUG = 'True'
    $env:INVENTREE_SITE_URL = 'http://localhost:8000'
    $env:XDG_CACHE_HOME = $LocalCacheDir
}

function Invoke-Checked([scriptblock]$Command) {
    # Native tools (docker) write progress to stderr; with 'Stop' PowerShell 5.1 turns that
    # into a terminating error when output is redirected. The exit code is checked below.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        & $Command
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

function New-UnhashedRequirementsFile([string]$RequirementsFile) {
    Ensure-RuntimeDir

    $name = [System.IO.Path]::GetFileNameWithoutExtension($RequirementsFile)
    $outputFile = Join-Path $RuntimeDir "$name-unhashed.txt"
    $outputLines = @()

    foreach ($line in Get-Content $RequirementsFile) {
        $trimmed = $line.TrimStart()

        if ($trimmed.StartsWith('--hash=')) {
            continue
        }

        if ($line.TrimEnd().EndsWith('\')) {
            $line = $line.TrimEnd().TrimEnd('\').TrimEnd()
        }

        $outputLines += $line
    }

    $outputLines | Set-Content -Path $outputFile -Encoding UTF8
    return $outputFile
}

function Ensure-RuntimeDir {
    New-Item -ItemType Directory -Force $RuntimeDir | Out-Null
}

function Read-PidState {
    if (Test-Path $PidFile) {
        $json = Get-Content $PidFile -Raw | ConvertFrom-Json
        $state = @{}

        foreach ($property in $json.PSObject.Properties) {
            $state[$property.Name] = $property.Value
        }

        return $state
    }

    return @{}
}

function Write-PidState($State) {
    Ensure-RuntimeDir
    $State | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
}

function Test-ProcessAlive($ProcessId) {
    if (-not $ProcessId) {
        return $false
    }

    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Test-BackendDependencies {
    if (-not (Test-Path $VenvPython)) {
        return $false
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        $output = & $VenvPython -c "import django, structlog, rest_framework, django_q" 2>&1
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Test-FrontendDependencies {
    $nodeModules = Join-Path $Root 'src\frontend\node_modules'
    return Test-Path $nodeModules
}

function Test-DatabaseReady {
    return Test-Path $LocalDb
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue

    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId $child.ProcessId
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue

    if ($process) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Start-ServiceProcess([string]$Name, [string]$Command) {
    Ensure-RuntimeDir
    Set-LocalEnvironment

    $stdout = Join-Path $RuntimeDir "$Name.log"
    $stderr = Join-Path $RuntimeDir "$Name.err.log"
    $wrappedCommand = "Set-Location '$Root'; if (Test-Path '$GtkRuntimeBin') { `$env:PATH='$GtkRuntimeBin;' + `$env:PATH }; `$env:INVENTREE_STATIC_ROOT='$LocalStaticDir'; `$env:INVENTREE_MEDIA_ROOT='$LocalMediaDir'; `$env:INVENTREE_BACKUP_DIR='$LocalBackupDir'; `$env:INVENTREE_DB_ENGINE='sqlite3'; `$env:INVENTREE_DB_NAME='$LocalDb'; `$env:INVENTREE_DEBUG='True'; `$env:INVENTREE_SITE_URL='http://localhost:8000'; `$env:XDG_CACHE_HOME='$LocalCacheDir'; if (Test-Path '$VenvActivate') { . '$VenvActivate' }; $Command"

    $process = Start-Process `
        -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $wrappedCommand) `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru

    return $process.Id
}

function Start-Local {
    Set-LocalEnvironment

    if ((-not (Test-BackendDependencies)) -or (-not (Test-FrontendDependencies)) -or (-not (Test-DatabaseReady))) {
        Write-Host "Local environment is missing dependencies. Running setup..."
        Setup-Local
    }

    if (-not (Test-Path $VenvInvoke)) {
        throw "Setup did not complete. Fix the error above and run .\start again."
    }

    Write-Host "Applying database migrations..."
    Invoke-Checked { & $VenvPython $ManagePy migrate --noinput }

    $state = Read-PidState

    if (Test-ProcessAlive $state.backend) {
        Write-Host "Backend already running on PID $($state.backend)"
    } else {
        $state['backend'] = Start-ServiceProcess -Name 'backend' -Command "& '$VenvPython' '$ManagePy' runserver 0.0.0.0:8000"
        Write-Host "Started backend: http://localhost:8000 (PID $($state['backend']))"
    }

    if (Test-ProcessAlive $state.frontend) {
        Write-Host "Frontend already running on PID $($state.frontend)"
    } else {
        $frontendDir = Join-Path $Root 'src\frontend'
        $state['frontend'] = Start-ServiceProcess -Name 'frontend' -Command "Set-Location '$frontendDir'; npm run compile; if (`$LASTEXITCODE -eq 0) { npm run dev -- --host }"
        Write-Host "Started frontend: http://localhost:5173 (PID $($state['frontend']))"
    }

    if ($Worker) {
        if (Test-ProcessAlive $state.worker) {
            Write-Host "Worker already running on PID $($state.worker)"
        } else {
            $state['worker'] = Start-ServiceProcess -Name 'worker' -Command "& '$VenvPython' '$ManagePy' qcluster"
            Write-Host "Started worker (PID $($state['worker']))"
        }
    }

    Write-PidState $state
    Write-Host "Logs are in dev\runtime\"
}

function Stop-Local {
    $state = Read-PidState

    foreach ($name in @('frontend', 'backend', 'worker')) {
        $pidValue = $state[$name]

        if (Test-ProcessAlive $pidValue) {
            Stop-ProcessTree -ProcessId $pidValue
            Write-Host "Stopped $name (PID $pidValue)"
        } elseif ($pidValue) {
            Write-Host "$name was not running (old PID $pidValue)"
        }

        $state.Remove($name)
    }

    Write-PidState $state
}

function Show-Status {
    $state = Read-PidState

    foreach ($name in @('backend', 'frontend', 'worker')) {
        $pidValue = $state[$name]
        $status = if (Test-ProcessAlive $pidValue) { "running (PID $pidValue)" } else { 'stopped' }
        Write-Host "$name`: $status"
    }
}

function Show-Logs {
    Ensure-RuntimeDir
    Write-Host "Backend log:  dev\runtime\backend.log"
    Write-Host "Backend err:  dev\runtime\backend.err.log"
    Write-Host "Frontend log: dev\runtime\frontend.log"
    Write-Host "Frontend err: dev\runtime\frontend.err.log"
    Write-Host "Worker log:   dev\runtime\worker.log"
    Write-Host "Worker err:   dev\runtime\worker.err.log"
    Write-Host ""
    Write-Host "Tail example:"
    Write-Host "Get-Content dev\runtime\backend.log -Wait -Tail 80"
}

function Setup-Local {
    Set-LocalEnvironment

    if (-not (Test-Path $VenvActivate)) {
        Invoke-Checked { python -m venv (Join-Path $Root 'dev\venv') }
    }

    . $VenvActivate

    if (-not (Test-BackendDependencies)) {
        Invoke-Checked { & $VenvPython -m pip install --upgrade pip setuptools invoke }

        $runtimeRequirements = Join-Path $Root 'src\backend\requirements-3.14.txt'
        $devRequirements = Join-Path $Root 'src\backend\requirements-dev-3.14.txt'

        if (-not (Test-Path $runtimeRequirements)) {
            $runtimeRequirements = Join-Path $Root 'src\backend\requirements.txt'
        }

        if (-not (Test-Path $devRequirements)) {
            $devRequirements = Join-Path $Root 'src\backend\requirements-dev.txt'
        }

        $unhashedRuntimeRequirements = New-UnhashedRequirementsFile $runtimeRequirements
        Invoke-Checked { & $VenvPython -m pip install --no-cache-dir --disable-pip-version-check -r $unhashedRuntimeRequirements }

        $unhashedDevRequirements = New-UnhashedRequirementsFile $devRequirements
        Invoke-Checked { & $VenvPython -m pip install --no-cache-dir --disable-pip-version-check -r $unhashedDevRequirements }
    }

    if (-not (Test-FrontendDependencies)) {
        Push-Location (Join-Path $Root 'src\frontend')
        try {
            Invoke-Checked { corepack yarn install }
        } finally {
            Pop-Location
        }
    }

    Invoke-Checked { & $VenvPython $ManagePy migrate --noinput }
    Write-Host "Setup done."
    Write-Host "If you still need an admin login, run: dev\venv\Scripts\invoke.exe superuser"
}

$TestBaseImage = 'inventree/inventree:1.4.0'
$TestImage = 'bo-inventree-test:latest'

function Get-TestImageHash {
    # Changes when anything baked into the test image changes
    $dockerfile = Join-Path $Root 'dev\test-image\Dockerfile'
    $requirements = Join-Path $Root 'src\backend\requirements-dev-3.14.txt'
    $text = $TestBaseImage + (Get-Content $dockerfile -Raw) + (Get-Content $requirements -Raw)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    return (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 16)
}

function Update-TestImage {
    # Build the cached test image when it is missing, out of date, or -RebuildImage is given
    $hash = Get-TestImageHash
    # PowerShell 5.1 strips inner quotes from native arguments, so read all labels as JSON
    $current = $null
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $labels = docker image inspect $TestImage --format '{{json .Config.Labels}}' 2>$null
    $ErrorActionPreference = $previousPreference

    if ($LASTEXITCODE -eq 0 -and $labels) {
        $current = ($labels | ConvertFrom-Json).'bo.test.hash'
    }

    if (-not $RebuildImage -and $current -eq $hash) {
        return
    }

    Write-Host "Building test image $TestImage (hash $hash)..."
    # Small build context: only the Dockerfile and the requirements file
    $context = Join-Path $RuntimeDir 'test-image'
    New-Item -ItemType Directory -Force $context | Out-Null
    Copy-Item (Join-Path $Root 'dev\test-image\Dockerfile') $context -Force
    Copy-Item (Join-Path $Root 'src\backend\requirements-dev-3.14.txt') $context -Force

    Invoke-Checked {
        docker build --pull=false `
            --build-arg "BASE_IMAGE=$TestBaseImage" `
            --build-arg "TEST_HASH=$hash" `
            -t $TestImage $context
    }
}

function Invoke-Tests {
    # Run the backend tests on Linux in Docker, exactly like the upstream CI
    # (see dev\ci-test.sh). Native Windows runs cannot pass: printing, static
    # file paths and plugin loading in InvenTree assume Linux, and running
    # against the dev database breaks tests which rely on content type ids.
    docker version --format '{{.Server.Version}}' *> $null

    if ($LASTEXITCODE -ne 0) {
        throw 'Docker is not running. Start Docker Desktop and try again.'
    }

    Update-TestImage

    $labels = ($Tests -join ' ')
    $mode = if ($Quick) { 'quick' } else { 'full' }

    Write-Host "Running backend tests ($mode) in $TestImage$(if ($labels) { " ($labels)" } else { ' (all apps)' })..."
    Invoke-Checked {
        docker run --rm `
            -v "${Root}:/src-ro:ro" `
            -e "TESTS=$labels" `
            -e "QUICK=$(if ($Quick) { 1 } else { 0 })" `
            --entrypoint sh $TestImage /src-ro/dev/ci-test.sh
    }
}

switch ($Action) {
    'start' { Start-Local }
    'stop' { Stop-Local }
    'restart' { Stop-Local; Start-Local }
    'status' { Show-Status }
    'logs' { Show-Logs }
    'setup' { Setup-Local }
    'admin' { Set-LocalEnvironment; & $VenvPython $ManagePy createsuperuser }
    'test' { Invoke-Tests }
}
