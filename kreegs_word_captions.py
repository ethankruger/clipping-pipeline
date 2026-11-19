"""Install and preview local word-timed captions in the Kreegs Windows worker."""
import argparse
import json
import math
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

ACTIVE = r'{\1c&H00FFFF&}'
WHITE = r'{\1c&HFFFFFF&}'


@lru_cache(maxsize=1)
def whisper_model():
    from faster_whisper import WhisperModel

    return WhisperModel('small.en', device='cpu', compute_type='int8')


def words_in_cut(source, start, duration):
    """Extract the same audio range FFmpeg will render; timings start at zero."""
    with TemporaryDirectory(prefix='kreegs-speech-') as temp:
        wav = Path(temp) / 'speech.wav'
        subprocess.run(
            ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(start),
             '-i', str(source.resolve()), '-t', str(duration), '-map', '0:a:0',
             '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)],
            check=True, capture_output=True, timeout=120,
        )
        segments, _ = whisper_model().transcribe(
            str(wav), language='en', beam_size=3, word_timestamps=True,
            vad_filter=True, condition_on_previous_text=False,
        )
        return [
            (word.start, word.end, word.word)
            for segment in segments
            for word in (segment.words or [])
        ]


def timed_phrases(words, duration):
    """Group nearby words and align the periods to ASS's centisecond clock."""
    limit = int(duration * 100)
    normalized = []
    last_end = 0
    for start, end, word in words:
        if start is None or end is None or not math.isfinite(start) or not math.isfinite(end):
            continue
        clean = ' '.join(word.replace('\\', '').replace('{', '').replace('}', '').split())
        start_cs = max(last_end, round(start * 100))
        end_cs = min(limit, round(end * 100))
        if not clean or end_cs <= start_cs:
            continue
        normalized.append((start_cs, end_cs, clean))
        last_end = end_cs

    phrase = []
    for item in normalized:
        if phrase:
            previous = phrase[-1]
            proposed = ' '.join(word[2] for word in [*phrase, item])
            if (
                len(phrase) >= 5
                or len(proposed) > 27
                or item[0] - previous[1] >= 38
                or item[1] - phrase[0][0] > 200
                or previous[2].endswith(('.', '?', '!'))
            ):
                yield phrase
                phrase = []
        phrase.append(item)
    if phrase:
        yield phrase


def highlight_lines(words, duration):
    """Keep the full phrase in place while the spoken word changes color."""
    events = []
    for phrase in timed_phrases(words, duration):
        words_in_phrase = [word[2] for word in phrase]
        plain = ' '.join(words_in_phrase)
        previous_end = phrase[0][0]
        for index, (start, end, word) in enumerate(phrase):
            if start > previous_end:
                events.append((previous_end / 100, start / 100, plain))
            colored = list(words_in_phrase)
            colored[index] = ACTIVE + word + WHITE
            events.append((start / 100, end / 100, ' '.join(colored)))
            previous_end = end
    return events


def highlighted_speech(source, start, duration):
    return highlight_lines(words_in_cut(source, start, duration), duration)


NEW_RENDER = 'def render(src, dest, ed, rule, font, *, word_captions=None):'
NEW_SPEECH = '''    timed = []
    if word_captions is None:
        enabled = os.environ.get('KREEGS_WORD_CAPTIONS', '0') == '1'
    else:
        enabled = word_captions
    # An empty editor caption list can mean that the source is already captioned.
    if enabled and ed['captions']:
        try:
            from kreegs_word_captions import highlighted_speech

            timed = highlighted_speech(src, start, length)
            if not timed:
                raise ValueError('Whisper returned no usable spoken words for this cut.')
        except Exception as error:
            if word_captions is True:
                raise
            print(f'Word captions unavailable ({type(error).__name__}); using editor captions.', flush=True)
    for a, b, text in timed:
        line(a,b,'Speech',text)
    prior=0
    for item in ([] if timed else ed['captions']):
'''


def install(root):
    if Path(__file__).resolve() != root / 'kreegs_word_captions.py':
        raise ValueError('Save this file as kreegs_word_captions.py in the project folder first.')
    path = root / 'edit.py'
    original = path.read_text(encoding='utf-8-sig')
    if NEW_RENDER in original and 'from kreegs_word_captions import highlighted_speech' in original:
        print('Word caption support is already installed.')
        return
    pairs = [
        ('def render(src, dest, ed, rule, font):', NEW_RENDER),
        ("    prior=0\n    for item in ed['captions']:\n", NEW_SPEECH),
    ]
    if not re.search(r'^import os(?:\s|$)', original, re.MULTILINE):
        pairs.insert(0, ('import math\n', 'import math\nimport os\n'))
    patched = original
    for old, new in pairs:
        if patched.count(old) != 1:
            raise ValueError('This edit.py differs from the Windows bundle. No change made.')
        patched = patched.replace(old, new, 1)
    compile(patched, str(path), 'exec')
    backup = path.with_name('edit.py.before-word-captions')
    if not backup.exists():
        copy2(path, backup)
    pending = path.with_name('edit.py.word-captions-tmp')
    pending.write_text(patched, encoding='utf-8')
    pending.replace(path)
    print('Installed word caption support. Original saved as edit.py.before-word-captions.')


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def preview(root, job):
    from edit import render

    batches = root / 'state' / 'batches'
    if job:
        if not re.fullmatch(r'test-\d{4}-\d{2}-\d{2}', job):
            raise ValueError('Use a no-posts test ID, e.g. test-2026-11-18.')
        manifests = [batches / job / 'manifest.json']
    else:
        manifests = sorted(
            batches.glob('test-*/manifest.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    for manifest in manifests:
        rows = load_json(manifest)
        if not rows:
            continue
        folder = manifest.parent
        for row in rows:
            edit = row['edit']
            if not edit.get('captions'):
                continue
            clip_id = row['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', clip_id):
                continue
            source = folder / 'sources' / f'{clip_id}.mp4'
            if not source.is_file():
                continue
            dest = folder / 'caption-previews' / f'{clip_id}-word-captions.mp4'
            font = load_json(root / 'config.json')['font']
            print(f'Building word captions for {clip_id}. The first run downloads the speech model.', flush=True)
            render(source, dest, edit, row['rule'], font, word_captions=True)
            print(f'Preview: {dest}')
            print(f'Original: {folder / "masters" / (clip_id + ".mp4")}')
            return
    raise ValueError('No completed test clip has both a source MP4 and an editor caption track.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'preview'])
    parser.add_argument('--job', help='Preview a particular test batch instead of the latest one.')
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        if args.command == 'install':
            install(root)
        else:
            preview(root, args.job)
    except (OSError, ValueError, RuntimeError, ImportError, SyntaxError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
