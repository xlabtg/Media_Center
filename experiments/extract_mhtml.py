#!/usr/bin/env python3
"""Извлечение текстового содержимого из MHTML-снапшота чата Qwen."""
import email
import quopri
import sys
from bs4 import BeautifulSoup

SRC = "Народный Медиацентр Регламент.mhtml"
OUT = "experiments/reglament_text.txt"
OUT_HTML = "experiments/reglament_main.html"

with open(SRC, "rb") as f:
    msg = email.message_from_binary_file(f)

html_parts = []
for part in msg.walk():
    ctype = part.get_content_type()
    if ctype == "text/html":
        payload = part.get_payload(decode=True)
        if payload:
            charset = part.get_content_charset() or "utf-8"
            try:
                html = payload.decode(charset, errors="replace")
            except LookupError:
                html = payload.decode("utf-8", errors="replace")
            html_parts.append(html)

print(f"Найдено HTML-частей: {len(html_parts)}", file=sys.stderr)

# Берём самую большую HTML-часть (основной контент)
html_parts.sort(key=len, reverse=True)
main_html = html_parts[0]

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(main_html)

soup = BeautifulSoup(main_html, "lxml")

# Удаляем скрипты и стили
for tag in soup(["script", "style", "noscript"]):
    tag.decompose()

text = soup.get_text(separator="\n")

# Чистим пустые строки
lines = [ln.strip() for ln in text.splitlines()]
lines = [ln for ln in lines if ln]
clean = "\n".join(lines)

with open(OUT, "w", encoding="utf-8") as f:
    f.write(clean)

print(f"Длина чистого текста: {len(clean)} символов", file=sys.stderr)
print(f"Строк: {len(lines)}", file=sys.stderr)
