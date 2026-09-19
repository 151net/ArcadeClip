param([string]$ToolsDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) 'tools'))
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot '.validation-data') | Out-Null
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Run this script on Windows.' }
# The bundle is a GUI-subsystem executable: PowerShell neither waits for it nor captures its
# output directly, so run it through Start-Process with redirected streams.
function Invoke-Bundled {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string[]]$Arguments)
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $Path -ArgumentList $Arguments -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if ($process.ExitCode -ne 0) {
            throw "$Path $($Arguments -join ' ') exited with $($process.ExitCode): $(Get-Content -Raw $errFile)"
        }
        return @(Get-Content $outFile)
    } finally {
        Remove-Item -Force $outFile, $errFile -ErrorAction SilentlyContinue
    }
}
$toolsPath = (Resolve-Path $ToolsDirectory).Path
foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Test-Path (Join-Path $toolsPath $name) -PathType Leaf)) { throw "Missing $name in $toolsPath" }
}
uv sync --locked --group build
if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed.' }
uv run --locked python run/compile_messages.py
if ($LASTEXITCODE -ne 0) { throw 'Translation compilation failed.' }
uv run --locked python run/build_manual.py
if ($LASTEXITCODE -ne 0) { throw 'Manual build failed.' }
uv run --locked python run/check_manual.py
if ($LASTEXITCODE -ne 0) { throw 'Manual verification failed.' }
uv run --locked --group build python run/verify_tools.py $toolsPath
if ($LASTEXITCODE -ne 0) { throw 'Pinned tool verification failed.' }
uv run --locked --group build python run/collect_licenses.py --tools-directory $toolsPath
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
# PySide6 finalize() copies run/deployment/ArcadeClip.dist to exec_directory/title.dist.
$bundle = Join-Path $projectRoot 'dist/ArcadeClip.dist'
if (Test-Path $bundle) { throw "Move the previous bundle before building: $bundle" }
$previousPythonPath = $env:PYTHONPATH
$deploySpec = Join-Path $PSScriptRoot 'pysidedeploy.spec'
$deploySpecContent = [System.IO.File]::ReadAllBytes($deploySpec)
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    uv run --locked --group build pyside6-deploy -c run/pysidedeploy.spec --force --keep-deployment-files
    $buildExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
    [System.IO.File]::WriteAllBytes($deploySpec, $deploySpecContent)
}
if ($buildExitCode -ne 0) { throw 'Build failed.' }
$executable = Join-Path $bundle 'ArcadeClip.exe'
# pyside6-deploy catches some build errors, so its exit status alone is insufficient.
if (-not (Test-Path $executable -PathType Leaf)) { throw "Built executable missing: $executable" }
foreach ($asset in (Get-ChildItem -LiteralPath (Join-Path $projectRoot 'src/assets') -File)) {
    $bundledAsset = Join-Path $bundle (Join-Path 'assets' $asset.Name)
    if (-not (Test-Path -LiteralPath $bundledAsset -PathType Leaf)) { throw "Bundle missing asset: $($asset.Name)" }
    if ((Get-FileHash -LiteralPath $asset.FullName).Hash -ne (Get-FileHash -LiteralPath $bundledAsset).Hash) {
        throw "Bundle asset differs from source: $($asset.Name)"
    }
}
foreach ($pattern in @('qwindows.dll', '*ffmpegmediaplugin.dll', 'Qt6Multimedia.dll', 'avcodec*.dll')) {
    if (-not (Get-ChildItem -Path $bundle -Recurse -File -Filter $pattern)) { throw "Bundle missing Qt media dependency: $pattern" }
}
foreach ($relative in @('manual/manual.html', 'manual/guide.js', 'manual/guide.css', 'manual/images/review.png', 'locales/en/LC_MESSAGES/arcadeclip.mo', 'yt_dlp_ejs/yt/solver/core.min.js', 'yt_dlp_ejs/yt/solver/lib.min.js', 'certifi/cacert.pem', 'rapidocr/config.yaml', 'rapidocr/default_models.yaml')) {
    if (-not (Test-Path (Join-Path $bundle $relative) -PathType Leaf)) { throw "Bundle missing data: $relative" }
}
$target = Join-Path $bundle 'tools'
New-Item -ItemType Directory -Path $target | Out-Null
# The shared FFmpeg build needs its libraries beside the executables, so follow the lock file.
$lockedTools = (Get-Content -Raw (Join-Path $PSScriptRoot 'tools.lock.json') | ConvertFrom-Json).files.PSObject.Properties.Name
foreach ($name in $lockedTools) {
    $sourceTool = Join-Path $toolsPath $name
    if (Test-Path -LiteralPath $sourceTool -PathType Leaf) {
        Copy-Item -LiteralPath $sourceTool -Destination (Join-Path $target $name)
    }
}
if (-not (Test-Path (Join-Path $target 'deno.exe') -PathType Leaf)) {
    $denoBinary = uv run --locked python -c "from deno import find_deno_bin; print(find_deno_bin())"
    if ($LASTEXITCODE -ne 0) { throw 'Bundled Deno lookup failed.' }
    Copy-Item -LiteralPath $denoBinary.Trim() -Destination (Join-Path $target 'deno.exe')
}
$denoLicense = uv run --locked python -c "from importlib.metadata import distribution; d=distribution('deno'); print(next(d.locate_file(f) for f in d.files if str(f).endswith('licenses/LICENSE')))"
if ($LASTEXITCODE -ne 0) { throw 'Deno license lookup failed.' }
Copy-Item -LiteralPath $denoLicense.Trim() -Destination (Join-Path $target 'DENO-LICENSE.txt')
uv run --locked --group build python run/verify_tools.py $target
if ($LASTEXITCODE -ne 0) { throw 'Bundled tool verification failed.' }
$denoVersion = & (Join-Path $target 'deno.exe') --version
if ($LASTEXITCODE -ne 0 -or -not $denoVersion) { throw 'Bundled Deno launch failed.' }
$ytdlpVersion = Invoke-Bundled $executable @('--yt-dlp', '--version')
if (-not $ytdlpVersion) { throw 'Bundled yt-dlp reported no version.' }
$extractors = Invoke-Bundled $executable @('--yt-dlp', '--list-extractors')
if ('youtube' -notin $extractors) { throw 'Bundled YouTube extractor failed to load.' }
Write-Host "Bundled yt-dlp $ytdlpVersion with $($extractors.Count) extractors."
# Not a .lnk: a shortcut stores the absolute path it was built at, which is this
# machine's, so it breaks wherever the reader unpacks the folder. %~dp0 is the folder
# the launcher itself sits in, so this one follows the bundle.
$launcher = Join-Path $bundle 'ArcadeClip_dbg.cmd'
@(
    '@echo off'
    'rem Runs ArcadeClip with diagnostic logging into the data folder.'
    'start "" "%~dp0ArcadeClip.exe" --debug'
) | Set-Content -Path $launcher -Encoding ascii
if (-not (Test-Path $launcher -PathType Leaf)) { throw 'Debug launcher creation failed.' }
Write-Host "Bundle: $bundle"
Write-Host 'Verify video playback, seeking, and a bounded YouTube download on a clean Windows machine.'
