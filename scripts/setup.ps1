param([string]$Python = 'python', [switch]$Rebuild)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$pythonInfoJson = & $Python (Join-Path $PSScriptRoot 'python_info.py')
if ($LASTEXITCODE -ne 0) { throw '需要 Windows 和 Python 3.11+；推荐 Python 3.12。' }
$pythonInfo = $pythonInfoJson | ConvertFrom-Json
$venvDir = Join-Path $projectDir '.venv'
$markerPath = Join-Path $venvDir '.doubao-environment.json'
if (Test-Path -LiteralPath $venvDir) {
    $reuse = $false
    if (-not $Rebuild -and (Test-Path -LiteralPath $markerPath)) {
        try {
            $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $reuse = ($marker.project_root -eq $projectDir -and $marker.base_prefix -eq $pythonInfo.base_prefix -and (($marker.python_version -join '.') -eq ($pythonInfo.version -join '.')))
        } catch { $reuse = $false }
    }
    if (-not $reuse) {
        $resolvedVenv = (Resolve-Path -LiteralPath $venvDir).Path
        $resolvedProject = (Resolve-Path -LiteralPath $projectDir).Path.TrimEnd('\')
        if ($resolvedVenv -ne ($resolvedProject + '\.venv')) { throw '虚拟环境路径校验失败。' }
        $previousVenv = Join-Path $resolvedProject ('.venv.previous-' + (Get-Date -Format 'yyyyMMdd-HHmmss-ffff'))
        if (-not $previousVenv.StartsWith($resolvedProject + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '备份路径超出项目目录。' }
        Move-Item -LiteralPath $resolvedVenv -Destination $previousVenv -ErrorAction Stop
        Write-Host "旧虚拟环境已保留在 $previousVenv，将重新创建。"
    }
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    & $Python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $projectPython -m pip install -r (Join-Path $projectDir 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $projectPython -m pip install --no-deps -e $projectDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $projectPython (Join-Path $PSScriptRoot 'configuration.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '安装完成。接入 Codex：运行 scripts\register_codex.py --write；其他客户端参考 mcp-config.json。'
exit 0
