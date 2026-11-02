"""NordRouter account rows and OmniRoute buckets must not both claim the same tokens.

Account billed totals and dollars stay on the row. exclusive_tokens is only the
proven residual. Router-wide estimates are never treated as a split invoice.
"""
import auto_usage
import omniroute_usage as om


FJORD = 'deepseek/deepseek-v4.1-flash:fjord'
BASE = 'deepseek/deepseek-v4.1-flash'
# Frozen 7-day account/bucket observation. Agent 36.5M is a different scope.
ACCOUNT_FJORD = 743_977_604
ACCOUNT_BASE = 45_870_232
ACCOUNT_FJORD_USD = 5.660827
ACCOUNT_BASE_USD = 1.327571
BUCKET_FJORD = 669_937_892
BUCKET_DATED = 1_391_123
BUCKET_TOTAL = BUCKET_FJORD + BUCKET_DATED


def route(model, tokens, *, nordrouter=True, date='2026-11-01', daily=True):
    body = {'provider': 'nr', 'model': model, 'nordrouter': nordrouter,
            'totals': {'total': tokens}, 'cost_usd': 1.0}
    if daily:
        body['daily'] = [{'date': date, 'totals': {'total': tokens}, 'cost_usd': 1.0}]
    return body


def bucket(name, routes, *, retention_limited=False, total=None):
    return {'source': 'omniroute', 'provider': 'omniroute', 'model': name,
            'model_key': 'omniroute/' + name,
            'totals': {'total': total if total is not None else sum(r['totals']['total'] for r in routes)},
            'cost_usd': 7.71, 'cost_kind': 'estimated',
            'bucket_usage': {'retention_limited': retention_limited, 'routes': routes}}


def account(model, tokens, usd, *, key=None):
    return {'source': 'nordrouter', 'provider': 'nordrouter', 'model': model,
            'model_key': key or model.rsplit('/', 1)[-1].split(':', 1)[0],
            'totals': {'total': tokens}, 'cost_usd': usd, 'daily': []}


def reported():
    models = [
        account(FJORD, ACCOUNT_FJORD, ACCOUNT_FJORD_USD),
        account(BASE, ACCOUNT_BASE, ACCOUNT_BASE_USD),
        bucket('nr-deepseek-41f', [
            route('deepseek-v4.1-flash:fjord', BUCKET_FJORD),
            route('deepseek-v4.1-flash-1017', BUCKET_DATED),
        ], total=BUCKET_TOTAL),
    ]
    daily = [{'date': '2026-11-01', 'tokens': ACCOUNT_FJORD + ACCOUNT_BASE, 'amount_usd': 6.988398}]
    return models, daily


def test_reported_family_keeps_only_unoverlapped_residual():
    models, daily = reported()
    before = models[0]['totals']['total'] + models[1]['totals']['total']
    assert before == 789_847_836
    result = om.account_overlap(models, router_status='ok', nr_daily=daily)
    notes = {models[i]['model']: result['annotations'][i] for i in range(2)}
    assert notes[FJORD]['exclusive_tokens'] == ACCOUNT_FJORD - BUCKET_FJORD
    assert notes[BASE]['exclusive_tokens'] == ACCOUNT_BASE
    family = notes[FJORD]['exclusive_tokens'] + notes[BASE]['exclusive_tokens']
    assert family == 119_909_944
    assert notes[FJORD]['overlap_proven_tokens'] == BUCKET_FJORD
    assert notes[FJORD]['overlap_unknown_tokens'] == BUCKET_DATED
    assert notes[BASE]['overlap_unknown_tokens'] == 0
    assert notes[FJORD]['account_billed_cost_usd'] == ACCOUNT_FJORD_USD
    assert models[0]['cost_usd'] == ACCOUNT_FJORD_USD
    assert models[0]['totals']['total'] == ACCOUNT_FJORD
    assert models[2]['totals']['total'] == BUCKET_TOTAL
    assert models[2]['cost_usd'] == 7.71
    assert result['exclusive_tokens'] == family
    assert result['proven_tokens'] + result['exclusive_tokens'] == before
    assert result['daily'][0]['subtracted'] == BUCKET_FJORD
    assert result['daily'][0]['tokens'] == before - BUCKET_FJORD
    assert result['daily'][0]['unreconciled'] == 0


def test_suffix_alias_is_not_deducted_twice_and_base_stays():
    models = [
        account('vendor/a:fjord', 100, 1.5),
        account('vendor/a', 40, 0.5),
        bucket('one', [route('a:fjord', 30)]),
    ]
    notes = om.account_overlap(models, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 140}])['annotations']
    assert notes[0]['exclusive_tokens'] == 70
    assert notes[1]['exclusive_tokens'] == 40
    assert notes[0]['overlap_proven_tokens'] == 30
    assert notes[1]['overlap_proven_tokens'] == 0


