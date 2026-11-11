$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$localPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$oldPython = Join-Path $projectDir '..\toyoko-tracker\.venv\Scripts\python.exe'
$python = $null

function Has-Dependencies($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    & $path -c 'import flask, requests, bs4' 2>$null
    return $LASTEXITCODE -eq 0
}

try {
    if (Has-Dependencies $localPython) {
        $python = $localPython
    } elseif (Has-Dependencies $oldPython) {
        $python = $oldPython
    } else {
        if (-not (Test-Path -LiteralPath $localPython)) {
            $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
            $pythonArgs = @()
            if ($launcher) {
                $pythonCommand = $launcher.Source
                $pythonArgs = @('-3')
            } else {
                $installedPython = Get-Command python.exe -ErrorAction SilentlyContinue
                if (-not $installedPython) { throw 'Python 3.10 이상을 설치하고 Start.cmd를 다시 실행해 주세요. https://www.python.org/downloads/' }
                $pythonCommand = $installedPython.Source
            }
            & $pythonCommand @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 이상이 필요합니다. 설치 후 Start.cmd를 다시 실행해 주세요. https://www.python.org/downloads/' }
            Write-Host '처음 실행을 준비하고 있습니다. 잠시 기다려 주세요.'
            & $pythonCommand @pythonArgs -m venv (Join-Path $projectDir '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Python 가상 환경을 만들지 못했습니다.' }
        }
        Write-Host '필요한 라이브러리를 설치하고 있습니다. 인터넷 연결이 필요합니다.'
        & $localPython -m pip install -r (Join-Path $projectDir 'requirements.txt')
        if ($LASTEXITCODE -ne 0 -or -not (Has-Dependencies $localPython)) { throw '라이브러리 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.' }
        $python = $localPython
    }
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
    $starter = Join-Path $PSScriptRoot 'launcher.pyw'
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Python 실행 파일을 찾지 못했습니다.' }
    Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f $starter) -WorkingDirectory $projectDir -WindowStyle Hidden
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
