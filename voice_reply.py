"""Plain speech formatting for /api/assistant, without another model call."""

import re
import unicodedata


def format_voice_reply(reply):
    text = str(reply or "").strip()
    # IR success confirms dispatch, never the device's unobservable power state.
    text = re.sub(
        r'^✅\s*(.+?)「([^」]+)」指令已送出\s*$',
        lambda m: f"已送出{m[1]}的{'電源' if m[2] in ('開', '關', '電源') else m[2]}指令。",
        text, flags=re.MULTILINE,
    )
    text = re.sub(r'!?\[([^\]]+)\]\(https?://[^\s)]+\)', r'\1', text)
    text = re.sub(r'https?://[^\s\u3000-\u9fff，。！？；「」（）]+', '已省略網址', text)
    text = text.replace('℃', '度').replace('°C', '度').replace('°F', '華氏度').replace('°', '度')
    text = re.sub(r'(-?\d+(?:\.\d+)?)\s*[%％]', r'百分之\1', text)
    text = re.sub(r'(\d{4})[/／](\d{1,2})[/／](\d{1,2})', r'\1年\2月\3日', text)
    text = re.sub(r'(?<=\d)\s*[~～〜]\s*(?=-?\d)', '至', text)
    # Preserve numerical fractions/ratios instead of inventing date or unit semantics.
    text = re.sub(r'(?<!\d)[/／]|[/／](?!\d)', '、', text)
    for symbol, spoken in [('≤', '小於等於'), ('≥', '大於等於'), ('<', '小於'), ('>', '大於')]:
        text = text.replace(symbol, spoken)
    text = re.sub(r'^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)', '', text, flags=re.MULTILINE)
    text = re.sub(r'[*`#~]', '', text)
    # So/Sk cover pictographs, arrows, dingbats and emoji skin tones. Preserve
    # numbers, punctuation and mathematical signs (notably negative temperatures).
    text = ''.join(c for c in text if unicodedata.category(c) not in {'So', 'Sk', 'Cf'}
                   and c not in '\ufe0e\ufe0f\u20e3')
    lines = [re.sub(r'[ \t]+', ' ', line).strip(' 、，。；|') for line in text.splitlines()]
    lines = [line for line in lines if line]
    text = '。'.join(lines)
    text = re.sub(r'([？?！!])。', r'\1', text).strip()
    if not text:
        return '沒有可朗讀的回覆，請查看文字紀錄。'
    if text[-1] not in '。！？.!?':
        text += '。'
    return text
