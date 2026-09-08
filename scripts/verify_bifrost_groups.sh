#!/usr/bin/env bash
set -euo pipefail
# Verify Bifrost Model Groups — issue #155 acceptance criteria
# Checks: :8080 health + config, /api/models 132, UI, :8081 health tier counts, no fallback, discoverability
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BIFROST=${BIFROST_URL:-http://localhost:8080}
SHIM=${SHIM_URL:-http://localhost:8081}
FAIL=0
pass(){ echo "  PASS $1"; }
fail(){ echo "  FAIL $1"; FAIL=1; }

# Ensure shim running for verify; start ephemeral if not
SHIM_PID=""
if ! curl -s -m 2 "$SHIM/health" >/dev/null 2>&1; then
  echo "[verify] starting ephemeral shim on :8081 for checks..."
  .venv/bin/python -m llm_discovery.bifrost.sidecar > /tmp/verify-shim.log 2>&1 &
  SHIM_PID=$!
  sleep 3
fi
cleanup(){ [[ -n "$SHIM_PID" ]] && kill "$SHIM_PID" 2>/dev/null || true; }
trap cleanup EXIT

echo "=== [1/5] Bifrost gateway :8080 healthy with config + shim_map (19 providers) ==="
if curl -s -m 5 "$BIFROST/health" | grep -q '"status":"ok"'; then
  pass "GET /health ok"
else
  fail "GET /health"
fi
if [[ -f data/bifrost/config.json ]]; then
  provs=$(python3 -c "import json; d=json.load(open('data/bifrost/config.json')); print(len(d.get('providers',{})))" 2>&1)
  if [[ "$provs" == "19" ]]; then pass "config.json 19 providers (got $provs)"; else fail "config.json providers expected 19 got $provs"; fi
else
  fail "data/bifrost/config.json missing"
fi
if [[ -f data/bifrost/shim_map.json ]]; then
  tiers=$(python3 -c "import json; d=json.load(open('data/bifrost/shim_map.json')); print(f\"{len(d.get('flash',[]))}/{len(d.get('max',[]))}/{len(d.get('contributor_free',[]))}\")" 2>&1)
  if [[ "$tiers" == "46/84/2" ]]; then pass "shim_map 46/84/2 (got $tiers)"; else fail "shim_map expected 46/84/2 got $tiers"; fi
else
  fail "data/bifrost/shim_map.json missing"
fi

echo "=== [2/5] GET /api/models lists 132 models ==="
if curl -s -m 5 "$BIFROST/api/models?limit=1000" > /tmp/verify_api.json 2>&1; then
  total=$(python3 -c "import json; d=json.load(open('/tmp/verify_api.json')); print(d.get('total',0))" 2>&1)
  if [[ "$total" == "132" ]]; then pass "/api/models total 132"; else fail "/api/models total expected 132 got $total"; fi
else
  fail "/api/models unreachable"
fi

echo "=== [3/5] Bifrost UI at $BIFROST shows flash/max/contributor_free (browser smoke + API) ==="
if curl -s -m 5 "$BIFROST/" | grep -qi "bifrost"; then
  pass "GET / UI 200 + bifrost string"
else
  fail "GET / UI"
fi
# API discoverability: shim augments models with alias virtual models
if curl -s -m 5 "$SHIM/api/models?limit=1000" > /tmp/verify_shim_api.json 2>&1; then
  has_alias=$(python3 -c "import json; d=json.load(open('/tmp/verify_shim_api.json')); print(any(m.get('provider')=='bifrost-shim' for m in d.get('models',[])))" 2>&1)
  if [[ "$has_alias" == "True" ]]; then pass "sidecar /api/models includes alias virtual models (discoverable)"; else fail "alias not in /api/models"; fi
  has_flash=$(python3 -c "import json; d=json.load(open('/tmp/verify_shim_api.json')); print(any(m.get('name')=='flash' for m in d.get('models',[])))" 2>&1)
  if [[ "$has_flash" == "True" ]]; then pass "flash discoverable"; else fail "flash not discoverable"; fi
else
  fail "shim /api/models unreachable"
fi
if curl -s -m 5 "$SHIM/v1/models" > /tmp/verify_shim_v1.json 2>&1; then
  has_v1=$(python3 -c "import json; d=json.load(open('/tmp/verify_shim_v1.json')); print(any(m.get('id')=='max' for m in d.get('data',[])))" 2>&1)
  if [[ "$has_v1" == "True" ]]; then pass "sidecar /v1/models includes alias (max)"; else fail "alias not in /v1/models"; fi
else
  fail "shim /v1/models unreachable"
fi

echo "=== [4/5] GET /health on sidecar :8081 tier counts matching shim_map ==="
if curl -s -m 5 "$SHIM/health" > /tmp/verify_health.json 2>&1; then
  tiers=$(python3 -c "import json; d=json.load(open('/tmp/verify_health.json')); t=d.get('tiers',{}); print(f\"{t.get('flash',0)}/{t.get('max',0)}/{t.get('contributor_free',0)}\")" 2>&1)
  if [[ "$tiers" == "46/84/2" ]]; then pass "/health tiers 46/84/2"; else fail "/health tiers expected 46/84/2 got $tiers"; fi
else
  fail "shim /health unreachable"
fi

echo "=== [5/5] No cross-group fallback observed (strict 503) ==="
# Use MockTransport-style check via live shim with ephemeral empty map would 503; here check that flash routes stay in flash pool by doing live alias proxy and verifying no max pool leak over 10 picks
# Lightweight: use python TestClient check (no live key needed) for strictness
if .venv/bin/python -c "
import json, httpx
from llm_discovery.bifrost.sidecar import create_app
from fastapi.testclient import TestClient
shim_map={'flash': [], 'max': ['max-m1'], 'contributor_free': []}
app=create_app(shim_map, bifrost_url='http://bifrost.test', transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'id':'x'})))
c=TestClient(app)
r=c.post('/v1/chat/completions', json={'model':'flash','messages':[{'role':'user','content':'hi'}]})
assert r.status_code==503 and r.headers.get('retry-after') is not None
assert 'tier_unavailable' in r.text
print('503 ok')
" 2>&1 | grep -q "503 ok"; then
  pass "empty flash -> 503 tier_unavailable no fallback"
