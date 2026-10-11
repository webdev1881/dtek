import io
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

KYIV = ZoneInfo('Europe/Kyiv')
WEEKDAYS = ['понеділок', 'вівторок', 'середа', 'четвер', 'пʼятниця', 'субота', 'неділя']

BG = (24, 38, 54)
WHITE = (255, 255, 255)
GREEN = (0, 170, 0)
RED = (255, 0, 0)
ORANGE = (255, 153, 0)
BAR_GREEN = (0, 200, 0)
BAR_RED = (220, 0, 0)

REGULAR = ['DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'segoeui.ttf', 'arial.ttf']
BOLD = ['DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 'segoeuib.ttf', 'arialbd.ttf']

# статус часа -> (цвет первой половины часа, цвет второй половины)
COLORS = {
    'yes': (GREEN, GREEN),
    'no': (RED, RED),
    'first': (RED, GREEN),
    'second': (GREEN, RED),
    'maybe': (ORANGE, ORANGE),
    'mfirst': (ORANGE, GREEN),
    'msecond': (GREEN, ORANGE),
}


def _font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def day_date(ts):
    """Дата суток по unix-времени в киевском часовом поясе"""
    return datetime.fromtimestamp(int(ts), KYIV)


def _circle(draw, x, y, d, colors):
    box = (x, y, x + d, y + d)
    if colors[0] == colors[1]:
        draw.ellipse(box, fill=colors[0], outline=WHITE, width=2)
        return
    draw.pieslice(box, 90, 270, fill=colors[0])
    draw.pieslice(box, 270, 90, fill=colors[1])
    draw.ellipse(box, outline=WHITE, width=2)


def render_day(group, ts, hours):
    """PNG с графиком одной группы на сутки. hours — {"1": статус, ..., "24": статус}"""
    w, h = 540, 900
    img = Image.new('RGB', (w, h), BG)
    d = ImageDraw.Draw(img)
    f_title, f_title_b = _font(REGULAR, 32), _font(BOLD, 32)
    f_hour, f_small = _font(REGULAR, 28), _font(REGULAR, 17)

    def centered(parts, y):
        """Строка из кусков разного шрифта по центру"""
        total = sum(d.textlength(t, font=f) for t, f in parts)
        x = (w - total) / 2
        for t, f in parts:
            d.text((x, y), t, font=f, fill=WHITE)
            x += d.textlength(t, font=f)

    date = day_date(ts)
    centered([('Група: ', f_title), (group[3:], f_title_b)], 12)
    centered([(f'Дата: {date:%Y-%m-%d}, ', f_title), (WEEKDAYS[date.weekday()], f_title_b)], 58)

    light = 0.0
    for hour in range(24):
        status = hours.get(str(hour + 1), 'yes')
        colors = COLORS.get(status, COLORS['yes'])
        col, row = divmod(hour, 12)
        x_text = 70 + col * 242
        y = 122 + row * 54
        d.text((x_text, y), f'{hour:02d}:00', font=f_hour, fill=WHITE)
        _circle(d, x_text + 90, y - 4, 42, colors)
        light += sum(0.5 for c in colors if c != RED)

    off = 24 - light
    d.line((34, 790, w - 34, 790), fill=(90, 100, 110), width=1)
    bx0, bx1, by = 50, w - 50, 806
    split = bx0 + (bx1 - bx0) * light / 24
    d.rectangle((bx0, by, split, by + 30), fill=BAR_GREEN)
    if off > 0:
        d.rectangle((split, by, bx1, by + 30), fill=BAR_RED)
    for x, color, text in (
        (14, BAR_GREEN, f'Світло: {light:.1f}г ({light / 24:.1%})'),
        (278, BAR_RED, f'Відключення: {off:.1f}г ({off / 24:.1%})'),
    ):
        d.rectangle((x, 856, x + 14, 870), fill=color)
        d.text((x + 22, 852), text, font=f_small, fill=WHITE)

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()
