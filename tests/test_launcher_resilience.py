from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "runtime" / "launcher.ps1"
UI_HELPERS = LAUNCHER.with_name("ui_helpers.ps1")


def test_launcher_has_cancel_cache_and_close_protection():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "$cancel.Text = '取消任务'" in source
    assert "$cleanup.Text = '清理缓存'" in source
    assert "cancel.request" in source
    assert "cache_maintenance.py" in source
    assert "$form.Add_FormClosing" in source
    assert "$script:CloseAfterCancel" in source


def test_launcher_handles_cancelled_and_missing_status():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "$resultState.cancelled" in source
    assert "任务已取消，未保留结果文件夹" in source
    assert "后台异常退出" in source
    assert "$resultState.log" in source
    assert "function Remove-UnexpectedTaskFolder" in source


def test_launcher_exposes_full_video_download_mode():
    command = (
        f". '{UI_HELPERS}'; "
        "$items=Get-ModeItems; "
        "Write-Output $items[2]; Write-Output (Get-WorkerMode 2); "
        "Write-Output $items[3]; Write-Output (Get-WorkerMode 3)"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "下载 MP4 视频",
        "video",
        "极速字幕（语音识别）",
        "fast",
    ]


def test_launcher_success_message_matches_result_type():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "MP4 视频下载完成" in source
    assert "MP3 音频提取完成" in source
    assert "字幕提取完成" in source
    assert "字幕处理完成，结果已单独保存。" not in source


def test_launcher_completion_alert_flashes_and_can_open_result_immediately():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "Flash-Taskbar $form" in source
    assert "Format-PhaseTimings $resultState.phase_timings" in source
    assert "[Windows.Forms.MessageBoxButtons]::YesNo" in source
    assert "[Windows.Forms.MessageBoxIcon]::Information" in source
    assert "Start-Process explorer.exe $script:LastSuccessFolder" in source


def test_launcher_checks_external_ffmpeg_and_result_write_access():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "$MediaToolsResolver" in source
    assert "& $Python $MediaToolsResolver --json" in source
    assert "未找到 FFmpeg。请安装 FFmpeg 并加入 PATH。" in source
    missing_line = next(line for line in source.splitlines() if "$missing = @(" in line)
    assert "$Ffmpeg" not in missing_line
    assert "$Ffprobe" not in missing_line
    assert "$Resolver" in source
    assert ".write-test-" in source
    assert "结果文件夹没有写入权限" in source


def test_launcher_cleans_folder_if_worker_cannot_start():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "后台程序启动失败" in source
    assert "Remove-Item -LiteralPath $folder -Recurse -Force" in source


def test_launcher_powershell_syntax_is_valid():
    command = (
        "$source=[IO.File]::ReadAllText("
        f"'{str(LAUNCHER).replace(chr(39), chr(39) * 2)}',"
        "[Text.Encoding]::UTF8);"
        "[ScriptBlock]::Create($source)|Out-Null;"
        "Write-Output 'PASS'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


if __name__ == "__main__":
    test_launcher_has_cancel_cache_and_close_protection()
    test_launcher_handles_cancelled_and_missing_status()
    test_launcher_exposes_full_video_download_mode()
    test_launcher_success_message_matches_result_type()
    test_launcher_completion_alert_flashes_and_can_open_result_immediately()
    test_launcher_checks_external_ffmpeg_and_result_write_access()
    test_launcher_cleans_folder_if_worker_cannot_start()
    test_launcher_powershell_syntax_is_valid()
    print("PASS: launcher resilience tests")
