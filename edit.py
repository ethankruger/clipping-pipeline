"""Validated edit decisions -> portrait MP4. Coordinates are fractions of the source."""
import json
import math
import os
import re
import subprocess
from pathlib import Path


def run(args, timeout=1200, **kw):
    return subprocess.run(args, check=True, timeout=timeout, capture_output=True, **kw)


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]).stdout)


def clamp_number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid '+label)
    return value


def rect(box):
    if not isinstance(box,list) or len(box)!=4:
        raise ValueError('Need [x,y,width,height]')
    x,y,w,h=[clamp_number(n,0,1,'crop') for n in box]
    if min(w,h)<0.08 or x+w>1.0001 or y+h>1.0001:
        raise ValueError('Crop exceeds frame')
    return f'crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:iw*{x}:ih*{y}'


def safe_text(s, limit=140):
    if not isinstance(s,str) or len(s)>limit:
        raise ValueError('Invalid text')
    return ' '.join(s.replace('\\','').replace('{','').replace('}','').split())



_PROFANITY = re.compile(
    r'\b(fuck(?:ing|ed|er|s)?|shit(?:ty|ting|s)?|bitch(?:es|ing)?|cock(?:s)?|dick(?:s)?|pussy|cunt(?:s)?|asshole(?:s)?|nigga(?:s)?|nigger(?:s)?|faggot(?:s)?|retard(?:ed|s)?)\b',
    re.I,
)


def _mask_ass(text):
    parts = re.split(r'(\{[^}]*\})', text)
    plain = ''.join(x for x in parts if not x.startswith('{'))
    letters = [ch for ch in plain if ch.isalpha()]
    all_caps = len(letters) >= 6 and all(ch.isupper() for ch in letters)
    out = []
    first_chunk = True
    for part in parts:
        if part.startswith('{'):
            out.append(part)
            continue
        s = _PROFANITY.sub(lambda m: m.group(0)[0] + '*' * (len(m.group(0)) - 1), part)
        if all_caps and s.strip():
            s = s.lower()
            if first_chunk:
                s = re.sub(r'([a-z])', lambda m: m.group(1).upper(), s, count=1)
                first_chunk = False
        out.append(s)
    return ''.join(out)


def ass_time(s):
    cs=round(s*100)
    return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'



def _twemoji_png(ch, cache_dir):
    import urllib.request
    code = '-'.join(f'{ord(c):x}' for c in ch if ord(c) != 0xFE0F)
    path = cache_dir / f'{code}.png'
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = f'https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{code}.png'
        urllib.request.urlretrieve(url, path)
    return path


def _render_hook_png(text, out_path, font_size=64, max_width=876, top_y=340):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_file = next((f for f in (r'C:\Windows\Fonts\arialbd.ttf', r'C:\Windows\Fonts\segoeuib.ttf') if Path(f).exists()), None)
    font = ImageFont.truetype(font_file, font_size) if font_file else ImageFont.load_default()
    cache_dir = Path('state') / 'emoji_cache'

    def is_emoji(ch):
        return ord(ch) > 0x2600

    # Split text into word/emoji tokens
    tokens = []
    for word in text.split(' '):
        buf = ''
        for ch in word:
            if ord(ch) == 0xFE0F:
                continue
            if is_emoji(ch):
                if buf:
                    tokens.append(('t', buf))
                    buf = ''
                tokens.append(('e', ch))
            else:
                buf += ch
        if buf:
            tokens.append(('t', buf))
        tokens.append(('s', ' '))
    if tokens and tokens[-1][0] == 's':
        tokens.pop()

    def tok_w(kind, val):
        if kind == 'e':
            return int(font_size * 1.05)
        return int(draw.textlength(val, font=font))

    # Word-wrap into lines
    lines, cur, cur_w = [], [], 0
    for kind, val in tokens:
        w = tok_w(kind, val)
        if cur and kind != 's' and cur_w + w > max_width:
            if cur and cur[-1][0] == 's':
                cur.pop()
            lines.append(cur)
            cur, cur_w = [(kind, val)], w
        else:
            if not cur and kind == 's':
                continue
            cur.append((kind, val))
            cur_w += w
    if cur:
        if cur[-1][0] == 's':
            cur.pop()
        lines.append(cur)

    line_h = int(font_size * 1.25)
    y = top_y
    for row in lines:
        total_w = sum(tok_w(k, v) for k, v in row)
        x = (1080 - total_w) // 2
        for kind, val in row:
            w = tok_w(kind, val)
            if kind == 'e':
                try:
                    ep = _twemoji_png(val, cache_dir)
                    eimg = Image.open(ep).convert('RGBA').resize((font_size, font_size), Image.LANCZOS)
                    img.alpha_composite(eimg, (x, y + 4))
                except Exception:
                    pass
            else:
                draw.text((x, y), val, font=font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(20, 20, 20, 255))
            x += w
        y += line_h
    img.save(out_path)
    return out_path


