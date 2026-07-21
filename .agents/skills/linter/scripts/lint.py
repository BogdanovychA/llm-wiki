import argparse
import json
import os
import re
import sys

# Розширення, які вважаємо медіафайлами (зображення, відео, документи-вкладення).
# Універсальний набір: будь-яке посилання з таким розширенням трактується як медіа,
# а не як внутрішня сторінка вікі (.md) чи текстове джерело (.md).
MEDIA_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".mp4",
    ".webm",
    ".ogv",
    ".mov",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}


def parse_yaml_frontmatter(content):
    frontmatter = {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return frontmatter, content

    yaml_text = match.group(1)
    body_text = content[match.end() :]

    current_key = None
    list_accumulator = []

    for line in yaml_text.splitlines():
        if not line.strip():
            continue
        # Check if it is a list item
        list_match = re.match(r"^\s*-\s*\"?(.*?)\"?\s*$", line)
        if list_match and current_key is not None:
            list_accumulator.append(list_match.group(1))
            continue

        # If we hit a new key, save the accumulated list to the previous key if there is one
        if list_match:
            continue

        key_match = re.match(r"^\s*(\w+):\s*\"?(.*?)\"?\s*$", line)
        if key_match:
            if current_key is not None:
                if list_accumulator:
                    frontmatter[current_key] = list_accumulator
                list_accumulator = []

            current_key = key_match.group(1)
            val = key_match.group(2).strip()
            if val:
                frontmatter[current_key] = val
            else:
                frontmatter[current_key] = []

    if current_key is not None and list_accumulator:
        frontmatter[current_key] = list_accumulator

    return frontmatter, body_text


def is_media_link(target):
    """Універсальна перевірка: чи є ціль медіафайлом (за розширенням)."""
    base = target.split("#")[0].split("?")[0].rstrip("/")
    ext = os.path.splitext(base)[1].lower()
    return ext in MEDIA_EXT


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_repo_root = os.path.abspath(os.path.join(script_dir, "../../../../"))

    parser = argparse.ArgumentParser(description="Linter for LLM Wiki")
    parser.add_argument(
        "--repo-root", default=default_repo_root, help="Path to the repository root"
    )
    args = parser.parse_args()

    repo_root = args.repo_root
    wiki_dir = os.path.join(repo_root, "wiki")
    raw_dir = os.path.join(repo_root, "raw")
    assets_dir = os.path.join(raw_dir, "assets")
    index_path = os.path.join(wiki_dir, "index.md")

    if not os.path.exists(wiki_dir):
        print(f"Error: wiki directory not found at {wiki_dir}", file=sys.stderr)
        sys.exit(1)

    # 1. Gather all wiki files
    parsed_wiki = {}
    categories = ["concepts", "entities", "archives"]
    wiki_files = {}
    for cat in categories:
        cat_dir = os.path.join(wiki_dir, cat)
        if os.path.exists(cat_dir):
            for f in os.listdir(cat_dir):
                if f.endswith(".md") and f != ".gitkeep":
                    abs_p = os.path.join(cat_dir, f)
                    rel_p = os.path.join(cat, f)
                    wiki_files[rel_p] = {
                        "filename": f,
                        "category": cat,
                        "rel_path": rel_p,
                        "abs_path": abs_p,
                    }
                    # Parse for parsed_wiki (needed by index/orphan checks)
                    with open(abs_p, "r", encoding="utf-8") as pf:
                        pf_content = pf.read()
                    pf_fm, _ = parse_yaml_frontmatter(pf_content)
                    parsed_wiki[rel_p] = {
                        "frontmatter": pf_fm,
                        "abs_path": abs_p,
                        "title": pf_fm.get("title", f),
                        "updated": pf_fm.get("updated", "YYYY-MM-DD"),
                        "category": cat,
                    }

    # 1b. Gather raw markdown documents (excluding assets/ — they are media, not docs)
    raw_doc_files = {}
    if os.path.exists(raw_dir):
        for root, dirs, files in os.walk(raw_dir):
            if "assets" in root:
                continue
            for f in files:
                if f.endswith(".md") and f != ".gitkeep":
                    abs_p = os.path.join(root, f)
                    rel_p = os.path.relpath(abs_p, raw_dir)
                    raw_doc_files[rel_p] = {
                        "filename": f,
                        "rel_path": rel_p,
                        "abs_path": abs_p,
                    }

    # 2. Gather all raw markdown files (for source-resolution lookup)
    raw_files = {}
    for root, dirs, files in os.walk(raw_dir):
        if "assets" in root:
            continue
        for f in files:
            if f.endswith(".md") and f != ".gitkeep":
                abs_p = os.path.join(root, f)
                rel_p = os.path.relpath(abs_p, raw_dir)
                raw_files[rel_p] = {"filename": f, "rel_path": rel_p, "abs_path": abs_p}

    # Parse each wiki file
    all_links = []  # entries: (source_file, target_path, is_source_yaml, line_no, raw_text)

    # Helper to collect links from a file (wiki or raw doc) into all_links.
    def collect_links(file_rel, abs_path, is_raw_doc=False):
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        frontmatter, body = parse_yaml_frontmatter(content)

        # Check source/wiki in YAML (тільки у wiki-файлів; raw-документи теж можуть мати, але
        # за контрактом вони архівні — перевіряємо лише їхні тілесні посилання на медіа).
        if not is_raw_doc:
            yaml_sources = frontmatter.get("sources", [])
            if not isinstance(yaml_sources, list):
                yaml_sources = [yaml_sources]
            for src in yaml_sources:
                all_links.append(
                    {
                        "source_file": file_rel,
                        "source_abs": abs_path,
                        "target": src,
                        "is_yaml_source": True,
                        "line": None,
                        "raw_text": src,
                    }
                )

            yaml_wiki = frontmatter.get("wiki", [])
            if not isinstance(yaml_wiki, list):
                yaml_wiki = [yaml_wiki]
            for w in yaml_wiki:
                all_links.append(
                    {
                        "source_file": file_rel,
                        "source_abs": abs_path,
                        "target": w,
                        "is_yaml_wiki": True,
                        "line": None,
                        "raw_text": w,
                    }
                )

        # Check markdown links in body (включно з медіа ![]())
        lines = content.splitlines()
        for idx, line in enumerate(lines):
            matches = re.finditer(r"!?\[(.*?)\]\((.*?)\)", line)
            for m in matches:
                target = m.group(2)
                # Ignore absolute web URLs and anchors
                if (
                    target.startswith("http://")
                    or target.startswith("https://")
                    or target.startswith("#")
                    or target.startswith("mailto:")
                ):
                    continue
                all_links.append(
                    {
                        "source_file": file_rel,
                        "source_abs": abs_path,
                        "target": target,
                        "is_yaml_source": False,
                        "is_yaml_wiki": False,
                        "is_raw_doc": is_raw_doc,
                        "line": idx + 1,
                        "raw_text": m.group(0),
                    }
                )

    for rel_path, info in wiki_files.items():
        collect_links(info["rel_path"], info["abs_path"], is_raw_doc=False)

    # log.md — append-only лог, перевіряємо лише його посилання, не додаємо в parsed_wiki
    log_abs = os.path.join(wiki_dir, "log.md")
    if os.path.exists(log_abs):
        collect_links("log.md", log_abs, is_raw_doc=False)

    for rel_path, info in raw_doc_files.items():
        # raw-документи додаємо під ключем, що вказує на їхнє розташування
        # відносно repo_root, щоб розв'язання шляхів працювало коректно.
        collect_links(os.path.join("raw", rel_path), info["abs_path"], is_raw_doc=True)

    # Read index.md
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index_content = f.read()
    else:
        index_content = ""

    # Parse index links
    index_links = []
    for m in re.finditer(r"\[(.*?)\]\((.*?)\)", index_content):
        index_links.append({"text": m.group(1), "target": m.group(2)})

    # 3. Analyze Index Consistency
    index_errors = []
    # For each wiki file, check if it is in the index
    for rel_path, data in parsed_wiki.items():
        found = False
        for link in index_links:
            if link["target"] == rel_path or link["target"] == "./" + rel_path:
                found = True
                break
        if not found:
            index_errors.append(
                {
                    "type": "missing_in_index",
                    "file": rel_path,
                    "category": data["category"],
                    "title": data["title"],
                    "updated": data["updated"],
                }
            )

    # Check if index has missing files
    for link in index_links:
        if link["target"].startswith("http://") or link["target"].startswith(
            "https://"
        ):
            continue
        target_path = link["target"].lstrip("./")
        target_abs = os.path.abspath(os.path.join(wiki_dir, target_path))
        if not os.path.exists(target_abs):
            index_errors.append(
                {
                    "type": "dangling_index_link",
                    "target": link["target"],
                    "text": link["text"],
                }
            )

    # 4. Check all links
    link_errors = []
    autofixes = []

    # Кеш знайдених медіа-файлів у raw/assets (рекурсивно) за іменем
    def get_assets_index():
        idx = {}
        if os.path.exists(assets_dir):
            for root, dirs, files in os.walk(assets_dir):
                for f in files:
                    idx.setdefault(f, []).append(os.path.join(root, f))
        return idx

    assets_index = get_assets_index()

    for link in all_links:
        src_dir = os.path.dirname(link["source_abs"])
        target_abs = os.path.abspath(os.path.join(src_dir, link["target"]))

        if os.path.exists(target_abs):
            continue  # посилання валідне — нічого робити

        # --- Посилання бите, класифікуємо та намагаємося виправити ---
        filename = os.path.basename(link["target"])
        media = is_media_link(link["target"])
        is_log = link["source_file"] == "log.md"

        if media:
            # Медіа: шукаємо файл за іменем у raw/assets/ (рекурсивно)
            candidates = assets_index.get(filename, [])
            if len(candidates) == 1:
                new_target = os.path.relpath(candidates[0], src_dir)
                # raw-документи архівні й незмінні → НЕ автофіксимо, лише звітуємо
                if link.get("is_raw_doc", False) or is_log:
                    link_errors.append(
                        {
                            "file": link["source_file"],
                            "target": link["target"],
                            "line": link["line"],
                            "suggested": new_target,
                            "type": "broken_log_media"
                            if is_log
                            else "broken_raw_media",
                        }
                    )
                else:
                    autofixes.append(
                        {
                            "file": link["source_file"],
                            "old_target": link["target"],
                            "new_target": new_target,
                            "is_yaml": False,
                            "line": link["line"],
                            "raw_text": link["raw_text"],
                        }
                    )
            else:
                link_errors.append(
                    {
                        "file": link["source_file"],
                        "target": link["target"],
                        "line": link["line"],
                        "matches_count": len(candidates),
                        "type": "broken_log_media" if is_log else "broken_media",
                    }
                )
            continue

        # Не медіа → це .md посилання (внутрішнє або на джерело)
        is_raw = "raw" in link["target"] or link.get("is_yaml_source", False)

        if is_raw:
            matches = []
            for rf_rel, r_info in raw_files.items():
                if r_info["filename"] == filename:
                    matches.append(r_info)
            if len(matches) == 1:
                new_target = os.path.relpath(matches[0]["abs_path"], src_dir)
                # raw-документи архівні → не автофіксимо, лише звітуємо
                if link.get("is_raw_doc", False) or is_log:
                    link_errors.append(
                        {
                            "file": link["source_file"],
                            "target": link["target"],
                            "line": link["line"],
                            "suggested": new_target,
                            "type": "broken_log_source"
                            if is_log
                            else "broken_raw_source",
                        }
                    )
                else:
                    autofixes.append(
                        {
                            "file": link["source_file"],
                            "old_target": link["target"],
                            "new_target": new_target,
                            "is_yaml": link.get("is_yaml_source", False),
                            "line": link["line"],
                            "raw_text": link["raw_text"],
                        }
                    )
            else:
                link_errors.append(
                    {
                        "file": link["source_file"],
                        "target": link["target"],
                        "line": link["line"],
                        "matches_count": len(matches),
                        "type": "broken_log_source" if is_log else "broken_raw_source",
                    }
                )
        else:
            matches = []
            for wf_rel, w_info in wiki_files.items():
                if w_info["filename"] == filename:
                    matches.append(w_info)
            if len(matches) == 1:
                new_target = os.path.relpath(matches[0]["abs_path"], src_dir)
                if is_log:
                    link_errors.append(
                        {
                            "file": link["source_file"],
                            "target": link["target"],
                            "line": link["line"],
                            "suggested": new_target,
                            "type": "broken_log_link",
                        }
                    )
                else:
                    autofixes.append(
                        {
                            "file": link["source_file"],
                            "old_target": link["target"],
                            "new_target": new_target,
                            "is_yaml": link.get("is_yaml_wiki", False)
                            or link.get("is_yaml_source", False),
                            "line": link["line"],
                            "raw_text": link["raw_text"],
                        }
                    )
            else:
                link_errors.append(
                    {
                        "file": link["source_file"],
                        "target": link["target"],
                        "line": link["line"],
                        "matches_count": len(matches),
                        "type": "broken_log_link" if is_log else "broken_internal_link",
                    }
                )

    # 5. Check if all raw files are referenced
    referenced_raw = set()
    for link in all_links:
        if link["source_file"] == "log.md":
            continue
        src_dir = os.path.dirname(link["source_abs"])
        target_abs = os.path.abspath(os.path.join(src_dir, link["target"]))
        if os.path.exists(target_abs) and target_abs.startswith(raw_dir):
            referenced_raw.add(os.path.normpath(target_abs))

    for f in autofixes:
        src_dir = os.path.dirname(os.path.join(wiki_dir, f["file"]))
        target_abs = os.path.abspath(os.path.join(src_dir, f["new_target"]))
        if os.path.exists(target_abs) and target_abs.startswith(raw_dir):
            referenced_raw.add(os.path.normpath(target_abs))

    unreferenced_raw = []
    for rf_rel, r_info in raw_files.items():
        norm_p = os.path.normpath(r_info["abs_path"])
        if norm_p not in referenced_raw:
            unreferenced_raw.append(r_info["rel_path"])

    # 6. Orphaned pages (pages not linked to by any other wiki page)
    referenced_wiki = set()
    for link in all_links:
        if link.get("is_yaml_source") or link.get("is_yaml_wiki"):
            continue
        if link["source_file"] == "log.md":
            continue
        src_dir = os.path.dirname(link["source_abs"])
        target_abs = os.path.abspath(os.path.join(src_dir, link["target"]))
        if os.path.exists(target_abs) and target_abs.startswith(wiki_dir):
            rel = os.path.relpath(target_abs, wiki_dir)
            if rel != "index.md" and rel != "log.md":
                referenced_wiki.add(rel)

    orphan_pages = []
    for rel_path, data in parsed_wiki.items():
        if rel_path not in referenced_wiki:
            orphan_pages.append(rel_path)

    # 7. Duplicate links within the same file
    duplicate_links = []
    for rel_path, data in parsed_wiki.items():
        if data["category"] == "log":
            continue
        abs_path = data["abs_path"]
        with open(abs_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Find where "Джерела та посилання" section starts
        sources_start = len(lines)
        for idx, line in enumerate(lines):
            if re.match(r"^##\s+Джерела та посилання", line):
                sources_start = idx
                break
        # Track body and sources targets separately
        body_targets = {}
        sources_targets = {}
        for idx, line in enumerate(lines):
            in_sources = idx >= sources_start
            bucket = sources_targets if in_sources else body_targets
            for m in re.finditer(r"!?\[(.*?)\]\((.*?)\)", line):
                target = m.group(2)
                if (
                    target.startswith("http://")
                    or target.startswith("https://")
                    or target.startswith("#")
                    or target.startswith("mailto:")
                ):
                    continue
                bucket.setdefault(target, []).append(idx + 1)
        # Find duplicates within sources section only
        for target, line_nums in sources_targets.items():
            if len(line_nums) > 1:
                duplicate_links.append(
                    {
                        "file": rel_path,
                        "target": target,
                        "lines": line_nums,
                    }
                )

    # Output results
    results = {
        "index_errors": index_errors,
        "link_errors": link_errors,
        "autofixes": autofixes,
        "unreferenced_raw": unreferenced_raw,
        "orphan_pages": orphan_pages,
        "duplicate_links": duplicate_links,
    }

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
