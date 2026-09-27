"""Predictions remain discoverable across card formats and partial analyses."""
import json
import shutil
import subprocess

import pytest

from biocore.providers.base import Finding, Tier, Category, Interpretation, ProviderStatus, Health
from biocore.report.render import render_html


def finding(source='clinvar', marker='1-100-A-G', interpreted=False):
    return Finding(marker, source, 'Variant finding', Tier.SPECULATIVE, [Category.CLINICAL],
                   detail={'clinical_significance': 'Uncertain significance',
                           'gnomad_af': 0.002,
                           'alphagenome': {'quantile_score': 0.99, 'top_modality': 'RNA_SEQ', 'n_tracks': 8},
                           'alphamissense': {'pathogenicity': 0.91, 'class': 'likely_pathogenic',
                                            'protein_variant': 'A1G'}},
                   interpretation=Interpretation('Found', 'Meaning', 'Uncertain') if interpreted else None)


def render(fs, statuses=()):
    return render_html(fs, list(statuses), disclaimer_path='/missing')


def test_counts_unique_variants_per_model_and_no_duplicate_primary_model():
    a = finding()
    b = finding('alphamissense')
    h = render([a, b, finding(marker='2-200-G-C')])
    assert '<strong>AlphaGenome</strong>: 2 variants with predictions' in h
    assert '<strong>AlphaMissense</strong>: 2 variants with predictions' in h
    assert 'AlphaMissense, AlphaMissense' not in h


@pytest.mark.parametrize('source,interpreted', [('clinvar', False), ('clinvar', True), ('novel', True)])
def test_scores_and_named_badges_survive_all_card_formats(source, interpreted):
    h = render([finding(source, interpreted=interpreted)])
    assert 'predicted · AlphaMissense, AlphaGenome' in h
    assert 'Quantile score: 0.99' in h
    assert 'Pathogenicity score: 0.91' in h
    assert '0.002 (allele fraction, not disease risk)' in h
    assert 'does not establish whether gene activity increases or decreases' in h
    assert 'not a personal disease probability' in h
    assert 'Uncertain significance' in h


def test_standalone_missense_uses_native_keys_and_is_filterable():
    f = finding('alphamissense')
    f.detail = {'pathogenicity': 0.1, 'am_class': 'likely_benign', 'protein_variant': 'V2A',
                'uniprot_id': 'P123'}
    h = render([f])
    assert "data-predicted='1'" in h
    assert 'Pathogenicity score: 0.1; model class: likely_benign; protein change: V2A' in h
    assert 'UniProt: P123' in h
    assert '<strong>AlphaGenome</strong>: Not scored in this report' in h


def test_statuses_explain_missing_results_and_escape_notes_and_scores():
    h = render([], [ProviderStatus('alphagenome', Health.UNAVAILABLE, note='<blocked>'),
                    ProviderStatus('alphamissense', Health.OK, note='No eligible variants')])
    assert '<strong>AlphaGenome</strong>: Unavailable' in h
    assert '<strong>AlphaMissense</strong>: Not scored in this report' in h
    assert '&lt;blocked&gt;' in h
    assert "id='explore-predictions'" not in h
    f = finding()
    f.detail['alphagenome']['top_modality'] = '<script>bad</script>'
    assert '&lt;script&gt;bad&lt;/script&gt;' in render([f])


def test_explorer_actually_resets_filters_and_opens_nested_details():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to execute the report interaction')
    h = render([finding()])
    function = h.split('  function explorePredictions(){', 1)[1].split("  var explore=", 1)[0]
    script = '''
const pred={value:'',focus(){this.focused=true}}, sel={value:'robust'}, topic={value:'cancer'},
search={value:'BRCA'}, mag={value:'9'}, mod={value:'methylome'}, dir={value:'benign'},
uncarried={checked:false}, mismatch={checked:false}, moreDetails=[{open:false}],
parent={open:false}, detail={open:false,parentElement:{closest(){return parent}}};
const document={querySelectorAll(){return [detail]}};
let view='outcome'; function setView(v){view=v;}
function explorePredictions(){''' + function + '''
explorePredictions();
console.log(JSON.stringify({pred,sel,topic,search,mag,mod,dir,uncarried,mismatch,moreDetails,
                           detailOpen:detail.open,parentOpen:parent.open,view}));
'''
    result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    state = json.loads(result.stdout)
    assert state['view'] == 'site'
    assert state['pred'] == {'value': 'only', 'focused': True}
    assert state['sel']['value'] == 'robust moderate speculative unknown'
    assert all(state[k]['value'] == '' for k in ('topic', 'search', 'mod', 'dir'))
    assert state['mag']['value'] == '0'
    assert state['detailOpen'] and state['parentOpen'] and state['moreDetails'][0]['open']