def test_multiple_buckets_cap_once_and_keep_excess_on_the_bucket():
    models = [
        account('vendor/a', 100, 2),
        bucket('one', [route('a', 60)]),
        bucket('two', [route('a', 50)]),
    ]
    result = om.account_overlap(models, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 100}])
    note = result['annotations'][0]
    assert note['exclusive_tokens'] == 0
    assert note['overlap_proven_tokens'] == 100
    assert note['overlap_unreconciled_tokens'] == 10
    assert models[1]['totals']['total'] == 60
    assert models[2]['totals']['total'] == 50
    assert result['daily'][0]['tokens'] == 0
    assert result['daily'][0]['unreconciled'] == 10


def test_direct_only_and_fully_bucketed():
    direct = [account('vendor/only', 80, 1.25)]
    only = om.account_overlap(direct, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 80}])
    assert only['annotations'][0]['exclusive_tokens'] == 80
    assert only['annotations'][0]['exclusive_status'] == 'partitioned'
    full = [account('vendor/a', 80, 1.25), bucket('one', [route('a', 80)])]
    note = om.account_overlap(full, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 80}])['annotations'][0]
    assert note['exclusive_tokens'] == 0
    assert note['overlap_unreconciled_tokens'] == 0
    assert note['account_billed_cost_usd'] == 1.25


def test_missing_route_map_unavailable_and_negative_inputs_do_not_drop_tokens():
    unmapped = [account('vendor/a', 80, 1), bucket('one', [route('a', 80, nordrouter=None)])]
    missing = om.account_overlap(unmapped, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 80}])
    assert missing['status'] == 'no_route_map'
    assert missing['annotations'][0]['exclusive_tokens'] is None
    assert missing['proven_tokens'] == 0
    down = om.account_overlap([account('vendor/a', 80, 1)], router_status='unavailable')
    assert down['status'] == 'unavailable'
    assert down['annotations'][0]['exclusive_tokens'] is None
    assert down['exclusive_tokens'] is None
    negative = [account('vendor/a', 10, 1), bucket('one', [route('a', 4)])]
    negative[0]['totals'] = {'total': -3}
    assert om.account_overlap(negative, router_status='ok')['annotations'][0]['exclusive_status'] == 'unavailable'


def test_partial_retention_subtracts_only_observed_and_other_window_does_not():
    models = [account('vendor/a', 100, 1), bucket('one', [route('a', 40)], retention_limited=True)]
    note = om.account_overlap(models, router_status='stale', nr_daily=[{'date': '2026-11-01', 'tokens': 100}])['annotations'][0]
    assert note['exclusive_status'] == 'partial_retention'
    assert note['exclusive_tokens'] == 60
    other = [account('vendor/a', 100, 1), bucket('one', [route('a', 40, date='2026-10-01')])]
    kept = om.account_overlap(other, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 100}])['annotations'][0]
    assert kept['exclusive_tokens'] == 100
    assert kept['overlap_proven_tokens'] == 0


def test_ambiguous_tail_is_not_assigned_to_either_account_model():
    models = [
        account('vendor/a:fjord', 50, 1),
        account('other/a:fjord', 50, 1),
        bucket('one', [route('a:fjord', 30)]),
    ]
    notes = om.account_overlap(models, router_status='ok', nr_daily=[{'date': '2026-11-01', 'tokens': 100}])['annotations']
    assert notes[0]['exclusive_tokens'] == 50
    assert notes[1]['exclusive_tokens'] == 50
    assert notes[0]['overlap_proven_tokens'] == 0


