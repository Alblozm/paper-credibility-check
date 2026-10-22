#!/usr/bin/env python3
"""Generic, evidence-preserving PDF structurer for the benchmark."""
from __future__ import annotations

import argparse, hashlib, json, re, statistics, unicodedata
from collections import Counter
from pathlib import Path
import pdfplumber

NUMBERED = re.compile(r"^(?P<num>(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*))\.?\s+(?P<title>\S.*)$")
SPECIAL = {"abstract", "references", "bibliography", "acknowledgments",
           "acknowledgements", "limitations", "broader impact", "ethics statement"}
CAPTION = re.compile(r"^(Figure|Fig\.|Table)\s*([A-Z]?\.?\d+(?:\.\d+)?)\s*[:.]?\s*(.*)$", re.I)
THEOREM = re.compile(r"^(Theorem|Lemma|Proposition|Corollary|Definition)\s+([A-Z]?\.?\d+(?:\.\d+)*)", re.I)
URL = re.compile(r"https?://[^\s<>{}\[\]\"']+", re.I)
ROLES = (("abstract","abstract"),("introduction","introduction"),("background","background"),
         ("related work","related_work"),("preliminar","background"),("method","method"),
         ("approach","method"),("theor","theory"),("experiment","experiments"),
         ("evaluation","experiments"),("result","results"),("analysis","results"),
         ("ablation","results"),("discussion","discussion"),("limitation","limitations"),
         ("conclusion","conclusion"),("reference","references"),("appendix","appendix"),
         ("implementation","implementation_details"))


def clean(text):
    text = str(text or "").replace("\x00", " ").replace("\u00ad", "")
    text = "".join(c if c in "\n\r\t" or unicodedata.category(c) not in {"Cc","Cs"} else " " for c in text)
    return re.sub(r"[ \t]+", " ", text).strip()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "section"


def role(title):
    low = title.lower()
    return next((value for needle, value in ROLES if needle in low), "other")


def normalize_small_caps(text):
    # PDF fonts often extract small-caps as "I NTRODUCTION" or "M ATH V ISTA".
    appendix = re.match(r"^([A-H])\s+(.+)$", text)
    if appendix and len(appendix.group(2).split()) >= 2:
        prefix, rest = appendix.groups()
        previous = None
        while rest != previous:
            previous = rest
            rest = re.sub(r"\b([A-Z])\s+([A-Z]{2,})\b", r"\1\2", rest)
        combined = prefix + rest
        return clean(combined if combined.lower() in SPECIAL else prefix + " " + rest)
    previous = None
    while text != previous:
        previous = text
        text = re.sub(r"\b([A-Z])\s+([A-Z]{2,})\b", r"\1\2", text)
    return clean(text)


