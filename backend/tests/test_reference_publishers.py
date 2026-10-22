from __future__ import annotations

import pytest

from app.credibility import reference_publishers as provider
from app.credibility.models import CredibilityBibliographicMetadata
from app.credibility.reference_publisher_http import PublisherFetch
from app.credibility.snapshots import capture_snapshot, read_snapshot

TITLE = 'A General Theory of Structured Prediction'
AUTHORS = ['Ada Example', 'Milo Researcher']


def citation(**changes):
    return CredibilityBibliographicMetadata(**{
        'title': TITLE, 'authors': AUTHORS, 'year': 2023, 'doi': None, 'arxiv_id': None, **changes,
    })


def paper(title=TITLE, authors=AUTHORS, year=2023, *, visible=True):
    meta = f'<meta name="citation_title" content="{title}">'
    meta += ''.join(f'<meta name="citation_author" content="{author}">' for author in authors)
    if year:
        meta += f'<meta name="citation_publication_date" content="{year}/07/01">'
    text = f'<h1>{title}</h1><p>{", ".join(authors)}</p>' if visible else '<p>A search result</p>'
    return f'<html><head>{meta}</head><body>{text}</body></html>'.encode()


def mock_pages(monkeypatch, pages):
    calls = []

    def fetch(url):
        calls.append(url)
        assert url in pages, f'Unexpected URL {url}'
        raw = pages[url]
        status, code = ('ok', 200) if raw is not None else ('not_found', 404)
        ref = capture_snapshot(kind='reference_official_publication', raw=raw, requested_url=url,
                               final_url=url, retrieved_at='2026-10-19T00:00:00Z', fetch_status=status,
                               http_status=code)
        return PublisherFetch(url, url, status, code, '2026-10-19T00:00:00Z', False, ref, raw, 'text/html')

    monkeypatch.setattr(provider, 'fetch_official', fetch)
    return calls


def test_original_official_url_confirms_all_authors_title_year(monkeypatch):
    url = 'https://aclanthology.org/2023.example.1'
    calls = mock_pages(monkeypatch, {url: paper()})
    result = provider.resolve_publishers(citation(), raw_text=f'{TITLE}. URL {url}.')
    assert len(result.confirmations) == 1 and calls == [url]
    proof = read_snapshot(result.confirmations[0].snapshot_ref)
    assert proof.raw == paper() and proof.normalized_json['metadata']['authors'] == AUTHORS
    assert proof.manifest['parent_ref'] == result.observations[0].snapshot_ref


@pytest.mark.parametrize('changes,reason', [
    ({'title': TITLE + ': Another Perspective'}, 'version_evidence_missing'),
    ({'authors': ['Ada Example', 'Someone Different']}, 'author_mismatch'),
    ({'authors': ['Ada Example']}, 'author_mismatch'),
    ({'year': 2024}, 'year_mismatch'), ({'year': None}, 'insufficient_metadata'),
    ({'visible': False}, 'insufficient_metadata'),
])
def test_similar_official_page_is_not_automatically_identity(monkeypatch, changes, reason):
    url = 'https://aclanthology.org/2023.example.1'
    mock_pages(monkeypatch, {url: paper(**changes)})
    result = provider.resolve_publishers(citation(), raw_text=url)
    assert not result.confirmations and result.reason_code == reason


def test_only_explicit_et_al_allows_author_subset_and_checks_every_listed_author(monkeypatch):
    url = 'https://aclanthology.org/2023.example.1'
    mock_pages(monkeypatch, {url: paper(authors=AUTHORS + ['Nora Scholar'])})
    assert not provider.resolve_publishers(citation(), raw_text=url).confirmations
    assert provider.resolve_publishers(citation(), raw_text=f'et al. {url}').confirmations
    changed = citation(authors=['Ada Example', 'Wrong Writer'])
    assert not provider.resolve_publishers(changed, raw_text=f'et al. {url}').confirmations


def test_missing_cited_year_is_explicit_and_never_invented(monkeypatch):
    url = 'https://aclanthology.org/2023.example.1'
    mock_pages(monkeypatch, {url: paper()})
    result = provider.resolve_publishers(citation(year=None), raw_text=url)
    assert result.confirmations and 'no year' in result.reason
    assert result.confirmations[0].metadata.year == 2023


def test_pmlr_volume_discovers_then_checks_real_page(monkeypatch):
    index = 'https://proceedings.mlr.press/v217/'
    url = 'https://proceedings.mlr.press/v217/example23a.html'
    calls = mock_pages(monkeypatch, {index: f'<html><a href="example23a.html">{TITLE}</a></html>'.encode(),
                                    url: paper()})
    result = provider.resolve_publishers(citation(), raw_text='PMLR, volume 217, 2023.')
    assert result.confirmations and calls == [index, url] and len(result.observations) == 2


