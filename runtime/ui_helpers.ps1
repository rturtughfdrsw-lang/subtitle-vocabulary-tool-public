function Format-Elapsed([double]$Seconds) {
    $total = [Math]::Max(0, [Math]::Floor($Seconds))
    $hours = [Math]::Floor($total / 3600)
    $minutes = [Math]::Floor(($total % 3600) / 60)
    $remaining = [Math]::Floor($total % 60)
    return '{0:00}:{1:00}:{2:00}' -f $hours,$minutes,$remaining
}

function Format-PhaseTimings($PhaseTimings) {
    if ($null -eq $PhaseTimings) { return '' }
    $parts = New-Object System.Collections.Generic.List[string]
    if ($PhaseTimings -is [Collections.IDictionary]) {
        foreach ($key in $PhaseTimings.Keys) {
            $parts.Add(([string]$key + ' ' + (Format-Elapsed ([double]$PhaseTimings[$key]))))
        }
    } else {
        foreach ($property in $PhaseTimings.PSObject.Properties) {
            $parts.Add(($property.Name + ' ' + (Format-Elapsed ([double]$property.Value))))
        }
    }
    return $parts -join '；'
}

if (-not ('TaskbarFlash.NativeMethods' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace TaskbarFlash {
    [StructLayout(LayoutKind.Sequential)]
    public struct FlashInfo {
        public UInt32 cbSize;
        public IntPtr hwnd;
        public UInt32 dwFlags;
        public UInt32 uCount;
        public UInt32 dwTimeout;
    }

    public static class NativeMethods {
        [DllImport("user32.dll")]
        public static extern bool FlashWindowEx(ref FlashInfo info);
    }
}
"@
}

function Flash-Taskbar($Form) {
    if ($null -eq $Form -or $Form.IsDisposed) { return }
    $info = New-Object TaskbarFlash.FlashInfo
    $info.cbSize = [Runtime.InteropServices.Marshal]::SizeOf([type][TaskbarFlash.FlashInfo])
    $info.hwnd = $Form.Handle
    $info.dwFlags = 15
    $info.uCount = 5
    $info.dwTimeout = 0
    [void][TaskbarFlash.NativeMethods]::FlashWindowEx([ref]$info)
}

function Get-WorkerMode([int]$SelectedIndex) {
    switch ($SelectedIndex) {
        1 { return 'audio' }
        2 { return 'video' }
        3 { return 'fast' }
        default { return 'smart' }
    }
}

function Get-ModeItems {
    return @(
        '智能字幕提取',
        '仅提取 MP3 音频',
        '下载 MP4 视频',
        '极速字幕（语音识别）'
    )
}
