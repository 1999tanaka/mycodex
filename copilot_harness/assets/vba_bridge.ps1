# Excel VBA 連携 (PowerShell + Excel COM。追加ソフト不要)
#
#   export : ブックの VBA モジュールを Src フォルダへ書き出す (.bas / .cls / .frm)
#   build  : Src のモジュールをブックへ取り込んで保存する (取り込み前にブックをバックアップ)
#   test   : テスト用マクロ (Function) を実行し、戻り値の TEST:<NAME>:<PASS|FAIL> 行を出力する
#
# 前提: デスクトップ版 Excel (ライセンス版) と、トラストセンターの
#       「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」が ON であること。
# Harness からは builtin:vba-build / builtin:vba-test / python harness.py vba-export で呼ばれる。

param(
    [Parameter(Mandatory = $true)][ValidateSet('export', 'build', 'test')][string]$Action,
    [Parameter(Mandatory = $true)][string]$Workbook,
    [string]$Src = 'src\vba',
    [string]$Macro = 'HarnessTests.RunAll',
    [string]$BackupDir = '',
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sjis = [Text.Encoding]::GetEncoding(932)
$TypeExt = @{ 1 = '.bas'; 2 = '.cls'; 3 = '.frm'; 100 = '.cls' }   # 標準 / クラス / フォーム / シート・ブック

function Fail([string]$msg) {
    Write-Output "error: $msg"
    exit 1
}

# Excel の PID を取得し、時間切れで強制終了する見張り (ダイアログ等で COM 呼び出しが戻らない場合の保険)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class HarnessExcelWatchdog {
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    static System.Threading.Timer timer;
    public static int PidOf(int hwnd) { uint pid; GetWindowThreadProcessId(new IntPtr(hwnd), out pid); return (int)pid; }
    public static void Arm(int pid, int ms) {
        timer = new System.Threading.Timer(_ => {
            try { System.Diagnostics.Process.GetProcessById(pid).Kill(); } catch { }
        }, null, ms, System.Threading.Timeout.Infinite);
    }
    public static void Disarm() { if (timer != null) { timer.Dispose(); timer = null; } }
}
'@

