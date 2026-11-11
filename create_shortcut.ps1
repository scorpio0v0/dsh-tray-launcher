<#
    创建 DeepSeek Harness 托盘启动器的快捷方式（可移植版：自动探测 Python 与图标）。

    用法（在任意 PowerShell 窗口执行）：
        pwsh -ExecutionPolicy Bypass -File "<本目录>\create_shortcut.ps1"

    可选参数：
        -PythonPath   指定 pythonw.exe 的完整路径（默认自动探测）
        -Desktop      只在桌面创建（默认同时创建桌面 + 开始菜单）
        -Remove       删除已创建的快捷方式
        -OpenFolder   创建完成后打开该工具目录
#>
[CmdletBinding()]
param(
    [string]$PythonPath,
    [switch]$Desktop,
    [switch]$Remove,
    [switch]$OpenFolder
)

$ErrorActionPreference = 'Stop'

$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Entry   = Join-Path $ToolDir 'dsh_tray.pyw'

function Find-Pythonw {
    param([string]$Override)

    if ($Override) {
        if (-not (Test-Path $Override)) { throw "指定的 pythonw 不存在: $Override" }
        return $Override
    }
    # 1) PATH 上的 pythonw
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # 2) 从 python.exe 推导同目录的 pythonw.exe
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) {
        $candidate = Join-Path (Split-Path -Parent $py.Source) 'pythonw.exe'
        if (Test-Path $candidate) { return $candidate }
    }
    # 3) py 启动器
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $dir = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($dir) {
            $candidate = Join-Path (Split-Path -Parent $dir.Trim()) 'pythonw.exe'
            if (Test-Path $candidate) { return $candidate }
        }
    }
    throw "找不到 pythonw.exe。请先安装 Python（python.org），或用 -PythonPath 指定路径。"
}

$Pythonw = Find-Pythonw -Override $PythonPath

# 图标：icon.ico（安装脚本生成）> tray.ico（本机复制）> 省略（用 pythonw 默认图标）
$Icon = $null
foreach ($name in 'icon.ico', 'tray.ico') {
    $candidate = Join-Path $ToolDir $name
    if (Test-Path $candidate) { $Icon = $candidate; break }
}

$DesktopDir = [Environment]::GetFolderPath('Desktop')
$StartDir   = Join-Path ([Environment]::GetFolderPath('Programs')) 'DeepSeek Harness'
$DesktopLnk = Join-Path $DesktopDir 'DeepSeek Harness.lnk'
$StartLnk   = Join-Path $StartDir  'DeepSeek Harness.lnk'

if ($Remove) {
    foreach ($path in @($DesktopLnk, $StartLnk)) {
        if (Test-Path $path) {
            Remove-Item $path -Force
            Write-Host "已删除: $path"
        }
    }
    return
}

if (-not (Test-Path $Entry)) {
    throw "找不到托盘程序: $Entry"
}

$Shell = New-Object -ComObject WScript.Shell

function New-DshShortcut {
    param(
        [Parameter(Mandatory)][string]$Path
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $lnk = $Shell.CreateShortcut($Path)
    $lnk.TargetPath       = $Pythonw
    $lnk.Arguments        = '"' + $Entry + '"'
    $lnk.WorkingDirectory = $ToolDir
    $lnk.Description      = 'DeepSeek Harness 后台服务托盘启动器'
    $lnk.WindowStyle      = 7            # 最小化启动，避免任何窗口闪现
    if ($Icon) { $lnk.IconLocation = $Icon }
    $lnk.Save()
    Write-Host "已创建快捷方式: $Path"
}

New-DshShortcut -Path $DesktopLnk
if (-not $Desktop) {
    New-DshShortcut -Path $StartLnk
}

Write-Host ''
Write-Host '完成。双击桌面上的 "DeepSeek Harness" 图标即可在后台启动服务。' -ForegroundColor Green
Write-Host "pythonw: $Pythonw"
if ($Icon) { Write-Host "图标:   $Icon" }

if ($OpenFolder) {
    Start-Process explorer.exe $ToolDir
}
