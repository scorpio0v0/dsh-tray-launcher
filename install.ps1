<#
    dsh-tray-launcher 一键安装脚本。

    做四件事：
      1. 探测 Python / pythonw.exe（未装则给出明确提示）
      2. 检查并安装 PyQt5 依赖
      3. 生成自有图标 icon.ico（已存在则跳过）
      4. 创建桌面 + 开始菜单快捷方式

    用法：
        pwsh -ExecutionPolicy Bypass -File "<本目录>\install.ps1"
    可选参数：
        -SkipShortcuts   只准备依赖与图标，不创建快捷方式
#>
[CmdletBinding()]
param(
    [switch]$SkipShortcuts
)

$ErrorActionPreference = 'Stop'
$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Python {
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $exe = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($exe -and (Test-Path $exe.Trim())) { return $exe.Trim() }
    }
    return $null
}

Write-Host '== dsh-tray-launcher 安装 ==' -ForegroundColor Cyan

# --- 1. Python ---
$python = Find-Python
if (-not $python) {
    Write-Host '未找到 Python。请先安装 Python 3.9+（https://www.python.org/downloads/），' -ForegroundColor Red
    Write-Host '安装时勾选 "Add python.exe to PATH"，然后重新运行本脚本。' -ForegroundColor Red
    exit 1
}
Write-Host "[1/4] Python: $python"

$pythonDir = Split-Path -Parent $python
$pythonw = Join-Path $pythonDir 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    Write-Host "缺少 $pythonw（pythonw 是无控制台窗口的解释器，标准安装自带）。" -ForegroundColor Red
    exit 1
}

# --- 2. PyQt5 ---
& $python -c "import PyQt5" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[2/4] 正在安装 PyQt5…'
    & $python -m pip install --disable-pip-version-check PyQt5
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'PyQt5 安装失败，请手动执行: python -m pip install PyQt5' -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host '[2/4] PyQt5 已就绪'
}

# --- 3. 图标 ---
$hasIcon = (Test-Path (Join-Path $ToolDir 'icon.ico')) -or (Test-Path (Join-Path $ToolDir 'tray.ico'))
if ($hasIcon) {
    Write-Host '[3/4] 图标已存在，跳过生成'
}
else {
    Write-Host '[3/4] 生成自有图标 icon.ico…'
    & $python (Join-Path $ToolDir 'make_icon.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '图标生成失败（不影响使用，托盘会用内置兜底图案）。' -ForegroundColor Yellow
    }
}

# --- 4. 快捷方式 ---
if ($SkipShortcuts) {
    Write-Host '[4/4] 按参数跳过快捷方式创建'
}
else {
    Write-Host '[4/4] 创建快捷方式…'
    & (Join-Path $ToolDir 'create_shortcut.ps1') -PythonPath $pythonw
}

Write-Host ''
Write-Host '安装完成。' -ForegroundColor Green
Write-Host '  * 双击桌面 "DeepSeek Harness" 即可在后台启动 dsh web。'
Write-Host '  * 若托盘没有找到 dsh 仓库，请复制 tray.config.example.json 为 tray.config.json'
Write-Host '    并把 "dsh_dir" 改为你的 dsh 仓库路径。'
Write-Host '  * 上下文菜单里可切换开机自启动、查看日志、重启服务等。'
