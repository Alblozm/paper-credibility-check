#!/usr/bin/env python3
"""Add the locked ICLR 2025/2026 proceedings selection to the pipeline manifest."""
import html
import json
import re
import ssl
from pathlib import Path
from urllib.request import Request, urlopen

import certifi

ROOT=Path(__file__).resolve().parents[1]
SELECTION=[
 (31,2026,"002a6bef26174b590e4eab95b63feb1b","agents"),
 (32,2026,"00a0ebcad584c59dbc439c2af8793638","transformers"),
 (33,2026,"00a5213b3479c9733eb3743459badc6b","calibration"),
 (34,2026,"00c5c0f92581f8b8230c9f349126a73a","time_series"),
 (35,2026,"014898b821e3ce966ae1db68cb8d9966","hallucination"),
 (36,2026,"01a8d63f9cb6dcbaa3092ccddd2075ac","rl_theory"),
 (37,2026,"033d9e8dbbbc0ad90e59222cf1db0fc2","robotics"),
 (38,2026,"039c30e9af8039fbd1b58da9d04f38e9","climate"),
 (39,2026,"0422dcfb36920e92713c3a12974b4300","graph_learning"),
 (40,2026,"0480adaf62a918405a5e3b1031e0c056","multimodal"),
 (41,2026,"04d39dca309cb02e8d7a8f16b75f41c5","uncertainty"),
 (42,2026,"0ae94013da7cd459402fd77874e09ee3","world_models"),
 (43,2026,"3922f1239a29dcb98f70f3d7bce545a0","fairness"),
 (44,2026,"bf11e0bb868b6735afcdb35ff39ea831","privacy"),
 (45,2026,"c1b3d1e2cf53bb28cabd801bd58b3521","unlearning"),
 (46,2025,"7cd4d633817da18d0beaf5fe5a8691e1","privacy"),
 (47,2025,"4264ee4376776907c0b87ed70b959585","llm_evaluation"),
 (48,2025,"1a25185fe1b0d46241de7738ea320710","federated_learning"),
 (49,2025,"45fc8e1c55df116b23488f3cdb2fc642","optimization"),
 (50,2025,"62302a24b04589f9f9cdd5b02c344b6c","in_context_learning"),
]
ctx=ssl.create_default_context(cafile=certifi.where())
manifest_path=ROOT/"data/manifests/pipeline_papers.json"
manifest=json.loads(manifest_path.read_text())
existing={p["paper_id"] for p in manifest["papers"]}
selection_records=[]
for number,year,digest,topic in SELECTION:
    pid=f"paper_{number:03d}"
    page=f"https://proceedings.iclr.cc/paper_files/paper/{year}/hash/{digest}-Abstract-Conference.html"
    raw=urlopen(Request(page,headers={"User-Agent":"paper-credibility-check-data/1.0"}),context=ctx,timeout=60).read().decode()
    def meta(name):
        m=re.search(rf'<meta name="{name}" content="([^"]*)"',raw)
        return html.unescape(m.group(1)) if m else None
    title=meta("citation_title")
    authors=[html.unescape(x) for x in re.findall(r'<meta name="citation_author" content="([^"]*)"',raw)]
    pdf=meta("citation_pdf_url")
    m=re.search(r'<p class="paper-abstract"><p>(.*?)</p>',raw,re.S)
    abstract=html.unescape(re.sub(r'<[^>]+>',' ',m.group(1))).strip() if m else None
    record={"paper_id":pid,"version":f"iclr_{year}_proceedings","source_type":"pdf",
      "source_url":pdf,"landing_page_url":page,"title":title,"authors":authors,
      "abstract":abstract,"language":"en","venue":"ICLR","publication_year":year,
      "dataset_split":"test","decision":"Accept (proceedings)","decision_verified":True,
      "tags":[f"iclr_{year}","official_proceedings","recent_holdout",topic],"enabled":True}
    selection_records.append(record)
    if pid not in existing: manifest["papers"].append(record)
manifest["target_paper_count"]=50
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
(ROOT/"data/manifests/recent_2025_2026_selection.json").write_text(json.dumps({
  "selection_basis":"Official ICLR proceedings; topic-diverse recent holdout, selected without scores.",
  "papers":selection_records},ensure_ascii=False,indent=2)+"\n")
print(f"[recent-selection] manifest={len(manifest['papers'])}, added={len(selection_records)}")
