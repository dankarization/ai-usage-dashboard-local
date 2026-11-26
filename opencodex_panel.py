"""Optional independent panel: never merges into dashboard history/overview."""
import opencodex_usage as usage

SECTION = '''
<section aria-labelledby="h-opencodex" id="opencodex-panel" hidden>
  <div class="sec-head"><h2 id="h-opencodex">OpenCodex на Macbook</h2></div>
  <div class="panel">
    <p class="note">Independent source · not added to overview or shared history · billing unknown</p>
    <label>Saved UTC window <select id="opencodex-days" aria-label="OpenCodex saved UTC window">
      <option value="0">All saved history</option><option value="1">1 day</option><option value="7" selected>7 days</option><option value="30">30 days</option>
    </select></label>
    <div id="opencodex-chart" class="chart-wrap"></div>
    <div id="opencodex-models" class="scroll"></div>
    <p id="opencodex-note" class="note">No saved observations loaded.</p>
  </div>
</section>
'''

SCRIPT = r'''
/* Independent cache-only observations; never add to historyRows/mergeModels. */
function renderOpenCodex(data) {
  var chart = $('opencodex-chart'), root = $('opencodex-models');
  chart.replaceChildren(); root.replaceChildren();
  var daily = data.daily || [], models = data.models || [];
  if (daily.length) {
    var s = svgEl('svg', {viewBox:'0 0 720 180', role:'img', 'aria-label':'Saved source model tokens by UTC day; gaps are unknown'}, chart);
    var max = Math.max.apply(null, [1].concat(daily.map(function(d) { return d.total_tokens || 0; })));
    var step = 720 / daily.length;
    daily.forEach(function(day, i) {
      if (day.total_tokens === null) return; // No fabricated zero for missing dates.
      var bottom = 160;
      (day.models || []).forEach(function(row) {
        var index = models.findIndex(function(m) { return m.provider === row.provider && m.model === row.model; });
        var color = ['#3b82f6','#10b981','#f59e0b','#ec4899','#8b5cf6','#06b6d4'][Math.max(0,index) % 6];
        if (row.total_tokens === null) return;
        var height = row.total_tokens / max * 140;
        bottom -= height;
        var rect = svgEl('rect', {x:i * step + 2, y:bottom, width:Math.max(1,step-4), height:height, fill:color}, s);
        var title = svgEl('title', {}, rect);
        title.textContent = day.date + ' UTC · ' + row.provider + ' / ' + row.model + ' · ' + tokens(row.total_tokens) + ' tokens · account unknown';
      });
      var label = svgEl('text', {x:i*step+2, y:177, fill:'currentColor', 'font-size':9}, s);
      label.textContent = day.date.slice(5);
    });
  }
  var table = node('table', undefined, root), head = node('tr', undefined, node('thead', undefined, table));
  ['Provider label', 'Model label', 'Account', 'Known tokens', 'Input (includes cache)', 'Output', 'Cache reads', 'Cache writes', 'Source API list-price estimate (not spend)'].forEach(function(t) { node('th', t, head); });
  var body = node('tbody', undefined, table);
  models.forEach(function(row) {
    var tr = node('tr', undefined, body);
    [row.provider, row.model, 'Unknown (no model join)', row.total_tokens === null ? 'Unknown' : tokens(row.total_tokens),
     ...['input_tokens','output_tokens','cache_read_tokens','cache_creation_tokens'].map(function(k) { return typeof row[k] === 'number' ? tokens(row[k]) : 'Unknown'; }),
     typeof row.estimated_cost_usd === 'number' ? '~$' + row.estimated_cost_usd.toFixed(4) : 'Unknown'].forEach(function(t) { node('td', t, tr); });
  });
  $('opencodex-note').textContent = (data.status === 'saved' ? 'Saved observations' : 'No saved observations') +
    (data.start_date ? ' · ' + data.start_date + ' – ' + data.end_date + ' UTC' : '') +
    (typeof data.measurement_coverage === 'number' ? ' · measurement coverage ' + (data.measurement_coverage * 100).toFixed(1) + '%' : '') +
    (typeof data.requests === 'number' ? ' · ' + data.requests + ' requests' : '') +
    (data.reported_counters ? ' · ' + data.reported_counters.reportedRequests + ' reported / ' +
      data.reported_counters.estimatedRequests + ' estimated / ' + data.reported_counters.unreportedRequests +
      ' unreported / ' + data.reported_counters.unsupportedRequests + ' unsupported' : '') +
    (typeof data.estimated_cost_usd === 'number' ? ' · source API list-price estimate ~$' + data.estimated_cost_usd.toFixed(4) + ' (not spend; excludes unpriced/unmetered usage)' : '') +
    ' · ' + (data.limitations || 'Collection unavailable; prior observations are retained.');
}
var openCodexTicket = 0, shownObservationSource = null, savedSourceObservations = {};
var sourceLabels = {openclaw:'OpenClaw', hermes:'Hermes', opencodex:'OpenCodex на Macbook'};
function sourceSelection() { return $('history-source').value; }
function updateSourceVisibility() {
  var selected = sourceSelection(), independent = selected !== 'all';
  $('history').hidden = independent;
  $('model-table').closest('.history-table').hidden = independent;
  $('opencodex-panel').hidden = !independent;
  $('h-opencodex').textContent = sourceLabels[selected] || 'OpenCodex на Macbook';
  $('opencodex-days').disabled = selected !== 'opencodex';
}
function agentObservation(source) {
  // Read only the already-loaded canonical period. Never collect or alter it.
  var part = (periodStates[selectedDays] || {}).parts || {}, data = part[source];
  if (!data) return {status:'unavailable', limitations:'No saved source observations loaded for this period.'};
  var byDate = new Map(), rows = [];
  (data.models || []).filter(function(r) { return r.source === source; }).forEach(function(r) {
    rows.push({provider:r.provider || 'unknown', model:r.model, total_tokens:r.totals && typeof r.totals.total === 'number' ? r.totals.total : null, estimated_cost_usd:null});
    (r.daily || []).forEach(function(d) {
      var day = byDate.get(d.date) || {date:d.date, total_tokens:null, models:[]};
      day.models.push({provider:r.provider || 'unknown', model:r.model, total_tokens:typeof d.total === 'number' ? d.total : null});
      if (typeof d.total === 'number') day.total_tokens = (day.total_tokens || 0) + d.total;
      byDate.set(d.date, day);
    });
  });
  return {status:'saved', models:rows, daily:Array.from(byDate.values()).sort(function(a,b) {return a.date.localeCompare(b.date);}),
    start_date:(data.meta || {}).start_date, end_date:(data.meta || {}).end_date,
    limitations:'Source-only observations; not an account split and not additional overview usage. Missing measurements remain unknown. Account unknown.'};
}
function loadOpenCodex() {
  var ticket = ++openCodexTicket;
  updateSourceVisibility();
  var selected = sourceSelection();
  if (selected === 'all') return;
  if (shownObservationSource !== selected) {
    shownObservationSource = selected;
    renderOpenCodex(savedSourceObservations[selected] || {status:'unavailable', limitations:'No saved observations loaded for this source.'});
  }
  if (selected !== 'opencodex') {
    var observation = agentObservation(selected);
    if (observation.status === 'saved') savedSourceObservations[selected] = observation;
    renderOpenCodex(savedSourceObservations[selected] || observation); return;
  }
  json('/api/v1/opencodex?days=' + $('opencodex-days').value).then(function(data) {
    if (ticket !== openCodexTicket) return;
    if (data.status === 'saved') savedSourceObservations[selected] = data;
    renderOpenCodex(savedSourceObservations[selected] || data);
  }).catch(function() {
    // Preserve the rendered observation, too. No stale badge or reset to zero.
    if (ticket === openCodexTicket) $('opencodex-note').textContent = 'Saved observations retained; cache read unavailable.';
  });
}
$('history-source').addEventListener('change', loadOpenCodex);
var canonicalRenderPeriod = renderPeriod;
renderPeriod = function(models, quotas) {
  canonicalRenderPeriod(models, quotas);
  if (sourceSelection() === 'openclaw' || sourceSelection() === 'hermes') loadOpenCodex();
};
$('opencodex-days').addEventListener('change', loadOpenCodex);
$('refresh').addEventListener('click', loadOpenCodex);
loadOpenCodex();
setInterval(loadOpenCodex, 5 * 60000);
'''


def extend(html):
    if not usage.enabled():
        return html
    selector = '<label>Source <select id="history-source" aria-label="Usage history source">' + (
        '<option value="all">Canonical overview (unchanged)</option>'
        '<option value="openclaw">OpenClaw</option><option value="hermes">Hermes</option>'
        '<option value="opencodex">OpenCodex на Macbook</option></select></label>')
    anchor = '<div id="history" class="chart-wrap"></div>'
    return html.replace(anchor, selector + SECTION + anchor, 1).replace('</script>', SCRIPT + '\n</script>', 1)


def install(app):
    from fastapi import Query, Response

    @app.get('/api/v1/opencodex', summary='Saved non-additive OpenCodex aggregates; never collects')
    def opencodex_summary(response: Response, days: int = Query(default=7, ge=0, le=30)):
        # Explicit all/1/7/30 contract rather than an accidental arbitrary period.
        from fastapi import HTTPException
        if days not in (0, 1, 7, 30):
            raise HTTPException(status_code=422, detail='Use days=0 (all), 1, 7 or 30')
        response.headers['Cache-Control'] = 'no-store'
        return usage.view(days)