def test_bounded_analysis_counts_visible_even_without_predictions():
    h = render_html([], [], disclaimer_path='/missing', scan_stats={'ai_predictions': {
        'alphagenome': {'status': 'partial', 'eligible': 7, 'scored': 0, 'failed': 2, 'skipped': 5},
        'selection': {'screened': 100, 'selected': 7, 'limit': 100, 'not_screened': 900}}})
    assert 'analysis: partial; eligible: 7; scored: 0; failed: 2; skipped: 5' in h
    assert 'Bounded quality scan: screened: 100; selected: 7; limit: 100; not screened: 900' in h
    assert 'This does not score every input variant.' in h


@pytest.mark.parametrize('url', ['/explore?variant=1-100-A-G&build=GRCh38', 'https://example.org/explore?v=1'])
def test_safe_caller_supplied_variant_explorer_links(url):
    import html
    f = finding()
    f.detail['variant_explorer_url'] = url
    h = render([f])
    assert "href='" + html.escape(url, quote=True) + "'>Explore this variant</a>" in h


@pytest.mark.parametrize('url', ['javascript:alert(1)', '//evil.org/x', '/\\evil.org/x', '/\nevil.org/x'])
def test_unsafe_explorer_links_are_rejected(url):
    f = finding()
    f.detail['variant_explorer_url'] = url
    assert 'Explore this variant' not in render([f])


