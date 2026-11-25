; ===========================================================================
; H-Quant — Inno Setup 安装包脚本
; ===========================================================================
; 用途: 把 PyInstaller 产出的 dist/HQuant/ 文件夹封装成
;       单个 Setup.exe 安装程序 (双击→安装向导→快捷方式→可卸载)。
;
; 构建 (本地):
;   1. 先跑 PyInstaller: cd backend && uv run pyinstaller ../packaging/hquant.spec
;   2. 再跑 Inno Setup:   ISCC.exe packaging\hquant.iss
;   3. 产物: packaging\Output\HQuant-Setup-x.x.x.exe
;
; 设计决策:
;   - 装到用户目录 {localappdata}\Programs\ (不弹 UAC, 不需管理员)
;   - 用户数据存在 {app}\data\ (与程序同处一个总目录, 视觉直观)
;   - 卸载时询问是否删除用户数据 ({app}\data\)
;   - 覆盖安装(升级)不动 data\: Inno Setup 只写程序文件, data 不在安装清单
;   - 桌面 + 开始菜单快捷方式
;   - 卸载入口 (控制面板可见)
; ===========================================================================

#define MyAppName          "H-Quant"
#define MyAppNameEN       "H-Quant"
#define MyAppExeName      "HQuant.exe"
#define MyAppPublisher    "H-Quant"

; 版本号: 从 frontend_v2/package.json 读取, 与 Release tag 保持一致
; 手动指定更可靠 (CI 传入 /DMyAppVersion)
#ifndef MyAppVersion
  #define MyAppVersion     "0.0.0"
#endif