def page_lines(page, page_no):
    words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False,
                               extra_attrs=["fontname", "size"], use_text_flow=True)
    # arXiv stamps its identifier vertically in the extreme left margin.  PDF
    # extraction otherwise interleaves those one-character glyphs with body
    # lines (for example "a M ... 4 ... [ G").  Removing only tiny tokens wholly
    # inside the outer margin preserves ordinary body text and page numbers.
    page_width = float(page.width)
    words = [w for w in words if not (
        len(clean(w.get("text"))) <= 2 and
        (float(w["x1"]) < 45 or float(w["x0"]) > page_width - 45)
    )]
    groups = []
    for word in sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"]))):
        if not groups or abs(float(word["top"]) - statistics.median(float(x["top"]) for x in groups[-1])) > 3:
            groups.append([word])
        else:
            groups[-1].append(word)
    result = []
    for group in groups:
        group.sort(key=lambda w: float(w["x0"]))
        text = clean(" ".join(w["text"] for w in group))
        if text:
            result.append({
                "page": page_no, "top": min(float(w["top"]) for w in group),
                "bottom": max(float(w["bottom"]) for w in group),
                "x0": min(float(w["x0"]) for w in group), "text": text,
                "size": statistics.median(float(w.get("size") or 0) for w in group),
                "bold": sum("bold" in str(w.get("fontname", "")).lower() for w in group) >= max(1, len(group)//2),
            })
    return result


def heading(line, body_size):
    text = normalize_small_caps(line["text"].strip().rstrip(":"))
    # arXiv's vertical identifier can be merged as one stray leading glyph.
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and len(parts[0]) == 1:
        remainder = parts[1]
        nested = NUMBERED.match(remainder)
        if remainder.lower() in SPECIAL or (
            parts[0].isdigit() and nested and nested.group("num")[0].isdigit()
        ):
            text = remainder
    if text.lower() in SPECIAL:
        return None, text
    match = NUMBERED.match(text)
    if not match or len(text) > 180:
        return None
    number, title = match.group("num"), match.group("title").strip()
    if title.endswith(".") and title[:-1].strip().isupper():
        title = title[:-1].strip()
    tokens = title.split()
    alpha = sum(c.isalpha() for c in title)
    digits = sum(c.isdigit() for c in title)
    singletons = sum(len(re.sub(r"[^A-Za-z]","",t)) == 1 for t in tokens)
    word_tokens = [re.sub(r"[^A-Za-z]","",t) for t in tokens]
    word_tokens = [t for t in word_tokens if len(t) > 1]
    title_case = sum(t[:1].isupper() for t in word_tokens) / max(1,len(word_tokens))
    titleish = title.isupper() or title_case >= .60
    appendix_like = bool(re.fullmatch(r"[A-Z](?:\.\d+)*", number)) and (
        title.isupper() or line["bold"] or line["size"] >= body_size + 1.2
    )
    prominent = line["size"] >= body_size + 1.2 or line["bold"] or title.isupper() or appendix_like
    malformed = (
        not title[:1].isalpha() or alpha < max(3, len(title) * .45)
        or digits > max(2, len(title) * .12)
        or singletons > max(2, len(tokens) * .35)
        or bool(re.search(r"\s\d+\s*$", title))
        or number == "0" or not titleish
    )
    if prominent and not malformed and not title.endswith((".", ";")) and len(tokens) <= 18:
        return number, title
    return None


def paragraphs(lines, body_size):
    if not lines:
        return []
    gaps = [max(0, b["top"]-a["bottom"]) for a,b in zip(lines,lines[1:]) if a["page"]==b["page"]]
    normal = statistics.median(gaps) if gaps else body_size*.35
    groups, current = [], []
    for line in lines:
        if current:
            prev = current[-1]
            page_break = line["page"] != prev["page"]
            gap = line["top"]-prev["bottom"] if not page_break else normal*2
            if page_break or gap > max(normal*1.65, body_size*.7):
                groups.append(current); current=[]
        current.append(line)
    if current:
        groups.append(current)
    result = []
    for group in groups:
        text = ""
        for line in group:
            part = line["text"]
            text = text[:-1]+part if text.endswith("-") and part[:1].islower() else (text+" "+part).strip()
        if clean(text):
            result.append({"text":clean(text),"page_start":group[0]["page"],"page_end":group[-1]["page"]})
    return result


def make_chunks(items, target=3600):
    result, buffer = [], []
    for item in items:
        if buffer and sum(len(x["text"]) for x in buffer)+len(item["text"]) > target:
            result.append(buffer); buffer=[]
        buffer.append(item)
    if buffer:
        result.append(buffer)
    return [{"text":"\n\n".join(x["text"] for x in group),
             "page_start":group[0]["page_start"],"page_end":group[-1]["page_end"]}
            for group in result]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pdf",required=True,type=Path); ap.add_argument("--output",required=True,type=Path)
    ap.add_argument("--paper-id",required=True); ap.add_argument("--version",required=True)
    ap.add_argument("--source-record",type=Path)
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    source=json.loads(args.source_record.read_text()) if args.source_record else {}
    version_id=f"{args.paper_id}_{args.version}"; raw=args.pdf.read_bytes()

    with pdfplumber.open(args.pdf) as pdf:
        pages=list(pdf.pages); all_lines=[page_lines(p,i+1) for i,p in enumerate(pages)]
        heights=[float(p.height) for p in pages]
        sizes=[x["size"] for lines in all_lines for x in lines if x["size"]]
        body=statistics.median(sizes) if sizes else 10
        repeated=Counter()
        for lines,height in zip(all_lines,heights):
            repeated.update({re.sub(r"\d+","#",x["text"].lower()) for x in lines
                             if x["top"]<55 or x["bottom"]>height-45})
        repeated={x for x,n in repeated.items() if n>=max(3,round(len(pages)*.3))}
        stream=[]
        for lines,height in zip(all_lines,heights):
            for line in lines:
                norm=re.sub(r"\d+","#",line["text"].lower())
                if norm in repeated and (line["top"]<55 or line["bottom"]>height-45): continue
                if re.fullmatch(r"\d+",line["text"]): continue
                stream.append(line)

        marks=[]; seen_body_start=False; in_references=False; appendix_started=False; seen_numbers=set()
        for i,line in enumerate(stream):
            h=heading(line,body)
            if not h: continue
            number,title=h; low=normalize_small_caps(title).lower()
            is_start = low in {"abstract","introduction"} or "introduction" in low
            # Superscript affiliation numbers on title pages are not sections.
            if not seen_body_start and not is_start:
                continue
            if is_start: seen_body_start=True
            is_lettered = bool(number and re.fullmatch(r"[A-Z](?:\.\d+)*", number))
            if number and number in seen_numbers:
                continue
            # Lettered appendices in this corpus begin only after References.
            # Before that point a table row such as "A Initialization Field
            # Perplexity" must not become a section.
            if is_lettered and not in_references and not appendix_started:
                continue
            # Bibliographic entries frequently resemble numbered headings. Only a
            # lettered appendix may terminate the references section.
            if in_references and not is_lettered:
                continue
            if low in {"references","bibliography"}: in_references=True
            elif is_lettered:
                in_references=False; appendix_started=True
            elif appendix_started and number and number[0].isdigit():
                continue
            marks.append((i,number,title,line))
            if number: seen_numbers.add(number)
        if not marks or marks[0][0]>0:
            marks.insert(0,(0,None,"Front Matter",stream[0] if stream else {"page":1}))
        sections=[]; content_by_section={}; used=set()
        for pos,(start,number,title,line) in enumerate(marks):
            end=marks[pos+1][0] if pos+1<len(marks) else len(stream)
            content=stream[:end] if pos==0 and title=="Front Matter" else stream[start+1:end]
            base=slug(number or title); sid=f"{version_id}_sec_{base}"; n=2
            while sid in used: sid=f"{version_id}_sec_{base}_{n}"; n+=1
            used.add(sid)
            parent=None
            if number and "." in number:
                pn=number.rsplit(".",1)[0]
                parent=next((s["section_id"] for s in reversed(sections) if s["section_number"]==pn),None)
            sections.append({"section_id":sid,"paper_version_id":version_id,
                "parent_section_id":parent,"section_number":number,"title":title,
                "section_role":role(title),"level":number.count(".")+1 if number else 1,
                "sequence_index":len(sections),"page_start":line["page"],
                "page_end":content[-1]["page"] if content else line["page"]})
            content_by_section[sid]=content

        chunks=[]; per_section=Counter()
        for section in sections:
            for local,item in enumerate(make_chunks(paragraphs(content_by_section[section["section_id"]],body))):
                sequence=len(chunks); text=item["text"]
                chunks.append({"chunk_id":f"{version_id}_chunk_{sequence:04d}",
                    "paper_version_id":version_id,"section_id":section["section_id"],
                    "sequence_index":sequence,"chunk_index_in_section":local,
                    "page_start":item["page_start"],"page_end":item["page_end"],
                    "content_type":"body_text","text_content":text,
                    "token_count":max(1,round(len(text.split())*1.3)),"char_count":len(text),
                    "location_confidence":"high"})
                per_section[section["section_id"]]+=1

        elements=[]; seen=set(); element_ids=set()
        for page_no,lines in enumerate(all_lines,1):
            owner=max((s for s in sections if s["page_start"]<=page_no),key=lambda s:s["sequence_index"],default=None)
            table_cache=None
            for idx,line in enumerate(lines):
                match=CAPTION.match(line["text"]); theorem=THEOREM.match(line["text"])
                if match:
                    kind,num,rest=match.groups(); kind="figure" if kind.lower().startswith("fig") else "table"
                    label=f"{kind.title()} {num}"
                    if label in seen: continue
                    seen.add(label); nearby=" ".join(x["text"] for x in lines[idx:idx+5])
                    extracted=None
                    if kind=="table":
                        if table_cache is None: table_cache=pages[page_no-1].extract_tables() or []
                        if table_cache:
                            extracted="\n".join(" | ".join(clean(c) for c in row) for row in table_cache[0] if row)
                    eid=f"{version_id}_{kind}_{slug(num)}"; suffix=2
                    while eid in element_ids: eid=f"{version_id}_{kind}_{slug(num)}_{suffix}"; suffix+=1
                    element_ids.add(eid)
                    elements.append({"element_id":eid,
                        "paper_version_id":version_id,"section_id":owner["section_id"] if owner else None,
                        "element_type":kind,"label":label,"caption":clean(rest+" "+nearby[len(line["text"]):])[:1800] or None,
                        "page_number":page_no,"extracted_text":extracted,
                        "asset_object_key":f"renders/{args.paper_id}/{args.version}/page-{page_no:04d}.png",
                        "parse_quality":"good" if kind=="figure" or extracted else "partial"})
                elif theorem:
                    kind,num=theorem.groups(); label=f"{kind.title()} {num}"
                    if label in seen: continue
                    seen.add(label)
                    eid=f"{version_id}_theorem_{slug(num)}"; suffix=2
                    while eid in element_ids: eid=f"{version_id}_theorem_{slug(num)}_{suffix}"; suffix+=1
                    element_ids.add(eid)
                    elements.append({"element_id":eid,
                        "paper_version_id":version_id,"section_id":owner["section_id"] if owner else None,
                        "element_type":"theorem","label":label,"caption":line["text"][:1800],
                        "page_number":page_no,"extracted_text":line["text"],"asset_object_key":None,
                        "parse_quality":"partial"})

    parents={s["parent_section_id"] for s in sections if s["parent_section_id"]}
    containers=[s["section_id"] for s in sections if s["section_id"] in parents and not per_section[s["section_id"]]]
    scorable=[s for s in sections if s["section_role"] not in {"references","acknowledgments"}
              and s["section_id"] not in containers]
    empty=[s["section_id"] for s in scorable if not per_section[s["section_id"]]]
    coverage=1-len(empty)/max(1,len(scorable)); warnings=[]
    if empty: warnings.append("sections_without_direct_body_text")
    if not elements: warnings.append("no_figures_or_tables_detected")
    quality="good" if coverage>=.95 else "partial" if coverage>=.70 else "poor"
    gate="passed" if quality=="good" else "passed_with_caveats" if quality=="partial" else "failed"
    metadata={"paper_id":args.paper_id,"paper_version_id":version_id,"version_label":args.version,
        "source_type":source.get("source_type","arxiv" if source.get("arxiv_id") else "openreview"),
        "source_url":source.get("source_url"),"pdf_object_key":f"raw-papers/{args.paper_id}/{args.pdf.name}",
        "pdf_sha256":hashlib.sha256(raw).hexdigest(),"page_count":len(heights),
        "parser_name":"pdfplumber-structured","parser_version":"1.0.0","parse_status":"completed",
        "parse_quality":quality,"parse_warnings":warnings,"has_ocr":False,
        "reading_order_quality":"high" if coverage>=.9 else "medium"}
    # Record URLs independently from prose chunks so the backend can query code,
    # data and project links without regexing tens of thousands of characters.
    links=[]; seen_urls=set()
    for chunk in chunks:
        for raw_url in URL.findall(chunk["text_content"]):
            url=raw_url.rstrip(".,;:)]}")
            if url in seen_urls: continue
            seen_urls.add(url)
            low=url.lower()
            link_type=("code" if any(x in low for x in ("github.com","gitlab.com","code."))
                       else "data" if any(x in low for x in ("dataset","datasets","huggingface.co/datasets"))
                       else "appendix" if "appendix" in low
                       else "project" if any(x in low for x in ("project",".github.io","huggingface.co/spaces"))
                       else "other")
            links.append({"paper_link_id":f"{version_id}_link_{len(links)+1:03d}",
                "paper_version_id":version_id,"source_chunk_id":chunk["chunk_id"],
                "link_type":link_type,"url":url,"access_status":"unchecked","checked_at":None})
    source_url=source.get("source_url")
    if source_url and source_url not in seen_urls:
        links.insert(0,{"paper_link_id":f"{version_id}_link_source",
            "paper_version_id":version_id,"source_chunk_id":None,"link_type":"other",
            "url":source_url,"access_status":"accessible","checked_at":source.get("retrieved_at")})

    report={"paper_version_id":version_id,"acceptance_gate":gate,"page_count":len(heights),
        "section_count":len(sections),"chunk_count":len(chunks),"element_count":len(elements),
        "link_count":len(links),
        "scorable_section_coverage":round(coverage,4),"sections_without_direct_body_text":empty,
        "container_only_sections":containers,
        "cross_section_chunk_count":0,"visual_pages_reviewed":[],
        "manual_review_notes":["Visual QA pending; no page is marked reviewed automatically."]}
    old_report_path=args.output/"parse_report.json"
    if old_report_path.exists():
        old_report=json.loads(old_report_path.read_text())
        if old_report.get("visual_pages_reviewed")==list(range(1,len(heights)+1)):
            for field in ("visual_pages_reviewed","visual_review_status","visual_reviewed_at",
                          "manual_review_notes"):
                if field in old_report: report[field]=old_report[field]
    (args.output/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n")
    (args.output/"sections.json").write_text(json.dumps(sections,ensure_ascii=False,indent=2)+"\n")
    (args.output/"elements.json").write_text(json.dumps(elements,ensure_ascii=False,indent=2)+"\n")
    (args.output/"links.json").write_text(json.dumps(links,ensure_ascii=False,indent=2)+"\n")
    (args.output/"chunks.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in chunks))
    (args.output/"parse_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"paper":args.paper_id,"pages":len(heights),"sections":len(sections),
        "chunks":len(chunks),"elements":len(elements),"quality":quality,"gate":gate},ensure_ascii=False))

if __name__=="__main__":
    main()