def render(src, dest, ed, rule, font, *, word_captions=None):
    info=probe(src)
    duration=float(info['format']['duration'])
    start=clamp_number(ed['start'],0,duration,'start')
    end=clamp_number(ed['end'],start+0.5,duration+0.15,'end')
    length=min(end,duration)-start
    layout=ed['layout']
    if layout=='wide':
        graph='[0:v]split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=32:2,eq=brightness=-0.18[b];[fg]scale=1080:-2[f];[b][f]overlay=(W-w)/2:(H-h)/2'
    elif layout=='portrait':
        graph='[0:v]'+rect(ed['box'])+',scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920'
    elif layout=='stack':
        def panel(key):
            return rect(ed[key])+',scale=1080:960:force_original_aspect_ratio=decrease,pad=1080:960:(ow-iw)/2:(oh-ih)/2'
        graph='[0:v]split[a][b];[a]'+panel('box')+'[p];[b]'+panel('box2')+'[q];[p][q]vstack'
    else:
        raise ValueError('Unknown layout')
    if not re.fullmatch(r'[\w .-]{1,60}',font): raise ValueError('Invalid font')
    header='[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
    for name,size,pos,margin in [('Speech',66,2,355),('Hook',64,8,340),('Mark',34,8,92)]:
        header+=f'Style: {name},{font},{size},&H00FFFFFF,&H0000FFFF,&H00141414,&H90000000,-1,0,0,0,100,100,0,0,1,4,1,{pos},84,120,{margin},1\n'
    header+='[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'
    lines=[]
    def line(a,b,style,txt):
        if txt: lines.append(f'Dialogue: 0,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{txt}')
    hook_txt = safe_text(ed.get('hook',''),90)
    hook_png = None
    if hook_txt:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            hook_png = _render_hook_png(hook_txt, dest.with_suffix('.hook.png'))
        except Exception as err:
            print(f'Color hook PNG fallback ({err}); using ASS hook.', flush=True)
            line(0,length,'Hook',hook_txt)
    line(0,length,'Mark',safe_text(rule.get('overlay',''),90))
    timed = []
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
        line(a,b,'Speech',_mask_ass(text))
    prior=0
    for item in ([] if timed else ed['captions']):
        a=clamp_number(item['s'],prior,length,'caption start')
        b=clamp_number(item['e'],a,length+0.15,'caption end')
        line(a,min(b,length),'Speech',safe_text(item['text'],104))
        prior=b
    dest.parent.mkdir(parents=True,exist_ok=True)
    ass=dest.with_suffix('.ass')
    ass.write_text(header+'\n'.join(lines)+'\n',encoding='utf-8-sig')
    temp=dest.with_suffix('.tmp.mp4')
    extra=[]
    next_in=1
    if hook_png:
        graph+=f'[hbase];[{next_in}:v]format=rgba[hk];[hbase][hk]overlay=0:0'
        extra+=['-i',str(hook_png.resolve())]
        next_in+=1
    if rule.get('watermark_file'):
        logo=Path(rule['watermark_file']).resolve(strict=True)
        graph+=f'[base];[{next_in}:v]scale=150:-1[logo];[base][logo]overlay=45:75'
        extra+=['-i',str(logo)]
    args=['ffmpeg','-v','error','-nostdin','-y','-ss',str(start),'-i',str(src.resolve()),*extra,'-t',str(length),
          '-filter_complex',graph+',setsar=1,subtitles='+ass.name+'[out]','-map','[out]','-map','0:a:0',
          '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',temp.name]
    run(args,cwd=dest.parent)
    v=probe(temp)
    if not any(s.get('codec_name')=='h264' and (s.get('width'),s.get('height'))==(1080,1920) for s in v['streams']) or not any(s.get('codec_name')=='aac' for s in v['streams']):
        raise ValueError('Invalid master streams')
    run(['ffmpeg','-v','error','-xerror','-i',str(temp),'-f','null','-'])
    temp.replace(dest)
