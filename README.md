# dsh-tray-launcher

把 `pnpm dsh web` 变成 Windows 桌面上的"双击即用"小应用：后台静默启动、常驻系统托盘、可最小化到后台，并集成了常用管理功能。适合通过 `git clone` 方式使用 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/DeepSeek-Harness) 但没有使用官方桌面版的用户。

> 本项目是独立的社区工具，与 DeepSeek 官方无关；DeepSeek Harness 及其商标归其所有者所有。

## 它解决什么问题

| 手动方式 | 用本工具 |
|---|---|
| 打开 pwsh → `cd <dsh 仓库>` → `pnpm dsh web` | 双击桌面 **DeepSeek Harness** 图标 |
| 终端窗口必须留在前台，关掉就断服务 | 完全后台运行，任务栏无窗口，只在托盘显示图标 |
| 看不到启动日志，出错只能猜 | 托盘右键 → **查看运行日志…**（实时窗口 + 落盘文件） |
| 反复点击浏览器地址会开出重复标签页 | 通过 Edge CDP 复用同一个标签页（见下文） |
| 关服务要找回终端窗口 | 托盘右键 → **退出（并停止服务）** |

## 环境要求

- Windows 10 / 11
- Python 3.9+（含标准安装自带的 `pythonw.exe`）
- PyQt5（安装脚本会自动 `pip install`）
- 已 clone 的 DSH 仓库（`package.json` + `apps/cli` 结构），且能运行 `pnpm dsh web`
- Microsoft Edge（用于标签页复用；其他浏览器会退化为普通打开）

## 安装

```powershell
git clone https://github.com/<你>/dsh-tray-launcher.git
cd dsh-tray-launcher
pwsh -ExecutionPolicy Bypass -File .\install.ps1
```

安装脚本会：探测 Python → 安装 PyQt5 → 生成图标 → 创建桌面与开始菜单快捷方式。

若托盘启动后提示找不到 DSH 仓库，把仓库路径写进配置：

```powershell
Copy-Item tray.config.example.json tray.config.json
# 编辑 tray.config.json 的 "dsh_dir" 为你的 dsh 仓库路径
```

## 配置

优先级：**环境变量 > `tray.config.json` > 自动探测 > 默认值**。

| 配置项 | config 键 | 环境变量 | 默认 |
|---|---|---|---|
| DSH 仓库路径 | `dsh_dir` | `DSH_TRAY_DIR` | 自动探测（`D:\deepseek-harness`、`~\deepseek-harness` 等常见位置） |
| Web 端口 | `port` | `DSH_TRAY_PORT` | `3080` |
| Edge 调试端口 | `cdp_port` | `DSH_TRAY_CDP_PORT` | `9222` |
| 托管 Edge 配置目录 | `edge_profile` | `DSH_TRAY_EDGE_PROFILE` | `<工具目录>\edge-profile` |

另有环境变量开关（仅调试用）：`DSH_TRAY_NO_AUTO_OPEN=1`（就绪后不自动开浏览器）、`DSH_TRAY_NO_BROWSER=1`（完全禁止开浏览器）、`DSH_TRAY_TEST_CMD`（替换启动命令做自检）、`DSH_TRAY_LOCK_PORT`（单实例锁端口）。

## 托盘菜单

| 菜单项 | 作用 |
|---|---|
| `DeepSeek Harness：● 运行中` | 状态行：● 服务在线，○ 未运行 |
| **打开 Web 界面** | 打开 GUI；CDP 可用时复用已有标签页 |
| **复制访问地址** | 复制带一次性令牌的地址到剪贴板 |
| **查看运行日志…** | 实时日志窗口（自动滚动 / 清空 / 复制），同时落盘 `tray.log` |
| **重启服务** | 结束当前服务并重新拉起 |
| **结束占用端口的旧实例并重新启动** | 仅当端口被非托盘进程占用时出现，带确认框 |
| **启动服务** / **停止服务** | 手动控制（只对托盘自己启动的服务生效） |
| **打开项目文件夹** | 打开 DSH 仓库目录 |
| **开机自启动** | 写入 / 移除 `HKCU\...\CurrentVersion\Run` |
| **退出（并停止服务）** | 停止服务并退出托盘 |

单击或双击托盘图标 = 打开 Web 界面。托盘是单实例的：重复双击快捷方式不会出现第二个图标。

## Edge 标签页复用（CDP）原理

**核心事实：CDP 只能控制「以 `--remote-debugging-port` 启动的那个 Edge 实例」**，而系统默认方式拉起的 Edge（`msedge.exe --single-argument <url>`）不带调试端口，CDP 根本看不到它。所以本工具从不把 `webbrowser.open` 当正常路径：

1. 探测 `127.0.0.1:<cdp_port>`；
2. **CDP 已就绪** → 在该实例里找 DSH 标签页：有就激活到前台，没有就在该实例内新建；
3. **CDP 未就绪** → 托盘**自己**用调试端口 + 独立配置目录拉起 Edge（约 1~3 秒），端口就绪后回到第 2 步；
4. 只有连 `msedge.exe` 都找不到时，才退回 `webbrowser.open`。

为什么必须用独立配置目录：Edge 限制同一个 `user-data-dir` 只能有一个主实例，调试端口只对第一个带该参数启动的进程生效。日常 Edge 占着默认配置时，加参数启动会被吞掉，端口不会打开。

`tray.log` 中的关键行：

```
[托盘] 正在启动带调试端口 9222 的 Edge 实例（配置：…）   ← 自动拉起
[托盘] 托管 Edge 调试端口已就绪。
[托盘] 已在 Edge 调试实例中新建标签页：…                ← 第一次
[托盘] 已激活现有 Edge 标签页：…                        ← 之后每次
```

**安全提示**：`--remote-debugging-port` 允许本机任意程序控制该 Edge 实例（仅监听 `127.0.0.1`）。介意的话，不用时关闭那个 Edge 窗口即可，下次点击托盘会重新拉起。

## 命令行控制

```powershell
# 让已运行的托盘打开网页
pythonw dsh_tray.pyw --open

# 让已运行的托盘干净退出（并停止它启动的服务）
pythonw dsh_tray.pyw --quit
```

## 故障排查

| 现象 | 处理 |
|---|---|
| 双击后无反应、无托盘 | 查看 `tray-error.log`；或用 `python dsh_tray.pyw`（带控制台）手动跑一次看报错 |
| 浏览器 401 | 托盘自己启动时会捕获带令牌的地址；若为"接管"模式（服务不是托盘启动的），从 `tray.log` 找 `dsh web: http://…?token=…` 复制到浏览器 |
| 提示端口被占用 | 旧的 `pnpm dsh web` 还在跑：用菜单"结束占用端口的旧实例并重新启动"，或手动关闭 |
| 退出托盘后网页仍可访问 | 服务不是托盘启动的（接管模式）；按上一条处理 |
| 开机自启动未生效 | 检查注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `DeepSeekHarnessTray` |
| 托盘图标"不见了" | Windows 11 折叠进任务栏 `^` 溢出区，拖出固定即可 |

## 卸载

1. 托盘右键 → **退出（并停止服务）**；
2. `pwsh -File .\create_shortcut.ps1 -Remove` 删除快捷方式；
3. 若开过开机自启动，先在托盘菜单取消勾选；
4. 删除本目录（对 DSH 仓库零写入，无需还原任何东西）。

## License

[MIT](LICENSE)