[Setup]
; 基本信息
; AppId 固定 GUID: Inno 默认从 AppName 派生, 一旦改名旧安装会变成重复程序;
; 固定后应用名再调整也保持同一卸载/升级身份
AppId={{B7F4C9E2-3A51-4D8E-9C60-5E8A2F1D7B43}
AppName={#MyAppName}
AppVerName={#MyAppName} {#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 默认装到 D 盘 (非系统盘), 用户可在向导中改任意位置
; 若 D 盘不存在, [Code] 段 InitializeWizard 会自动回退到用户目录
DefaultDirName=D:\H-Quant
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=HQuant-Setup-{#MyAppVersion}

; 关键: 不需要管理员权限, 永不弹 UAC
; 装到 D 盘普通目录 (非 Program Files) 不需要管理员权限
PrivilegesRequired=lowest
; 允许用户在向导中自由选择安装目录
DisableDirPage=no

; 压缩
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; 界面
WizardStyle=modern
DisableWelcomePage=no
DisableReadyPage=no
; 安装向导/卸载图标: 直接引用品牌资产唯一来源 (brand/README 接入指引),
; 不再使用 packaging/icon.ico 的旧手绘版本, 避免两处漂移
SetupIconFile=..\brand\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; 卸载相关
Uninstallable=yes
CreateUninstallRegKey=yes

; 安装前强制关闭正在运行的 H-Quant (见 [Code] PrepareToInstall)。
; 不用 Inno 的 CloseApplications: 它靠 Restart Manager 发关闭请求并等响应,
; 应用不理会时照样继续, 然后卡在「_internal 里的 pyd 被占用」上失败。
CloseApplications=no

[Languages]
; 简中语言包内置在 packaging/ 下 (从 Inno Setup 官方仓库获取),
; 不依赖安装目录是否含该文件 (CI 友好)。
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 把 PyInstaller 产出的整个文件夹搬进安装目录
; Source 路径相对于 .iss 文件所在目录
Source: "..\backend\dist\HQuant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; .NET 按 EXE 文件名读取同目录配置，不能放进 PyInstaller 的 _internal。
Source: "HQuant.exe.config"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; 旧版程序文件残留清理 (升级原地安装, 旧文件不会被新包覆盖删除):
;   - TSP.exe / TSP.exe.config: 改名前的可执行文件与 .NET 配置, 不删会并存两个入口
;   - _internal: PyInstaller onedir 运行时目录, 新版删掉的模块/扩展会在此残留并被
;     pkgutil.iter_modules 重新发现。用户数据在 {app}\data, 不受影响。
Type: files; Name: "{app}\TSP.exe"
Type: files; Name: "{app}\TSP.exe.config"
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
; 开始菜单
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

; 桌面 (可选, 由 Task 控制)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后启动应用
; shellexec: 部分机器对 exe 路径存有「以管理员身份运行」兼容标志/策略要求提权,
; CreateProcess 无法弹 UAC 会直接报错误码 740; ShellExecute 遇提权正常弹 UAC,
; asInvoker 场景行为不变。
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
; 卸载前先关闭正在运行的应用 (否则 exe 被占用删不掉)
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillApp"

; [UninstallDelete] 故意不删 {app}:
; 用户数据在 {app}\data\, 若这里写 Type: filesandordirs; Name: "{app}" 会连数据一起删。
; 卸载默认行为已足够 —— Inno Setup 会删除它安装清单内的所有程序文件, 只留下运行时
; 生成的 data\ 目录。是否清理 data\ 由下方 [Code] 的卸载询问逻辑决定。

[Code]
// ── 应用是否仍在运行 ──────────────────────────────────────────
// tasklist 带过滤时, 没有匹配进程会打印「没有运行的任务匹配指定标准」,
// 不含 exe 名 → find 找不到 → 退出码 1。据此判断比 taskkill 的退出码可靠
// (taskkill 的各种失败码分不清「没在跑」和「没权限杀」)。
function IsAppRunning(): Boolean;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'),
       '/C tasklist /FI "IMAGENAME eq {#MyAppExeName}" /NH | find /I "{#MyAppExeName}" >nul',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := (ResultCode = 0);
  Log(Format('HQuant 运行检测: %d (True=仍在运行)', [ResultCode]));
end;

// ── 安装前强制关闭正在运行的应用 ───────────────────────────────
// 升级时报「无法替换 HQuant.exe / _internal 里的文件被占用」, 用户得自己
// 去任务管理器结束进程再重装。这里在写文件之前先处理掉。
//
// 上一版把 /F 挂在 `if 软杀成功 then` 里, 结果一次都没执行到, 应用纹丝不动:
// taskkill 不加 /F 时找不到可见窗口会返回 128, 而判定条件只看返回码。
// 现在改成: 软杀 (只发 WM_CLOSE, 让应用正常退出并落盘) → 无条件 /F 重试
// → 最后用 tasklist 确认真的没了, 仍在就明确告诉用户原因。
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Cmd: String;
  I: Integer;
begin
  Result := '';
  // 日志落到 %TEMP%\Setup Log*.txt —— 没有 Windows 开发机, 只能靠它事后定位
  Log('HQuant 安装前钩子进入');
  if not IsAppRunning() then
  begin
    Log('HQuant 未运行, 跳过关闭步骤');
    Exit;
  end;

  Cmd := ExpandConstant('{cmd}');
  // 1) 请求正常关闭: 不加 /F, 应用收到窗口关闭会走正常退出流程 (停后端、落盘)。
  //    强行 /F 可能打断正在写的 parquet, 所以先给一次机会。
  Exec(Cmd, '/C taskkill /IM {#MyAppExeName} /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log(Format('软关闭返回码 %d', [ResultCode]));
  if ResultCode = 0 then
    Sleep(4000);

  // 2) 无条件强制结束, 失败就重试 —— 不看上一步返回码
  for I := 1 to 5 do
  begin
    if not IsAppRunning() then
      Exit;
    Exec(Cmd, '/C taskkill /F /IM {#MyAppExeName} /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Log(Format('强制结束第 %d 次, 返回码 %d', [I, ResultCode]));
    Sleep(1000);
  end;

  // 3) 仍在运行 → 中止安装并说明原因 (常见: 应用以管理员身份启动, 安装器没有权限关闭)
  if IsAppRunning() then
  begin
    Log('HQuant 强制结束失败, 中止安装');
    Result := 'H-Quant 仍在运行，无法完成安装。' + #13#10 + #13#10 +
              '请先手动退出 H-Quant，然后重试安装。' + #13#10 +
              '如果它是以管理员身份启动的，本次安装没有权限关闭它。';
  end;
end;

// ── 辅助函数: 判断目录是否为空 ─────────────────────────────────
// Inno Setup 内置无 IsDirEmpty, 用 FindFirst/FindNext 自行实现。
// 用于卸载后清理空的 {app} 壳目录。
function IsDirEmpty(const Dir: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Dir) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          Result := False;
          Break;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

// ── 启动时: 若 D 盘不存在, 回退默认路径到用户目录 ───────────────
// 避免默认 D:\... 但系统没 D 盘时向导显示无效路径
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard();
var
  DefaultDir: String;
begin
  // D 盘存在 → 用 D 盘; 否则回退用户目录 (无需管理员权限)
  if not DirExists('D:\') then
  begin
    DefaultDir := ExpandConstant('{localappdata}\Programs\H-Quant');
    WizardForm.DirEdit.Text := DefaultDir;
  end;
end;

// ── 安装完成后: 通知外壳刷新图标缓存 ─────────────────────────────
// 覆盖安装时 exe 路径不变 (如 D:\H-Quant\HQuant.exe), 资源管理器会一直显示缓存的旧图标
// (首个版本为透明底)。用 SHCNE_ASSOCCHANGED 强制外壳重建图标缓存, 装完快捷方式
// 立即显示当前 exe 内嵌图标, 无需用户重启资源管理器。
// 此前用 ie4uinit -show 实现同一目的, 但 ie4uinit.exe 是 IE 组件, 在移除 IE 的
// 系统 (Win11 24H2+ 等) 上不存在 —— 执行会报错, 只能加 Check 跳过, 结果那些
// 机器永远刷不掉旧图标。SHChangeNotify 是 ie4uinit -show 底层调用的同一外壳 API,
// 所有 Windows 都有, 无外部依赖。
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $0000;

procedure SHChangeNotify(wEventId, uFlags, dwItem1, dwItem2: Longint);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
end;

// ── 卸载时询问是否删除用户数据 ─────────────────────────────────
// 用户数据在 {app}\data\ (策略/回测/监控/行情), 与程序同处 {app} 总目录。
// Inno Setup 卸载默认只删它装过的程序文件, data\ 会被保留 (覆盖安装/常规卸载都不丢)。
// 这里仅在用户明确「彻底卸载」时, 才询问是否清理 data\ + {app} 空壳。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, AppDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // {app}\data = 用户数据目录 (与程序同总目录, 子文件夹)
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) then
    begin
      if SuppressibleMsgBox(
          '是否同时删除用户数据?' + #13#10 + #13#10 +
          '位置: ' + DataDir + #13#10 +
          '内容: 行情数据、策略、策略结果、回测记录、监控规则等' + #13#10 + #13#10 +
          '选「是」彻底卸载, 选「否」保留数据(重装后可恢复)。',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
    // 清理可能残留的空 {app} 壳目录 (程序文件已被 Inno Setup 删除)
    AppDir := ExpandConstant('{app}');
    if DirExists(AppDir) and IsDirEmpty(AppDir) then
    begin
      DelTree(AppDir, True, True, True);
    end;
  end;
end;