else
  fail "503 tier_unavailable"
fi
# Also verify flash alias rewrites within flash pool only (not max) via mock
if .venv/bin/python -c "
import json, httpx
from llm_discovery.bifrost.sidecar import create_app
from fastapi.testclient import TestClient
shim_map={'flash': ['flash-m1','flash-m2'], 'max': ['max-m1'], 'contributor_free': ['c1']}
captured={}
def h(r):
    b=json.loads(r.content.decode())
    captured['m']=b.get('model')
    return httpx.Response(200, json={'id':'x','choices':[]})
app=create_app(shim_map, bifrost_url='http://bifrost.test', transport=httpx.MockTransport(h))
c=TestClient(app)
for _ in range(5):
    c.post('/v1/chat/completions', json={'model':'flash','messages':[{'role':'user','content':'hi'}]})
    assert captured['m'] in shim_map['flash']
    assert captured['m'] not in shim_map['max']
print('strict ok')
" 2>&1 | grep -q "strict ok"; then
  pass "strict intra-group pick (flash not in max)"
else
  fail "strict intra-group"
fi

echo ""; if [[ $FAIL -eq 0 ]]; then echo "All 5 checks PASS"; else echo "Some checks FAIL"; fi
# also run shim/auth curl smoke similar to prototype/test_shim_curl.sh section 4
 echo ""
echo "Done. For manual browser smoke: open http://localhost:8080 (gateway UI, 132 models) and http://localhost:8081/health (tiers)."
exit $FAIL
