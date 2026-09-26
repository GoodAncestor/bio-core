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
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([node, '-e', script, page.as_uri()], check=True, capture_output=True, text=True, timeout=45)