def test_pmlr_main_index_discovers_conference_and_year_without_fixed_mapping(monkeypatch):
    base = 'https://proceedings.mlr.press/'
    volume = base + 'v222/'
    url = volume + 'example23a.html'
    calls = mock_pages(monkeypatch, {
        base: b'<html><a href="v222/">International Conference on Machine Learning 2023</a>'
              b'<a href="v999/">International Conference on Machine Learning 2022</a></html>',
        volume: f'<html><a href="example23a.html">{TITLE}</a></html>'.encode(), url: paper(),
    })
    result = provider.resolve_publishers(citation(), raw_text='International Conference on Machine Learning, 2023.')
    assert result.confirmations and calls == [base, volume, url]


def test_directory_title_alone_never_confirms(monkeypatch):
    index = 'https://proceedings.mlr.press/v217/'
    calls = mock_pages(monkeypatch, {index: f'<html><a href="https://evil.test/paper">{TITLE}</a></html>'.encode()})
    result = provider.resolve_publishers(citation(), raw_text='PMLR, volume 217, 2023.')
    assert not result.confirmations and calls == [index]


def test_jmlr_and_neurips_use_generic_volume_year_indexes(monkeypatch):
    jmlr = 'https://jmlr.org/papers/v22/'
    nips = 'https://papers.nips.cc/paper_files/paper/2023'
    pages = {
        jmlr: f'<html><a href="theory.html">{TITLE}</a></html>'.encode(),
        'https://jmlr.org/papers/v22/theory.html': paper(),
        nips: f'<html><a href="/paper_files/paper/2023/hash/theory-Abstract.html">{TITLE}</a></html>'.encode(),
        'https://papers.nips.cc/paper_files/paper/2023/hash/theory-Abstract.html': paper(),
    }
    calls = mock_pages(monkeypatch, pages)
    assert provider.resolve_publishers(citation(), raw_text='JMLR, 22(4), 2023.').confirmations
    assert provider.resolve_publishers(citation(),
                                       raw_text='Neural Information Processing Systems, 2023.').confirmations
    assert len(calls) == 4


def test_crossref_resource_only_used_for_same_title_and_complete_authors(monkeypatch):
    url = 'https://jmlr.org/papers/v22/theory.html'
    calls = mock_pages(monkeypatch, {url: paper()})
    payload = {'message': {'items': [{'title': [TITLE], 'author': [
        {'given': 'Ada', 'family': 'Example'}, {'given': 'Milo', 'family': 'Researcher'},
    ], 'resource': {'primary': {'URL': url}}}]}}
    ref = capture_snapshot(kind='crossref', normalized_json=payload)
    assert provider.resolve_publishers(citation(), raw_text='', crossref_snapshot_ref=ref).confirmations
    assert calls == [url]
    assert not provider.resolve_publishers(citation(authors=['Ada Example']), raw_text='',
                                          crossref_snapshot_ref=ref).confirmations


def test_no_title_or_authors_never_sends_http(monkeypatch):
    mock_pages(monkeypatch, {})
    assert not provider.resolve_publishers(citation(title=None), raw_text='JMLR 22').confirmations
    assert not provider.resolve_publishers(citation(authors=[]), raw_text='JMLR 22').confirmations


def test_wrapped_original_acl_url_is_repaired():
    assert provider._urls('URL https://aclanthology.\norg/2023.example.1.') == ['https://aclanthology.org/2023.example.1']
    assert provider._urls('URL http://www.aclweb.org/anthology/\n2023.example.1.') == [
        'https://www.aclweb.org/anthology/2023.example.1']


def test_transport_failure_is_distinct_from_completed_search(monkeypatch):
    url = 'https://aclanthology.org/2023.example.1'
    mock_pages(monkeypatch, {url: None})
    result = provider.resolve_publishers(citation(), raw_text=url)
    assert not result.confirmations and result.reason_code == 'source_fetch_failed'
    assert result.observations[0].fetch_status == 'not_found'


def test_html_scripts_cannot_supply_visible_identity():
    page = provider.parse_publication_html(
        b'<html><script>Secret Title Ada Example</script><body>Only text</body></html>')
    assert page.text == 'Only text'


