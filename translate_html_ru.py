import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Iterable

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None  # type: ignore


LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def has_latin(text: str) -> bool:
    return bool(LATIN_RE.search(text))


def has_cyrillic(text: str) -> bool:
    return bool(CYRILLIC_RE.search(text))


def protect_blocks(content: str, tag: str) -> Tuple[str, List[str]]:
    pattern = re.compile(rf"(<{tag}[^>]*>)([\s\S]*?)(</{tag}>)", re.IGNORECASE)
    saved: List[str] = []

    def repl(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"@@{tag.upper()}_{len(saved)-1}@@"

    new_content = pattern.sub(repl, content)
    return new_content, saved


def restore_blocks(content: str, tag: str, saved: List[str]) -> str:
    for idx, block in enumerate(saved):
        content = content.replace(f"@@{tag.upper()}_{idx}@@", block)
    return content


class BatchTranslator:
    def __init__(self) -> None:
        self.cache: Dict[str, str] = {}
        self._pending: List[Tuple[str, str, str, str, str]] = []
        if GoogleTranslator is not None:
            try:
                self.tr = GoogleTranslator(source="auto", target="ru")
            except Exception:
                self.tr = None
        else:
            self.tr = None

    def _translate_list(self, items: List[str]) -> List[str]:
        if not items:
            return []
        if self.tr is None:
            return items
        try:
            # deep_translator supports translate_batch
            return self.tr.translate_batch(items)  # type: ignore[attr-defined]
        except Exception:
            # Fallback: translate individually
            out: List[str] = []
            for it in items:
                try:
                    out.append(self.tr.translate(it))  # type: ignore[union-attr]
                except Exception:
                    out.append(it)
            return out

    def translate_preserving_ws(self, s: str) -> str:
        if s in self.cache:
            return self.cache[s]
        stripped = s.strip()
        if not stripped:
            self.cache[s] = s
            return s
        if has_cyrillic(stripped) and not has_latin(stripped):
            self.cache[s] = s
            return s
        if any(x in stripped for x in ("http://", "https://", "www.", "@", "{", "}")):
            self.cache[s] = s
            return s
        prefix = s[: len(s) - len(s.lstrip())]
        suffix = s[len(s.rstrip()):]
        placeholder = f"@@T_{len(self._pending)}@@"
        self._pending.append((placeholder, prefix, stripped, suffix, s))
        self.cache[s] = placeholder
        return placeholder

    def process_batches(self, content: str) -> str:
        if not self._pending:
            return content
        batch_texts = [item[2] for item in self._pending]
        # Chunk to avoid limits
        translated_all: List[str] = []
        chunk_size = 80
        for i in range(0, len(batch_texts), chunk_size):
            chunk = batch_texts[i:i+chunk_size]
            translated_all.extend(self._translate_list(chunk))
        # Replace placeholders
        for (placeholder, prefix, stripped, suffix, original), tr_text in zip(self._pending, translated_all):
            result = f"{prefix}{tr_text}{suffix}"
            content = content.replace(placeholder, result)
            # Update cache to final
            self.cache[original] = result
        # Clear pending
        self._pending.clear()
        return content


def translate_attributes(content: str, bt: BatchTranslator) -> str:
    attrs = ["alt", "title", "placeholder", "aria-label", "aria-placeholder", "data-title"]
    # double quotes first
    for attr in attrs:
        pattern_dq = re.compile(rf"({attr}\s*=\s*\")(.*?)(\")", re.IGNORECASE)
        def repl_dq(m: re.Match) -> str:
            val = m.group(2)
            if not has_latin(val):
                return m.group(0)
            translated = bt.translate_preserving_ws(val)
            return f"{m.group(1)}{translated}{m.group(3)}"
        content = pattern_dq.sub(repl_dq, content)

        pattern_sq = re.compile(rf"({attr}\s*=\s*\')(.*?)(\')", re.IGNORECASE)
        def repl_sq(m: re.Match) -> str:
            val = m.group(2)
            if not has_latin(val):
                return m.group(0)
            translated = bt.translate_preserving_ws(val)
            return f"{m.group(1)}{translated}{m.group(3)}"
        content = pattern_sq.sub(repl_sq, content)

    content = bt.process_batches(content)
    return content


def translate_textnodes(content: str, bt: BatchTranslator) -> str:
    pattern = re.compile(r">([^<>]+)<")

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if not inner or not has_latin(inner):
            return m.group(0)
        translated = bt.translate_preserving_ws(inner)
        return f">{translated}<"

    content = pattern.sub(repl, content)
    content = bt.process_batches(content)
    return content


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python translate_html_ru.py /absolute/path/to/index.html")
        return 2

    src_path = Path(sys.argv[1])
    if not src_path.exists():
        print(f"File not found: {src_path}")
        return 1

    html = src_path.read_text(encoding="utf-8")

    # Switch lang attribute to ru if set to it
    html = re.sub(r'(<html[^>]*\blang\s*=\s*")it(\")', r"\1ru\2", html, flags=re.IGNORECASE)
    html = re.sub(r"(<html[^>]*\blang\s*=\s*\')it(\')", r"\1ru\2", html, flags=re.IGNORECASE)

    # Protect blocks that must not be translated
    html, styles = protect_blocks(html, "style")
    html, scripts = protect_blocks(html, "script")

    bt = BatchTranslator()

    # Attributes first
    html = translate_attributes(html, bt)

    # Then text between tags
    html = translate_textnodes(html, bt)

    # Restore protected blocks
    html = restore_blocks(html, "script", scripts)
    html = restore_blocks(html, "style", styles)

    out_path = src_path.with_name(src_path.stem + ".ru" + src_path.suffix)
    out_path.write_text(html, encoding="utf-8")
    print(f"Written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())