def test_breakdown_preserves_hero_bill_and_annotates_residual(monkeypatch):
    import openclaw_usage
    monkeypatch.setenv('NORDROUTER_API_KEY', 'fixture')
    monkeypatch.setattr(auto_usage._hermes_usage, 'state_dbs', lambda: [])
    monkeypatch.setattr(auto_usage, 'get_date_range', lambda days: ('2026-11-01', '2026-11-01', None, None))
    monkeypatch.setattr(openclaw_usage, 'load_with_cache', lambda *a, **kw: {
        'status': 'ok', 'cached_at_utc': None, 'gateway_cache_status': None, 'error': None,
        'response': {'aggregates': {'byModel': [
            {'provider': 'omniroute', 'model': 'nr-deepseek-41f', 'totals': {'totalTokens': 651_600_000}},
            {'provider': 'nordrouter', 'model': 'deepseek-v4.1-flash', 'totals': {'totalTokens': 36_500_000}},
            {'provider': 'xai', 'model': 'grok-4.7', 'totals': {'input': 5, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0, 'totalTokens': 5}}],
            'modelDaily': []}}})

    def get(self, endpoint, **params):
        assert endpoint == 'analytics'
        return {'window_days': 1, 'totals': {'amount_usd': 6.988398},
                'daily': [{'date': '2026-11-01', 'tokens': 789_847_836, 'amount_usd': 6.988398}],
                'top_models': [
                    {'id': FJORD, 'tokens': ACCOUNT_FJORD, 'amount_usd': ACCOUNT_FJORD_USD},
                    {'id': BASE, 'tokens': ACCOUNT_BASE, 'amount_usd': ACCOUNT_BASE_USD}]}, False
    monkeypatch.setattr(auto_usage._nordrouter_usage.Client, 'get', get)
    observation = {'models': [bucket('nr-deepseek-41f', [
        route('deepseek-v4.1-flash:fjord', BUCKET_FJORD),
        route('deepseek-v4.1-flash-1017', BUCKET_DATED),
    ], total=BUCKET_TOTAL)], 'daily': [], 'status': 'ok',
        'source': {'tokens': BUCKET_TOTAL, 'complete': False, 'additive': False, 'status': 'ok'}}
    monkeypatch.setattr(auto_usage._omniroute_usage, 'collect', lambda *a, **kw: observation)
    result = auto_usage.build_model_breakdown(1)
    # No per-agent serving/account join exists, so a combined token total
    # cannot claim these routed calls are distinct from the account invoice.
    assert result['totals']['total'] is None
    assert result['sources']['accounting_complete'] is False
    assert result['sources']['openclaw']['bucket_comparison_tokens'] == 651_600_000
    assert result['sources']['nordrouter']['billed_cost_usd'] == 6.988398
    assert result['sources']['nordrouter']['tokens'] == 789_847_836
    direct = [row for row in result['models'] if row['source'] == 'nordrouter']
    assert sum(row['totals']['total'] for row in direct) == 789_847_836
    assert sum(row['exclusive_tokens'] for row in direct) == 119_909_944
    assert sum(row['cost_usd'] for row in direct) == ACCOUNT_FJORD_USD + ACCOUNT_BASE_USD
    bucket_row = next(row for row in result['models'] if row['source'] == 'omniroute')
    assert bucket_row['totals']['total'] == BUCKET_TOTAL
    assert bucket_row['cost_usd'] == 7.71
    overlap = result['sources']['nordrouter']['overlap']
    assert overlap['proven_tokens'] + overlap['exclusive_tokens'] == 789_847_836
    assert overlap['unknown_tokens'] == BUCKET_DATED


def test_daily_presentation_flag_does_not_change_accounting(monkeypatch):
    """Daily route evidence must survive until overlap is calculated."""
    import copy
    import openclaw_usage
    monkeypatch.setenv('NORDROUTER_API_KEY', 'fixture')
    monkeypatch.setattr(auto_usage, 'get_date_range', lambda days: ('2026-11-01', '2026-11-01', None, None))
    monkeypatch.setattr(auto_usage._hermes_usage, 'state_dbs', lambda: [])
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *a: {'aggregates': {'byModel': [], 'modelDaily': []}})
    monkeypatch.setattr(auto_usage._nordrouter_usage.Client, 'get', lambda *a, **kw: ({
        'window_days': 1, 'totals': {'amount_usd': 1},
        'daily': [{'date': '2026-11-01', 'tokens': 100, 'amount_usd': 1}],
        'top_models': [{'id': 'vendor/a', 'tokens': 100, 'amount_usd': 1}]}, False))
    def collect(*a, include_daily=True):
        observation = bucket('bucket', [route('a', 40)])
        if not include_daily:
            observation['bucket_usage']['routes'][0]['daily'] = []
        return {'models': [copy.deepcopy(observation)], 'daily': [], 'status': 'ok', 'source': {}}
    monkeypatch.setattr(auto_usage._omniroute_usage, 'collect', collect)
    true = auto_usage.build_model_breakdown(1, include_daily=True)
    false = auto_usage.build_model_breakdown(1, include_daily=False)
    fields = ('exclusive_tokens', 'overlap_proven_tokens', 'overlap_unreconciled_tokens')
    direct = lambda data: next(m for m in data['models'] if m['source'] == 'nordrouter')
    assert [direct(true)[k] for k in fields] == [60, 40, 0]
    assert [direct(false)[k] for k in fields] == [60, 40, 0]
    assert false['totals'] == true['totals']
    assert all(not m['daily'] for m in false['models'])
    assert not next(m for m in false['models'] if m['source'] == 'omniroute')['bucket_usage']['routes'][0]['daily']
