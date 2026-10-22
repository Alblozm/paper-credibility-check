"""Offline semantic regression tests; scoring numbers below are TEST ONLY."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from credibility_r2.core import ROOT, DIMENSIONS, ValidationError, build_report, load_json, validate_case, validate_rubric, validate_report
from credibility_r2.connectors import FetchResult, PublicJsonClient, collect_reproduction_signals, verify_reference
from credibility_r2.extraction import extract_references, extract_claim_candidates
from credibility_r2.presentation import render_html, export_seed

T = "2026-10-07T10:59:42Z"


def case():
    return load_json(ROOT / "data/examples/synthetic_vertical_slice.json")


def test_rubric():
    return {"rubric_id": "TEST_ONLY", "version": "NOT_FOR_REAL_PAPERS", "status": "approved", "approved_by": "unit-test-fixture", "approved_at": T,
            "weights": dict(zip(DIMENSIONS, (4, 3, 3))), "claim_scores": {"supported": 10, "partially_supported": 7, "evidence_not_found": 4, "contradicted": 1},
            "reference_scores": {"matched": 9, "metadata_conflict": 3, "unverified": 5}, "code_scores": {"official_code_available": 9, "independent_success_report": 9.5, "failure_or_data_problem_report": 3, "discussion_inconclusive": 6, "no_relevant_public_feedback": 8, "no_public_code_found": 5},
            "reference_aggregation": "mean", "code_aggregation": "min", "code_score_cap": 9, "low_score_threshold": 5,
            "categories": [{"category": "TEST-0", "lower_bound": 0}, {"category": "TEST-1", "lower_bound": 3}, {"category": "TEST-2", "lower_bound": 6}, {"category": "TEST-3", "lower_bound": 8}]}


def complete_case():
    c = case()
    c["scope_complete"] = c["claim_scope_complete"] = True
    c["claim_checks"][0]["scope_complete"] = True
    c["code_scope"] = {"applicability": "not_applicable", "justification": "TEST ONLY: synthetic theoretical-paper path", "searched_sources": [], "query_completed": False, "repository_checks": []}
    for ref, check in zip(c["references"], c["reference_checks"]):
        check.update(status="matched", query_attempted=True, source_url="https://example.org/test-metadata", checked_at=T,
                     candidates=[{"candidate_id": "test-candidate", "title": ref["parsed_title"], "authors": ref["parsed_authors"], "year": ref["parsed_year"], "years": [ref["parsed_year"]], "doi": ref["parsed_doi"], "source_url": "https://example.org/test-metadata"}], selected_candidate_id="test-candidate", match_confidence=1.0)
    return c


def confirmed_repo(c):
    repo = {"repository_id": "test-repo", "paper_version_id": c["paper_version"]["paper_version_id"], "repository_url": "https://github.com/example/TEST_ONLY", "platform": "GitHub", "association_status": "confirmed", "association_reviewed": True, "association_evidence": [{"kind": "paper_link", "source_url": "https://example.org/paper", "excerpt": "https://github.com/example/TEST_ONLY", "observed_at": T}], "access_status": "accessible", "checked_at": T}
    c["repositories"] = [repo]
    c["code_scope"] = {"applicability": "applicable", "justification": None, "searched_sources": [{"source_url": "https://example.org/paper", "checked_at": T, "status": "checked"}], "query_completed": True, "repository_checks": [{"repository_id": "test-repo", "completed": True, "truncated": False, "issues_checked": 1, "comments_checked": 0, "technical_error": None, "checked_at": T, "source_urls": ["https://api.github.com/repos/example/TEST_ONLY/issues"]}]}
    return repo


def signal():
    return {"signal_id": "sig-test", "repository_id": "test-repo", "signal_type": "discussion_inconclusive", "review_status": "reviewed", "source_url": "https://github.com/example/TEST_ONLY/issues/1", "source_title": "TEST_ONLY", "source_excerpt": "test observation", "third_party_claim": "Synthetic test claim", "third_party": True, "author_response": None, "resolution_status": "closed", "resolution_url": None, "observed_at": T, "confidence": None, "limitations": ["TEST ONLY"], "comments": []}


class FakeClient:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.urls = data, error, []
    def get(self, url):
        self.urls.append(url)
        data = self.data(url) if callable(self.data) else self.data
        return FetchResult(data, self.error, url, T)


class CoreTests(unittest.TestCase):
    def test_vertical_slice_is_explicitly_incomplete(self):
        r = build_report(case(), load_json(ROOT / "rubrics/credibility_v0.1/rubric.json"))
        self.assertFalse(r["scope_complete"])
        self.assertIsNone(r["overall_score"])
        self.assertEqual(r["findings"][0]["internal_evidence"][0]["chunk_id"], "results-1")

    def test_draft_rubric_never_invents_numbers(self):
        r = build_report(complete_case(), load_json(ROOT / "rubrics/credibility_v0.1/rubric.json"))
        self.assertTrue(r["scope_complete"])
        self.assertIsNone(r["overall_score"])
        self.assertIsNone(r["overall_category"])
        self.assertTrue(all(d["score"] is None for d in r["dimensions"]))

    def test_provisional_rubric_scores_without_formal_approval(self):
        rubric = test_rubric()
        rubric.update(status="provisional", approved_by=None, approved_at=None)
        report = build_report(complete_case(), rubric)
        self.assertEqual(report["rubric_status"], "provisional")
        self.assertIsNotNone(report["overall_score"])

    def test_provisional_failures_become_explained_low_verifiability_scores(self):
        c = complete_case()
        c["scope_complete"] = c["claim_scope_complete"] = False
        c["claim_checks"][0].update(verdict="unable_to_determine", evidence=[])
        c["reference_checks"][0].update(status="query_failed", technical_error="HTTP 429", candidates=[], selected_candidate_id=None)
        c["code_scope"] = {"applicability":"applicable", "justification":None, "searched_sources":[{"source_url":"https://example.org/paper", "checked_at":T, "status":"query_failed"}], "query_completed":True, "repository_checks":[]}
        rubric = test_rubric()
        rubric.update(status="provisional", approved_by=None, approved_at=None)
        rubric["reference_scores"]["unverified"] = 4
        report = build_report(c, rubric)
        self.assertEqual([d["score"] for d in report["dimensions"]], [3, 6, 3])
        self.assertIsNotNone(report["overall_score"])
        self.assertFalse(report["scope_complete"])
        self.assertTrue(all(d["failure_reasons"] for d in report["dimensions"]))
        self.assertTrue(any("不等于" in note for note in report["limitations"]))

    def test_not_applicable_excluded_and_weights_renormalized(self):
        r = build_report(complete_case(), test_rubric())
        self.assertAlmostEqual(r["overall_score"], (7*4+9*3)/7)
        self.assertEqual(r["dimensions"][2]["applicability"], "not_applicable")
        self.assertIsNone(r["dimensions"][2]["score"])

    def test_single_bad_claim_not_averaged_or_overall_capped(self):
        c = complete_case()
        check = c["claim_checks"][0]
        check["verdict"] = "contradicted"
        check["evidence"][0]["evidence_role"] = "contradicts"
        context = dict(dataset="A", metric="accuracy", baseline="B", conditions="same split")
        check["context_comparison"] = {"claim_context": context, "evidence_context": dict(context)}
        rubric = test_rubric()
        rubric["weights"] = dict(zip(DIMENSIONS, (1,9,1)))
        r = build_report(c, rubric)
        self.assertAlmostEqual(r["overall_score"], 8.2)
        self.assertEqual(r["overall_category"], "TEST-3")
        self.assertTrue(any(a["kind"] == "low_dimension_score" for a in r["alerts"]))

    def test_dedup_group_count_and_minimum(self):
        c = complete_case()
        claim = copy.deepcopy(c["claims"][0]); claim["credibility_claim_id"] = "claim-duplicate"
        c["claims"].append(claim)
        check = copy.deepcopy(c["claim_checks"][0]); check.update(claim_check_id="cc-duplicate", credibility_claim_id="claim-duplicate", verdict="evidence_not_found", evidence=[])
        c["claim_checks"].append(check)
        r = build_report(c, test_rubric())
        self.assertEqual(r["dimensions"][0]["coverage"]["deduplicated_claim_count"],1)
        self.assertEqual(r["dimensions"][0]["score"],4)

    def test_query_failure_blocks_total_and_is_not_zero(self):
        c = complete_case(); check = c["reference_checks"][0]
        check.update(status="query_failed", technical_error="HTTP 429", candidates=[], selected_candidate_id=None)
        r = build_report(c, test_rubric())
        self.assertIsNone(r["overall_score"])
        self.assertIsNone(r["dimensions"][1]["score"])
        self.assertEqual(r["reference_coverage"]["query_failed_count"],1)
        self.assertEqual(r["reference_coverage"]["queried_count"],2)

    def test_parse_failed_not_counted_as_queried(self):
        c = case(); c["references"][0]["parse_status"]="failed"; c["reference_checks"][0]["status"]="parse_failed"
        r = build_report(c,test_rubric())
        self.assertEqual(r["reference_coverage"]["parsed_count"],1)
        self.assertEqual(r["reference_coverage"]["queried_count"],0)

    def test_omitted_reference_state_rejected(self):
        c=case();c["reference_checks"].pop()
        with self.assertRaises(ValidationError):validate_case(c)

    def test_invented_page_rejected(self):
        c=case();c["claim_checks"][0]["evidence"][0]["page_number"]=3
        with self.assertRaises(ValidationError):validate_case(c)

    def test_invented_chunk_rejected(self):
        c=case();c["claim_checks"][0]["evidence"][0]["chunk_id"]="nonexistent"
        with self.assertRaises(ValidationError):validate_case(c)

    def test_invented_excerpt_rejected(self):
        c=case();c["claim_checks"][0]["evidence"][0]["excerpt"]="fabricated evidence"
        with self.assertRaises(ValidationError):validate_case(c)

    def test_different_conditions_cannot_be_contradiction(self):
        c=case();check=c["claim_checks"][0];check["verdict"]="contradicted";check["evidence"][0]["evidence_role"]="contradicts"
        ctx=dict(dataset="A",metric="accuracy",baseline="B",conditions="same split")
        check["context_comparison"]={"claim_context":ctx,"evidence_context":{**ctx,"dataset":"C"}}
        with self.assertRaises(ValidationError):validate_case(c)

    def test_not_found_requires_recorded_search_scope(self):
        c=case();c["claim_checks"][0].update(verdict="evidence_not_found",evidence=[],searched_scope=[])
        with self.assertRaises(ValidationError):validate_case(c)

    def test_partial_scope_blocks_total_with_approved_rubric(self):
        c=complete_case();c["scope_complete"]=False
        r=build_report(c,test_rubric());self.assertIsNone(r["overall_score"])

    def test_empty_claims_do_not_award_perfect_score(self):
        c=complete_case();c["claims"]=[];c["claim_checks"]=[]
        r=build_report(c,test_rubric());self.assertIsNone(r["overall_score"])

    def test_expected_reference_count_unknown_blocks_score(self):
        c=complete_case();c["reference_scope"]["expected_count"]=None
        r=build_report(c,test_rubric());self.assertIsNone(r["dimensions"][1]["score"])

    def test_name_similarity_is_not_strong_association(self):
        c=complete_case();repo=confirmed_repo(c);repo["association_evidence"][0]["kind"]="name_similarity"
        with self.assertRaises(ValidationError):validate_case(c)

    def test_missing_feedback_query_is_unable_to_assess(self):
        c=complete_case();confirmed_repo(c);c["code_scope"]["repository_checks"]=[]
        r=build_report(c,test_rubric());self.assertIsNone(r["overall_score"])
        self.assertNotIn("no_relevant_public_feedback",r["code_states"])

    def test_public_code_and_no_feedback_is_not_reproduction_success(self):
        c=complete_case();confirmed_repo(c)
        r=build_report(c,test_rubric());self.assertIn("no_relevant_public_feedback",r["code_states"])
        self.assertNotIn("independent_success_report",r["code_states"])
        self.assertLess(r["dimensions"][2]["score"],10)

    def test_failed_source_search_cannot_be_no_public_code_found(self):
        c=complete_case()
        c['code_scope']={'applicability':'applicable','justification':None,'searched_sources':[{'source_url':'https://example.org/paper','checked_at':T,'status':'query_failed'}],'query_completed':True,'repository_checks':[]}
        r=build_report(c,test_rubric())
        self.assertIn('query_failed',r['code_states']);self.assertNotIn('no_public_code_found',r['code_states']);self.assertIsNone(r['overall_score'])

    def test_imported_report_cannot_turn_unable_into_zero(self):
        r=build_report(case(),test_rubric());r['dimensions'][0]['score']=0
        with self.assertRaises(ValidationError):validate_report(r)

    def test_imported_report_cannot_hide_reference_query_failure(self):
        r=build_report(case(),test_rubric());r['reference_coverage']['queried_count']=2
        with self.assertRaises(ValidationError):validate_report(r)

    def test_imported_report_cannot_change_weighted_total(self):
        r=build_report(complete_case(),test_rubric());r['overall_score']=10
        with self.assertRaises(ValidationError):validate_report(r)

    def test_model_candidate_is_not_confirmed_or_scored(self):
        c=complete_case();c['claim_checks'][0].update(review_status='candidate',check_source='model')
        r=build_report(c,test_rubric())
        self.assertEqual(r['findings'][0]['status'],'pending_verification');self.assertIsNone(r['overall_score'])

    def test_unreviewed_success_report_rejected(self):
        c=complete_case();confirmed_repo(c);s=signal();s.update(signal_type="independent_success_report",review_status="candidate");c["reproduction_signals"]=[s]
        with self.assertRaises(ValidationError):validate_case(c)

    def test_closed_issue_does_not_mean_fixed(self):
        c=complete_case();confirmed_repo(c);s=signal();s["resolution_status"]="fixed";c["reproduction_signals"]=[s]
        with self.assertRaises(ValidationError):validate_case(c)

    def test_synthetic_gold_rejected(self):
        c=case();c["review_status"]="gold";c["review_record"]={"annotators":["A","B"],"adjudicator":"C"}
        with self.assertRaises(ValidationError):validate_case(c)

    def test_invalid_scoring_weights_and_code_cap(self):
        for key,value in [('weights',{d:0 for d in DIMENSIONS}),('code_score_cap',10),('categories',[])]:
            r=test_rubric();r[key]=value
            with self.assertRaises(ValidationError):validate_rubric(r)

    def test_pdf_version_changes_rejected(self):
        c=case();c["claims"][0]["paper_version_id"]="another-version"
        with self.assertRaises(ValidationError):validate_case(c)

    def test_rubric_revision_changes_assessment_identity(self):
        r=test_rubric();a=build_report(complete_case(),r);r["version"]="another-test-version";b=build_report(complete_case(),r)
        self.assertNotEqual(a["credibility_assessment_id"],b["credibility_assessment_id"])

    def test_html_escapes_excerpt_and_keeps_null_as_dash(self):
        c=case();r=build_report(c,test_rubric());r["findings"][0]["description"]='<script>alert("x")</script>'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.html';render_html(r,path);content=path.read_text()
            self.assertNotIn('<script>alert',content);self.assertIn('&lt;script&gt;',content);self.assertIn('<strong>—</strong>',content)

    def test_sql_export_escapes_quotes_and_preserves_source_hash(self):
        c=case();c["case_id"]="case'quote";r=build_report(c,test_rubric())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'seed.sql';export_seed(c,r,path);content=path.read_text()
            self.assertIn("case''quote@",content);self.assertIn('BEGIN;',content);self.assertIn('COMMIT;',content)
            self.assertNotIn('INSERT INTO public.',content)


class ConnectorTests(unittest.TestCase):
    def test_external_failure_keeps_technical_state(self):
        check=verify_reference(case()['references'][0],FakeClient(error='HTTP 429'))
        self.assertEqual(check['status'],'query_failed');self.assertTrue(check['query_attempted'])

    def test_no_metadata_is_unverified(self):
        check=verify_reference(case()['references'][0],FakeClient({'message':{'items':[]}}))
        self.assertEqual(check['status'],'unverified')

    def test_wrong_title_under_same_doi_is_metadata_conflict(self):
        check=verify_reference(case()['references'][0],FakeClient({'message':{'DOI':'10.9999/synthetic','title':['Unrelated research'],'author':[{'family':'Doe'}],'published':{'date-parts':[[2024]]}}}))
        self.assertEqual(check['status'],'metadata_conflict');self.assertIn('title',check['conflicting_fields'])

    def test_ambiguous_title_candidates_are_not_forced(self):
        ref=case()['references'][1]
        item={'DOI':'10.9999/one','title':[ref['parsed_title']],'author':[{'family':'Roe'}],'published':{'date-parts':[[2025]]}}
        other={**item,'DOI':'10.9999/two'}
        check=verify_reference(ref,FakeClient({'message':{'items':[item,other]}}))
        self.assertEqual(check['status'],'unverified');self.assertEqual(len(check['candidates']),2)

    def test_preprint_publication_year_ambiguity_is_not_conflict(self):
        ref=case()['references'][0]
        item={'DOI':ref['parsed_doi'],'title':[ref['parsed_title']],'author':[{'family':'Doe'}],'published':{'date-parts':[[2025]]}}
        check=verify_reference(ref,FakeClient({'message':item}))
        self.assertEqual(check['status'],'unverified')

    def test_parse_failed_skips_service_entirely(self):
        ref=case()['references'][0];ref['parse_status']='failed';client=FakeClient()
        check=verify_reference(ref,client);self.assertEqual(check['status'],'parse_failed');self.assertEqual(client.urls,[])

    def test_comment_only_reproduction_discussion_is_collected(self):
        c=complete_case();repo=confirmed_repo(c)
        def response(url):
            if '/comments?' in url:return [{'html_url':'https://github.com/example/TEST_ONLY/issues/1#issuecomment-2','body':'Cannot reproduce the paper results.','user':{'login':'outsider'}},{'html_url':'https://github.com/example/TEST_ONLY/issues/1#issuecomment-3','body':'We are checking it.','user':{'login':'paper-author'}}]
            if '/issues?' in url:return [{'number':1,'html_url':'https://github.com/example/TEST_ONLY/issues/1','title':'Question','body':'Help please','comments':2,'state':'closed','user':{'login':'outsider'}},{'number':2,'pull_request':{},'title':'reproduce'}]
            return {'private':False}
        signals,scope=collect_reproduction_signals(repo,FakeClient(response),author_logins=['paper-author'])
        self.assertEqual(scope['issues_checked'],1);self.assertEqual(scope['comments_checked'],2)
        self.assertEqual(signals[0]['signal_type'],'discussion_inconclusive');self.assertEqual(signals[0]['resolution_status'],'closed');self.assertIn('checking',signals[0]['author_response'])

    def test_pagination_limit_is_incomplete_not_no_feedback(self):
        c=complete_case();repo=confirmed_repo(c)
        client=FakeClient(lambda url:[{'pull_request':{},'number':i} for i in range(100)] if '/issues?' in url else {'private':False})
        signals,scope=collect_reproduction_signals(repo,client,max_pages=1)
        self.assertTrue(scope['truncated']);self.assertFalse(scope['completed']);self.assertEqual(signals,[])

    def test_api_retry_cache_and_minimal_payload(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Opener:
            calls=0
            def open(self,request,timeout):
                self.calls+=1
                if self.calls==1:raise HTTPError(request.full_url,429,'rate limited',{'Retry-After':'0'},None)
                return Response(json.dumps({'message':{'DOI':'10.9999/test','title':['A'],'abstract':'do not cache copyrighted abstract'}}).encode())
        with tempfile.TemporaryDirectory() as tmp:
            opener=Opener();client=PublicJsonClient(tmp,opener=opener,sleep=lambda _:None,interval=0)
            first=client.get('https://api.crossref.org/works/10.9999%2Ftest');second=client.get('https://api.crossref.org/works/10.9999%2Ftest')
            self.assertIsNone(first.error);self.assertTrue(second.from_cache);self.assertEqual(opener.calls,2)
            self.assertNotIn('abstract',first.data['message']);self.assertIsNotNone(first.response_sha256)

    def test_api_outside_allowlist_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):PublicJsonClient(tmp).get('http://localhost/internal')

    def test_cross_page_numbered_reference_preserves_pages(self):
        c=case();c['document']['sections'][2]['page_start']=3
        c['document']['chunks'][-1]['page_number']=3;c['document']['chunks'][-1]['text']='[1] Doe. Long title'
        c['document']['chunks'].append({'chunk_id':'ref-continuation','section_id':'references','page_number':4,'sequence_index':3,'text':'continued. doi:10.9999/test\n[2] Roe. Another item.'})
        result=extract_references(c)
        self.assertEqual(result['references'][0]['page_end'],4);self.assertFalse(result['reference_scope']['extraction_complete'])

    def test_claim_extractor_emits_candidates_only(self):
        claims=extract_claim_candidates(case());self.assertTrue(claims);self.assertTrue(all(c['review_status']=='candidate' and not c['verifiability'] for c in claims))


if __name__ == '__main__':
    unittest.main()
