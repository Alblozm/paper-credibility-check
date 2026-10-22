#!/usr/bin/env python3
"""Reconcile pipeline_state with already validated on-disk deliverables."""
import json
from pathlib import Path
from pipeline_common import ROOT, atomic_json, utc_now

path=ROOT/'data/quality_reports/pipeline_state.json'
state=json.loads(path.read_text()) if path.exists() else {'pipeline_version':'1'}
manifest=json.loads((ROOT/'data/manifests/pipeline_papers.json').read_text())
papers=state.setdefault('papers',{})
completed=0
for item in manifest['papers']:
    if not item.get('enabled',True): continue
    pid=item['paper_id']; version=item['version']; folder=ROOT/'data/parsed'/pid/version
    assessment=ROOT/'data/annotations/ai_trial'/f'{pid}.json'
    if not all((folder/name).exists() for name in ['metadata.json','sections.json','chunks.jsonl','elements.json','parse_report.json']) or not assessment.exists():
        continue
    a=json.loads(assessment.read_text())
    if a.get('status')!='approved': continue
    row=papers.setdefault(pid,{'paper_id':pid,'version':version,'stages':{}})
    row['version']=version; row['status']='completed'; row['updated_at']=utc_now()
    row['error']=None; row['traceback']=None
    row.setdefault('stages',{})['score']={'status':'completed','at':a.get('created_at'),
        'output':json.dumps({'overall_score':a.get('overall_score'),'assessment_id':a.get('assessment_id')})}
    completed += 1
state['manifest']=str((ROOT/'data/manifests/pipeline_papers.json').relative_to(ROOT))
state['finished_at']=utc_now(); state['summary']={'selected':completed,'completed':completed,'failed':0}
atomic_json(path,state)
print(f'[state] completed={completed} failed=0')
