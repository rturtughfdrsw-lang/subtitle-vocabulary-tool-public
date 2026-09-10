import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

tool_dir = Path(__file__).resolve().parent
if str(tool_dir) not in sys.path:
    sys.path.insert(0, str(tool_dir))

from ocr_dialogue_filter import clean_transcript_text
from transcription_profiles import get_transcription_profile

os.environ.setdefault('HF_HOME', str(tool_dir / 'models'))

try:
    from faster_whisper import WhisperModel
except ImportError:
    local_packages = Path(sys.executable).parent / 'Lib' / 'site-packages'
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'install', '--upgrade',
        '--target', str(local_packages), 'faster-whisper'
    ])
    importlib.invalidate_caches()
    from faster_whisper import WhisperModel

def progress(progress_path, value, message):
    temp = progress_path.with_suffix('.tmp')
    temp.write_text(json.dumps({'value': int(value), 'message': message}, ensure_ascii=False), encoding='utf-8')
    temp.replace(progress_path)

def stamp(seconds):
    value = round(seconds * 1000)
    hours, value = divmod(value, 3600000)
    minutes, value = divmod(value, 60000)
    secs, ms = divmod(value, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{ms:03}'

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        raise RuntimeError('语音识别启动参数不足')
    audio, output = Path(args[0]), Path(args[1])
    progress_path = Path(args[2]) if len(args) > 2 else output / 'progress.json'
    profile_name = args[3] if len(args) > 3 else 'standard'
    profile = get_transcription_profile(profile_name)

    progress(progress_path, 45, f'正在加载语音识别模型（{profile.model_name}）…')
    threads = max(2, (os.cpu_count() or 4) - 1)
    model = WhisperModel(
        profile.model_name,
        device=profile.device,
        compute_type=profile.compute_type,
        cpu_threads=threads,
        num_workers=1,
        local_files_only=True,
    )
    segments, info = model.transcribe(
        str(audio),
        vad_filter=True,
        beam_size=profile.beam_size,
        best_of=profile.best_of,
        condition_on_previous_text=False,
    )

    number = 0
    promotions_removed = 0
    with (output / '语音识别字幕.srt').open('w', encoding='utf-8-sig') as srt_handle, \
         (output / '语音识别文字.txt').open('w', encoding='utf-8-sig') as txt_handle:
        for segment in segments:
            text, changed = clean_transcript_text(segment.text.strip())
            if changed:
                promotions_removed += 1
            if text:
                number += 1
                srt_handle.write(
                    f'{number}\n{stamp(segment.start)} --> {stamp(segment.end)}\n{text}\n\n'
                )
                txt_handle.write(text + '\n')
                srt_handle.flush()
                txt_handle.flush()
                print(f'[{stamp(segment.start)}] {text}', flush=True)
            ratio = min(1.0, segment.end / max(info.duration, 1.0))
            progress(
                progress_path,
                46 + ratio * 52,
                f'正在识别字幕… {int(ratio * 100)}%',
            )
    if promotions_removed:
        print(f'语音识别广告净化：{promotions_removed} 段', flush=True)
    progress(progress_path, 98, '识别完成，正在整理文件…')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