# ------------------------------------------------------------ 文字コード変換 (取り込み用に Shift-JIS + CRLF へ)
function ConvertTo-ImportFile([IO.FileInfo]$file, [string]$tmpDir) {
    $bytes = [IO.File]::ReadAllBytes($file.FullName)
    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
    try {
        $text = $strictUtf8.GetString($bytes)
        if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    } catch {
        $text = $sjis.GetString($bytes)   # UTF-8 として不正なら Shift-JIS (VBE の書き出し形式)
    }
    $text = ($text -replace "`r`n", "`n") -replace "`n", "`r`n"
    $name = [IO.Path]::GetFileNameWithoutExtension($file.Name)
    if ($text -notmatch '(?m)^Attribute VB_Name = ') {
        $text = "Attribute VB_Name = `"$name`"`r`n" + $text
    }
    if ($file.Extension -ieq '.cls' -and $text -notmatch '^VERSION 1\.0 CLASS') {
        $text = "VERSION 1.0 CLASS`r`nBEGIN`r`n  MultiUse = -1  'True`r`nEND`r`n" + $text
    }
    $out = Join-Path $tmpDir $file.Name
    [IO.File]::WriteAllBytes($out, $sjis.GetBytes($text))
    if ($file.Extension -ieq '.frm') {
        $frx = [IO.Path]::ChangeExtension($file.FullName, '.frx')
        if (Test-Path $frx) { Copy-Item $frx $tmpDir }
    }
    return $out
}

# シート / ThisWorkbook のモジュールは取り込めないため、ヘッダを除いたコード本体だけを取り出す
function Get-CodeBody([string]$path) {
    $lines = [IO.File]::ReadAllLines($path, $sjis)
    $i = 0
    if ($lines.Count -gt 0 -and $lines[0] -match '^VERSION ') {
        $i = 1
        if ($i -lt $lines.Count -and $lines[$i] -match '^BEGIN') {
            while ($i -lt $lines.Count -and $lines[$i] -notmatch '^END\s*$') { $i++ }
            $i++
        }
    }
    while ($i -lt $lines.Count -and $lines[$i] -match '^Attribute VB_') { $i++ }
    if ($i -ge $lines.Count) { return '' }
    return ($lines[$i..($lines.Count - 1)] -join "`r`n")
}

function Get-Component($proj, [string]$name) {
    foreach ($c in $proj.VBComponents) { if ($c.Name -eq $name) { return $c } }
    return $null
}

# ------------------------------------------------------------ 本体
if (-not (Test-Path $Workbook)) { Fail "ブックが見つかりません: $Workbook" }
$full = (Resolve-Path $Workbook).Path

if ($Action -eq 'build' -and $BackupDir) {
    New-Item -ItemType Directory -Force $BackupDir | Out-Null
    $bak = Join-Path $BackupDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '_' + (Split-Path $full -Leaf))
    Copy-Item $full $bak
    Write-Output "backup: $bak"
}

$excel = $null
$wb = $null
$excelPid = 0
$tmpDir = $null
$code = 0
try {
    try {
        $excel = New-Object -ComObject Excel.Application
    } catch {
        Fail "Excel を起動できません (デスクトップ版 Excel が必要です): $($_.Exception.Message)"
    }
    $excelPid = [HarnessExcelWatchdog]::PidOf([int]$excel.Hwnd)
    [HarnessExcelWatchdog]::Arm($excelPid, $TimeoutSec * 1000)
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $false        # Workbook_Open などのイベントを実行しない
    $excel.AutomationSecurity = 1       # msoAutomationSecurityLow: このブックのマクロを実行可能にする

    $readOnly = ($Action -ne 'build')
    $wb = $excel.Workbooks.Open($full, 0, $readOnly)
    if ($Action -eq 'build' -and $wb.ReadOnly) {
        Fail "ブックを書き込みで開けません。Excel で開いている場合は閉じてから実行してください: $full"
    }

    if ($Action -ne 'test') {
        try {
            $proj = $wb.VBProject
            $null = $proj.VBComponents.Count
        } catch {
            Fail ("VBA プロジェクトにアクセスできません。Excel の [ファイル] → [オプション] → [トラスト センター] → " +
                  "[トラスト センターの設定] → [マクロの設定] で「VBA プロジェクト オブジェクト モデルへのアクセスを信頼する」を ON に" +
                  "してください (プロジェクトにパスワード保護がある場合も失敗します)。")
        }
    }

    switch ($Action) {
        'export' {
            New-Item -ItemType Directory -Force $Src | Out-Null
            foreach ($c in $proj.VBComponents) {
                $ext = $TypeExt[[int]$c.Type]
                if (-not $ext) { continue }
                if ([int]$c.Type -eq 100 -and $c.CodeModule.CountOfLines -eq 0) { continue }   # 空のシートモジュール
                $c.Export((Join-Path $Src ($c.Name + $ext)))
                Write-Output "exported: $($c.Name)$ext"
            }
        }
        'build' {
            if (-not (Test-Path $Src)) { Fail "モジュールのフォルダがありません: $Src (先に python harness.py vba-export)" }
            $tmpDir = Join-Path ([IO.Path]::GetTempPath()) ('harness_vba_' + [Guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory $tmpDir | Out-Null
            $files = Get-ChildItem $Src -File | Where-Object { $_.Extension -in '.bas', '.cls', '.frm' }
            foreach ($f in $files) {
                $name = [IO.Path]::GetFileNameWithoutExtension($f.Name)
                $tmp = ConvertTo-ImportFile $f $tmpDir
                $existing = Get-Component $proj $name
                if ($existing -and [int]$existing.Type -eq 100) {
                    $cm = $existing.CodeModule
                    if ($cm.CountOfLines -gt 0) { $cm.DeleteLines(1, $cm.CountOfLines) }
                    $body = Get-CodeBody $tmp
                    if ($body.Trim()) { $cm.AddFromString($body) }
                } else {
                    if ($existing) {
                        $existing.Name = ($name + '_harness_old')   # 同名で再取り込みできるよう先に改名
                        $proj.VBComponents.Remove($existing)
                    }
                    $null = $proj.VBComponents.Import($tmp)
                }
                Write-Output "imported: $($f.Name)"
            }
            $wb.Save()
            Write-Output "saved: $full"
        }
        'test' {
            $target = "'" + $wb.Name + "'!" + $Macro
            try {
                $result = $excel.Run($target)
            } catch {
                Fail "テスト用マクロを実行できません ($Macro): $($_.Exception.Message)"
            }
            if ($null -eq $result -or "$result" -eq '') {
                Fail "テスト用マクロの戻り値が空です。$Macro は TEST:<NAME>:<PASS|FAIL> 行を返す Function にしてください。"
            }
            Write-Output ("$result" -replace "`r`n", "`n")
        }
    }
} catch {
    $msg = $_.Exception.Message
    if ($excelPid -and -not (Get-Process -Id $excelPid -ErrorAction SilentlyContinue)) {
        $msg = "Excel が応答しなくなったため終了しました ($TimeoutSec 秒)。VBA のコンパイルエラーや MsgBox などで停止した可能性があります: $msg"
    }
    Write-Output "error: $msg"
    $code = 1
} finally {
    [HarnessExcelWatchdog]::Disarm()
    if ($wb) { try { $wb.Close($false) } catch { } }
    if ($excel) { try { $excel.Quit() } catch { } }
    foreach ($o in @($wb, $excel)) {
        if ($o) { try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($o) } catch { } }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if ($excelPid) {
        Start-Sleep -Milliseconds 500
        Stop-Process -Id $excelPid -Force -ErrorAction SilentlyContinue   # この処理で起動した Excel のみ
    }
    if ($tmpDir -and (Test-Path $tmpDir)) { Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue }
}
exit $code
