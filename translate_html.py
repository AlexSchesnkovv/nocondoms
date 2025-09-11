#!/usr/bin/env python3
import re
import sys
import os
from typing import Dict, List, Tuple

try:
    from deep_translator import GoogleTranslator
except Exception as e:
    print("[ERROR] deep_translator is not installed. Install with: pip install deep-translator", file=sys.stderr)
    raise


VISIBLE_ATTRS = [
    "alt",
    "title",
    "placeholder",
    "aria-label",
]


def make_placeholders(html: str) -> Tuple[str, Dict[str, str]]:
    placeholders: Dict[str, str] = {}

    def _store(kind: str, content: str) -> str:
        key = f"__PLACEHOLDER_{kind}_{len(placeholders)}__"
        placeholders[key] = content
        return key

    # Protect comments
    html = re.sub(r"<!--[\s\S]*?-->", lambda m: _store("COMMENT", m.group(0)), html)
    # Protect scripts
    html = re.sub(r"<script\b[\s\S]*?</script>", lambda m: _store("SCRIPT", m.group(0)), html, flags=re.IGNORECASE)
    # Protect styles
    html = re.sub(r"<style\b[\s\S]*?</style>", lambda m: _store("STYLE", m.group(0)), html, flags=re.IGNORECASE)

    return html, placeholders


def restore_placeholders(html: str, placeholders: Dict[str, str]) -> str:
    for key, value in placeholders.items():
        html = html.replace(key, value)
    return html


def find_text_nodes(html: str) -> List[str]:
    texts: List[str] = []
    placeholder_re = re.compile(r"__PLACEHOLDER_[A-Z]+_\d+__")
    # Capture text between tags, ignoring angle brackets
    for m in re.finditer(r">([^<>]+)<", html):
        text = m.group(1)
        if not text.strip():
            continue
        if placeholder_re.fullmatch(text.strip()):
            # Skip our placeholders to prevent them from being translated
            continue
        # Skip if it's only punctuation or decorative characters
        if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text):
            # Still could be numbers or symbols; usually not translatable
            continue
        texts.append(text)
    return texts


def find_attr_values(html: str) -> List[Tuple[str, str, str]]:
    # Returns list of (attr, quote, value) occurrences for replacement later
    occurrences: List[Tuple[str, str, str]] = []
    placeholder_re = re.compile(r"__PLACEHOLDER_[A-Z]+_\d+__")
    for attr in VISIBLE_ATTRS:
        pattern = rf"{attr}\s*=\s*(\"([^\"]*)\"|'([^']*)')"
        for m in re.finditer(pattern, html, flags=re.IGNORECASE):
            quoted = m.group(1)
            value = m.group(2) if m.group(2) is not None else (m.group(3) or "")
            if value and not placeholder_re.fullmatch(value.strip()) and re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", value):
                occurrences.append((attr, quoted[0], value))
    return occurrences


def batch_translate(strings: List[str], source_lang: str = "it", target_lang: str = "th") -> Dict[str, str]:
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    mapping: Dict[str, str] = {}
    # De-duplicate while preserving order
    seen = set()
    uniq: List[str] = []
    for s in strings:
        if s not in seen:
            seen.add(s)
            uniq.append(s)

    # Chunk to avoid request size limits
    CHUNK = 40
    for i in range(0, len(uniq), CHUNK):
        chunk = uniq[i:i + CHUNK]
        try:
            translations = translator.translate_batch(chunk)
        except Exception as e:
            # Fallback: translate individually for this chunk
            translations = []
            for s in chunk:
                try:
                    translations.append(translator.translate(s))
                except Exception as ie:
                    translations.append(s)
        for original, translated in zip(chunk, translations):
            mapping[original] = translated
    return mapping


def replace_between_tags(html: str, mapping: Dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        original = m.group(1)
        # Preserve leading/trailing whitespace
        leading_ws = re.match(r"^\s*", original).group(0)
        trailing_ws = re.search(r"\s*$", original).group(0)
        core = original[len(leading_ws):len(original) - len(trailing_ws)]
        if not core:
            return m.group(0)
        translated = mapping.get(original)
        if translated is None:
            # Try mapping by core if surrounding whitespace varied
            translated = mapping.get(core, core)
        return ">" + leading_ws + translated + trailing_ws + "<"

    return re.sub(r">([^<>]+)<", repl, html)


def replace_attr_values(html: str, mapping: Dict[str, str]) -> str:
    # Replace attribute values while preserving quotes
    def make_attr_replacer(attr: str):
        pattern = rf"({attr}\s*=\s*)(\"([^\"]*)\"|'([^']*)')"

        def _repl(m: re.Match) -> str:
            prefix = m.group(1)
            quote_block = m.group(2)
            dbl_val = m.group(3)
            sgl_val = m.group(4)
            val = dbl_val if dbl_val is not None else (sgl_val or "")
            if not val:
                return m.group(0)
            leading_ws = re.match(r"^\s*", val).group(0)
            trailing_ws = re.search(r"\s*$", val).group(0)
            core = val[len(leading_ws):len(val) - len(trailing_ws)]

            translated = mapping.get(val)
            if translated is None:
                translated = mapping.get(core, core)

            quote_char = '"' if dbl_val is not None else "'"
            return f"{prefix}{quote_char}{leading_ws}{translated}{trailing_ws}{quote_char}"

        return re.sub(pattern, _repl, html, flags=re.IGNORECASE)

    for attr in VISIBLE_ATTRS:
        html = make_attr_replacer(attr)
    return html


def switch_lang_attr(html: str) -> str:
    # Change html lang attribute from it to th
    html = re.sub(r"(<html\b[^>]*\blang\s*=\s*)\"it(?:-[A-Za-z]+)?\"", r"\1\"th\"", html, flags=re.IGNORECASE)
    html = re.sub(r"(<html\b[^>]*\blang\s*=\s*)'it(?:-[A-Za-z]+)?'", r"\1'th'", html, flags=re.IGNORECASE)
    return html


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "/workspace/index.html"
    out_path = sys.argv[2] if len(sys.argv) > 2 else in_path

    if not os.path.exists(in_path):
        print(f"[ERROR] Input file not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    with open(in_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Backup
    backup_path = in_path + ".bak.it.html"
    if not os.path.exists(backup_path):
        with open(backup_path, "w", encoding="utf-8") as bf:
            bf.write(html)

    protected_html, placeholders = make_placeholders(html)

    # Gather texts
    text_nodes = find_text_nodes(protected_html)
    attr_occ = find_attr_values(protected_html)
    attr_texts = [val for (_, _, val) in attr_occ]

    all_texts = text_nodes + attr_texts
    if not all_texts:
        print("[INFO] No translatable text found.")
        sys.exit(0)

    print(f"[INFO] Translating {len(set(all_texts))} unique text segments it->th ...")
    mapping = batch_translate(all_texts, source_lang="it", target_lang="th")

    # Replace texts
    translated = replace_between_tags(protected_html, mapping)
    translated = replace_attr_values(translated, mapping)
    translated = switch_lang_attr(translated)

    # Restore protected blocks
    translated = restore_placeholders(translated, placeholders)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(translated)

    print(f"[OK] Translated file written to: {out_path}")


if __name__ == "__main__":
    main()