def test_real_pmlr_markup_discovers_volume_then_title_outside_abs_anchor(monkeypatch):
    base = 'https://proceedings.mlr.press/'
    volume = base + 'v222'
    url = volume + '/example23a.html'
    calls = mock_pages(monkeypatch, {
        base: b'<html><li><a href="v222"><b>Volume 222</b></a> Proceedings of ICML 2023</li></html>',
        volume: (f'<html><h3><a href="#theory">{TITLE}</a></h3><div class="paper">'
                 f'<p class="title">{TITLE}</p><p class="details">Ada Example, Milo Researcher</p>'
                 '<p class="links">[<a href="v222/example23a.html">abs</a>]</p></div></html>').encode(),
        'https://proceedings.mlr.press/v222/example23a.html': paper(),
    })
    result = provider.resolve_publishers(citation(), raw_text='ICML 2023')
    assert result.confirmations and calls == [base, volume, url]


def test_real_jmlr_markup_title_dt_and_abs_dd(monkeypatch):
    index = 'https://jmlr.org/papers/v22/'
    url = index + 'example.html'
    mock_pages(monkeypatch, {index: (f'<html><dl><dt>{TITLE}</dt><dd><b>Ada Example, Milo Researcher</b>'
                                    '<br>[<a href="example.html">abs</a>]</dd></dl></html>').encode(), url: paper()})
    assert provider.resolve_publishers(citation(), raw_text='JMLR 22, 2023').confirmations


def test_family_first_metadata_matches_visible_given_family_names(monkeypatch):
    url = 'https://proceedings.mlr.press/v217/test.html'
    body = paper().replace(b'content="Ada Example"', b'content="Example, Ada"')
    body = body.replace(b'content="Milo Researcher"', b'content="Researcher, Milo"')
    mock_pages(monkeypatch, {url: body})
    result = provider.resolve_publishers(citation(), raw_text=url)
    assert result.confirmations and result.confirmations[0].metadata.authors == AUTHORS


def test_same_book_title_cannot_hide_different_editions(monkeypatch):
    url = 'https://www.cambridge.org/book/example'
    mock_pages(monkeypatch, {url: paper(title=TITLE + ', 3rd edition')})
    result = provider.resolve_publishers(citation(title=TITLE + ', 2nd edition'), raw_text=url)
    assert not result.confirmations and result.reason_code == 'version_evidence_missing'


@pytest.mark.parametrize('document_title,xmp_titles,expected', [
    (TITLE, None, True),
    (None, {'x-default': TITLE}, True),
    (None, None, False),
    ('A Different Document', None, False),
    ('A Different Document', {'x-default': TITLE}, False),
])
def test_pdf_independent_title_is_required_not_a_first_page_mention(monkeypatch, document_title, xmp_titles, expected):
    from types import SimpleNamespace

    import pypdf

    # All cases have the cited title, full author list and cited year visible.
    # For the wrong-document case this title is a related-work mention, which
    # must not establish that the PDF itself is the cited work.
    text = f'A Different Document by {", ".join(AUTHORS)}. 2023. Related work: {TITLE}.'
    reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: text)],
        metadata=SimpleNamespace(title=document_title, author='; '.join(AUTHORS)),
        xmp_metadata=SimpleNamespace(dc_title=xmp_titles) if xmp_titles else None,
    )
    monkeypatch.setattr(pypdf, 'PdfReader', lambda *args, **kwargs: reader)
    fetch = PublisherFetch('https://cdn.openai.com/report.pdf', 'https://cdn.openai.com/report.pdf', 'ok',
                           200, '2026-10-19T00:00:00Z', False, '', b'%PDF-fake', 'application/pdf')
    actual, reason = provider._pdf_metadata(fetch, citation(), '')
    assert (actual is not None) is expected
    if expected:
        assert actual.title == TITLE
    else:
        assert reason in {'insufficient_metadata', 'version_evidence_missing'}


def test_pdf_metadata_title_needs_visible_first_page_title(monkeypatch):
    from types import SimpleNamespace

    import pypdf

    reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: f'Unrelated report. {", ".join(AUTHORS)}. 2023.')],
        metadata=SimpleNamespace(title=TITLE, author='; '.join(AUTHORS)), xmp_metadata=None,
    )
    monkeypatch.setattr(pypdf, 'PdfReader', lambda *args, **kwargs: reader)
    fetch = PublisherFetch('https://cdn.openai.com/report.pdf', 'https://cdn.openai.com/report.pdf', 'ok',
                           200, '2026-10-19T00:00:00Z', False, '', b'%PDF-fake', 'application/pdf')
    actual, reason = provider._pdf_metadata(fetch, citation(), '')
    assert actual is None and reason == 'version_evidence_missing'