def test_prediction_summary_is_visible_and_cards_fit_in_browser(tmp_path):
    """Optional real-browser regression; NODE_PATH can point at a temporary Playwright install."""
    node = shutil.which('node')
    if not node or subprocess.run([node, '-e', "require('playwright')"], capture_output=True).returncode:
        pytest.skip('Optional Playwright browser check requires Node and playwright')
    page = tmp_path / 'report.html'
    page.write_text(render([finding(), finding('novel', marker='2-200-A-C', interpreted=True)]))
    script = r'''
const {chromium}=require('playwright');
(async()=>{
const browser=await chromium.launch();
try {
for(const mode of ['light','dark'])for(const width of [390,1440]){
 const page=await browser.newPage({viewport:{width,height:900},colorScheme:mode});
 await page.goto(process.argv[1]);
 await page.locator('#explore-predictions').click({timeout:5000});
 const result=await page.evaluate(()=>({
  overflow:document.documentElement.scrollWidth>innerWidth,
  view:document.body.dataset.view, filter:document.querySelector('#predfilter').value,
  bodyWidth:document.querySelector('.finding:not(.compact)>.body').getBoundingClientRect().width,
  details:document.querySelectorAll('.prediction-details[open]').length
 }));
 if(result.overflow || result.view!=='site' || result.filter!=='only' || result.bodyWidth<150 || result.details!==2)
  throw new Error(JSON.stringify(result));
 await page.close();
}
const host=await browser.newPage();
const errors=[];host.on('pageerror',e=>errors.push(e.message));
await host.goto(process.argv[1]);
const report=await host.content();
await host.setContent('<iframe sandbox="allow-scripts" id="sandbox"></iframe>');
await host.locator('#sandbox').evaluate((f,html)=>{f.srcdoc=html},report);
const embedded=host.frameLocator('#sandbox');
await embedded.locator('#explore-predictions').click({timeout:5000});
const selected=await embedded.locator('#predfilter').inputValue();
if(errors.length || selected!=='only')throw new Error(JSON.stringify({errors,selected}));
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([node, '-e', script, page.as_uri()], check=True, capture_output=True, text=True, timeout=45)


@pytest.mark.parametrize('context,expected', [
    ('variant_lookup', 'No genome file was uploaded. This report describes a variant, not a sample genotype.'),
    ('public_ai_demo', 'Public research examples; no personal genome was uploaded.')])
def test_non_upload_context_privacy_is_accurate(context, expected):
    h = render_html([finding()], [], scan_stats={'context': context}, disclaimer_path='/missing')
    assert expected in h
    assert 'Your uploaded file is processed' not in h


def test_explicitly_unrequested_model_is_distinct_from_failure():
    h = render_html([], [ProviderStatus('alphagenome', Health.UNAVAILABLE)],
                    scan_stats={'ai_predictions': {'alphagenome': {'status': 'not_requested'}}},
                    disclaimer_path='/missing')
    assert '<strong>AlphaGenome</strong>: Not requested' in h


def test_prediction_context_and_evidence_provenance_are_visible_and_escaped():
    f = finding()
    f.detail.update({'review_status': 'criteria provided', 'gold_stars': 1,
                     'clinvar_variation_id': '17864', 'gnomad_status': 'cached_match',
                     'provenance': {'method': 'fresh API', 'verified_at': '2026-09-26',
                                    'source_url': 'https://example.org/source', 'note': '<context>'}})
    f.detail['alphagenome'].update({'biosample_name': 'HEK293', 'biosample_type': 'cell_line',
                                  'ontology_curie': 'EFO:0001182', 'variant_scorer': 'CenterMaskScorer',
                                  'gene_name': 'BRCA2', 'scored_at': '2026-09-26'})
    h = render([f])
    for text in ('Biosample: HEK293', 'Biosample type: cell line', 'Biosample ontology: EFO:0001182',
                 'Scorer: CenterMaskScorer', 'Scored gene: BRCA2', 'Scored at: 2026-09-26',
                 '1 of 4 review stars', 'criteria provided', 'Matched cached record',
                 'Method: fresh API', 'Verified at: 2026-09-26', 'Note: &lt;context&gt;',
                 'https://www.ncbi.nlm.nih.gov/clinvar/variation/17864/'):
        assert text in h
    assert 'Tissue: HEK293' not in h


@pytest.mark.parametrize('status,label', [('unavailable', 'Database unavailable'), ('no_match', 'No matching record')])
def test_missing_population_evidence_explains_reason(status, label):
    f = finding()
    f.detail.pop('gnomad_af')
    f.detail['gnomad_status'] = status
    assert 'Not provided · ' + label in render([f])


def test_unrequested_provider_is_not_labeled_failed_in_footer_or_summary():
    h = render_html([], [ProviderStatus('alphagenome', Health.UNAVAILABLE)],
                    scan_stats={'ai_predictions': {'alphagenome': {'status': 'not_requested'}}},
                    disclaimer_path='/missing')
    assert 'provider: not requested' in h
    assert '<li>alphagenome: not requested' in h
    assert 'provider: unavailable' not in h


def test_evidence_only_lookup_keeps_clinvar_evidence_without_claiming_predictions():
    f = finding('variant_lookup')
    f.detail = {'research_candidate': True, 'clinical_significance': 'Uncertain significance',
                'gold_stars': 2, 'review_status': 'criteria provided, multiple submitters',
                'conditions': ['Example condition'], 'gnomad_status': 'unavailable'}
    h = render([f])
    assert 'Variant evidence side by side' in h
    assert '2 of 4 review stars' in h
    assert 'Reported conditions: Example condition' in h
    assert 'AI predictions &amp; evidence side by side' not in h
    assert 'Not scored in this report' in h


def test_atlas_is_distinct_counted_once_and_includes_explanations():
    f = finding()
    f.detail['alphagenome_atlas'] = {
        'status': 'complete', 'source': 'local_avi', 'cache_hit': True,
        'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': 0.4999, 'quantile_score': 0.9869},
                   {'scorer': 'AVI_SCORE_FEATURE_IMPORTANCE', 'feature_name': '<RNA>', 'raw_score': -0.2}],
        'queried_at': '2026-09-26'}
    h = render([f, f])
    assert '<strong>AlphaGenome Atlas / AVI</strong>: 1 variant with predictions' in h
    assert '<strong>AlphaGenome</strong>: 1 variant with predictions' in h
    assert 'Raw score: 0.4999; Quantile: 0.9869' in h
    assert 'Feature: &lt;RNA&gt;' in h
    assert 'Source: local_avi' in h and 'Retrieval: cached result.' in h
    assert 'neither its raw score nor its quantile is a personal disease probability' in h
    assert 'not evidence of a causal disease mechanism' in h


def test_standalone_atlas_does_not_count_as_alphagenome():
    f = finding('alphagenome_atlas')
    f.detail = {'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': 0.1}]}
    h = render([f])
    assert 'predicted · AlphaGenome Atlas' in h
    assert '<strong>AlphaGenome</strong>: Not scored in this report' in h
    assert '<strong>AlphaGenome Atlas / AVI</strong>: 1 variant with predictions' in h


def test_atlas_local_fallback_shows_missing_remote_evidence_and_source():
    f = finding()
    f.detail['alphagenome_atlas'] = {'status': 'complete', 'remote_status': 'offline',
        'local_status': 'ready', 'source_url': 'https://example.org/avi',
        'provenance': 'alphagenome_atlas_local_avi', 'missing_scorers': ['AVI_SCORE_FEATURE_IMPORTANCE'],
        'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': 0.2}]}
    h = render_html([f], [], scan_stats={'ai_predictions': {'alphagenome_atlas': {
        'status': 'partial', 'local_hits': 1, 'partial': 1, 'local_status': 'ready', 'remote_status': 'offline'}}})
    assert 'Remote lookup status: offline' in h
    assert 'Local database status: ready' in h
    assert 'Scorers not available in this result: AVI_SCORE_FEATURE_IMPORTANCE' in h
    assert "href='https://example.org/avi'>Atlas data source" in h
    assert 'local hits: 1; partial: 1; local status: ready; remote status: offline' in h


def test_atlas_lay_rank_drivers_and_disclaimer_from_api_quantile():
    f = finding()
    f.detail['alphagenome_atlas'] = {'status': 'complete', 'avi_score': 0.5, 'tracks': [
        {'scorer': 'AVI_SCORE', 'raw_score': 0.5, 'quantile_score': 0.987},
        {'scorer': 'AVI_SCORE_FEATURE_IMPORTANCE', 'name': 'CACTUS_241_WAY', 'raw_score': 0.3},
        {'scorer': 'AVI_SCORE_FEATURE_IMPORTANCE', 'name': 'MERGED_SPLICING', 'raw_score': -0.1},
        {'scorer': 'AVI_SCORE_FEATURE_IMPORTANCE', 'name': 'NEW_FEATURE', 'raw_score': 0.05}]}
    h = render([f])
    assert "higher than about 98.7% of the model's reference distribution" in h
    assert 'conservation across 241 mammals (about 60% of the score)' in h
    assert 'RNA splicing (lowers the score)' in h
    assert 'new feature (about 10% of the score)' in h
    assert 'not independent confirmation' in h
    assert 'not approved for, any clinical use' in h


def test_atlas_phred_wins_and_bad_values_are_skipped():
    f = finding()
    f.detail['alphagenome_atlas'] = {'status': 'complete', 'avi_score': 0.4, 'avi_phred': 20.0,
        'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': 0.4, 'quantile_score': 0.9}]}
    h = render([f])
    assert 'top 1% of the ~9 billion possible single-letter changes Atlas scored (PHRED 20.0)' in h
    assert "model's reference distribution" not in h
    f.detail['alphagenome_atlas'] = {'status': 'complete', 'avi_phred': float('nan'),
        'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': 0.4, 'quantile_score': 1.5}]}
    h = render([f])
    assert 'Ranks in the top' not in h and "model's reference distribution" not in h


def test_rare_high_impact_needs_both_numbers():
    from biocore.report.render import atlas_rare_high_impact
    def f(marker, phred=None, af=None):
        x = finding()
        x.marker = marker
        x.detail = {'alphagenome_atlas': {'avi_phred': phred, 'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': .5}]} if phred is not None else {},
                    'gnomad': {'af': af} if af is not None else {}}
        return x
    fs = [f('1-1-A-G', 25, 0.0001), f('1-2-A-G', 25, 0.01), f('1-3-A-G', 15, 0.0001),
          f('1-4-A-G', 30), f('1-5-A-G', None, 0.0001), f('1-6-A-G', 21, 0.0)]
    rows = atlas_rare_high_impact(fs)
    assert [r['marker'] for r in rows] == ['1-1-A-G', '1-6-A-G']
    h = render(fs)
    assert 'Rare and high-impact (research only)' in h
    assert "href='/explore?variant=1-1-A-G'" in h and 'top 0.32% by Atlas' in h


def test_splicing_row_renders_with_terms():
    f = finding()
    f.detail['alphagenome_atlas_splicing'] = {'splicing_score': 0.8, 'score_explanation': '0 means no predicted change.'}
    h = render([f])
    assert 'AlphaGenome Atlas · splicing' in h and 'Merged splicing score: 0.8.' in h
    assert 'Non-commercial use only' in h
