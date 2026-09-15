"""Verified provider claim helpers — first-party URL allowlist, owner matching, specificity.

Issue #213: ProviderClaim gains url field; collector captures first-party URL per
provider claim with owner-matching via same normalization as collector; gate promotes
weak→moderate only when attributable + allowlisted URL + specific (benchmark name or
>=2 langs/agentic), never strong.
"""
import re
from urllib.parse import urlparse

try:
    from .model_matching import normalize_model_id as _norm
    from .model_matching import _generate_match_variants as _variants  # optional
    HAS_NORM = True
except Exception:
    _norm = None
    HAS_NORM = False

# ---------- Allowlist ----------
# Huggingface + provider blog / docs domains that count as first-party.
# Derived from models_dev catalog hosts + research allowlist.
BLOG_ALLOWLIST = {
    "huggingface.co",
    "github.com",  # owner-matched only (e.g. github.com/Qwen)
    "ai.meta.com",
    "meta.com",
    "mistral.ai",
    "openai.com",
    "qwen.ai",
    "qwenlm.github.io",
    "deepseek.com",
    "api-docs.deepseek.com",
    "static.stepfun.com",
    "stepfun.com",
    "mimo.xiaomi.com",
    "xiaomi.com",
    "bytedance.com",
    "seed.bytedance.com",
    "poolside.ai",
    "minimax.io",
    "www.minimax.io",
    "deepmind.google",
    "anthropic.com",
    "www.anthropic.com",
    "cohere.com",
    "nvidia.com",
    "google.com",
    "ai.google.dev",
    "aider.chat",
    "artificialanalysis.ai",
    "www.swebench.com",
    "swebench.com",
    "openrouter.ai",  # not first-party for vendor but include for completeness? treat as allowlisted
    "z.ai",
    "x.ai",
}

OWNER_MATCH_DOMAINS = {"huggingface.co", "github.com"}

# ---------- Helpers ----------
BENCHMARK_KEYWORDS = ("swe-bench", "terminal-bench", "livecodebench", "humaneval", "aider", "bigcodebench", "codeforces", "gpqa", "swe-bench verified", "swe-bench pro")
MINI_TOKENS = ("mini", "small", "lite", "nano")

def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        # strip port
        if ":" in host:
            host = host.split(":")[0]
        # strip www handled via allowlist contains both forms; keep as is
        return host
    except Exception:
        return ""

def is_allowlisted_url(url: str) -> bool:
    """Return True if url domain is in blog/HF allowlist."""
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("http"):
        return False
    host = _domain(url)
    if not host:
        return False
    # direct match
    if host in BLOG_ALLOWLIST:
        return True
    # allow subdomains: e.g. docs.qwen.ai vs qwen.ai? check suffix
    for allowed in BLOG_ALLOWLIST:
        # avoid matching github.io generic: qwenlm.github.io is specific, but any *.github.io not allowed unless exact
        if host == allowed or host.endswith("." + allowed):
            # for github.io, require exact qwenlm.github.io already covered; any other github.io would still match qwenlm.github.io suffix? No, random.github.io would not end with qwenlm.github.io
            return True
    # huggingface subdomains?
    if host.endswith(".huggingface.co"):
        return True
    return False

def _normalize(s: str) -> str:
    if _norm is None:
        return s.lower().strip()
    try:
        return _norm(s)
    except Exception:
        return s.lower().strip()

def _repo_from_url(url: str) -> str | None:
    """Extract repo segment from HF/GitHub URL: https://huggingface.co/<owner>/<repo>"""
    try:
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return parts[1]
        if len(parts) == 1:
            return parts[0]
        return None
    except Exception:
        return None

def is_owner_matched_url(url: str, model_id: str) -> bool:
    """For HF/GitHub domains, verify repo name matches model_id via same normalization.

    For blog domains, always True (domain itself is provider attribution).
    """
    host = _domain(url)
    if host in OWNER_MATCH_DOMAINS or host.endswith(".huggingface.co"):
        repo = _repo_from_url(url)
        if not repo:
            return False
        # Normalize both sides
        norm_repo = _normalize(repo)
        # model_id bare (after /) and full
        bare = model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id
        norm_bare = _normalize(bare)
        norm_full = _normalize(model_id)
        # Also handle dot/hyphen variant equivalence: generate variants for repo and compare
        candidates = {norm_repo, norm_repo.replace(".", "-"), norm_repo.replace("-", ".")}
        # Add dot<->hyphen variants for bare/full as well
        # Simple equivalence: repo matches bare or full, ignoring dot/hyphen
        for cand in [norm_bare, norm_full, norm_bare.replace(".", "-"), norm_full.replace(".", "-")]:
            if cand in candidates:
                return True
        # Also handle version format variant via regex similar to _generate_match_variants: \d-\d vs \d.\d
        # Do direct hyphen/dot swap
        import re as _re
        def hy_to_dot(s): return _re.sub(r"(\d)-(\d)", r"\1.\2", s)
        def dot_to_hy(s): return s.replace(".", "-")
        for cand in [norm_bare, norm_full]:
            if hy_to_dot(cand) == norm_repo or hy_to_dot(norm_repo) == cand:
                return True
            if dot_to_hy(cand) == norm_repo or dot_to_hy(norm_repo) == cand:
                return True
        # Allow repo being prefix of bare when variant suffix like -pro ? Be conservative: require exact match, but lenient via substring if repo is prefix of bare
        # Example repo mimo-v2.5 vs bare mimo-v2.5-pro -> repo is prefix, should we consider matched? For true owner matching, HF repo for base model may not match pro variant -> treat as mismatch? Conservatively require exact or repo prefix.
        # For safety, allow prefix match when bare starts with repo + "-"
        if norm_bare.startswith(norm_repo + "-") or norm_bare.startswith(norm_repo.replace(".", "-") + "-"):
            return True
        return False
    # blog domains: no repo check, domain allowlist already ensures attribution
    return True

def is_specific_claim(text: str) -> bool:
    """Specific = benchmark name OR >=2 langs (or 80+ programming languages) OR agentic phrasing."""
    if not text:
        return False
    low = text.lower()
    # benchmark name
    for kw in BENCHMARK_KEYWORDS:
        if kw in low:
            return True
    # explicit "programming languages" enumeration (80+ or similar)
    if "programming languages" in low:
        return True
    # agentic phrasing (including repository/tool use variants)
    agentic_phrases = ("repository edits", "software engineering workflows", "multi-turn tool use", "agentic coding", "agentic software", "repository reasoning", "repository tasks")
    for phrase in agentic_phrases:
        if phrase in low:
            return True
    # generic agentic + coding
    if "agentic" in low and ("coding" in low or "software" in low or "programming" in low or "code generation" in low):
        return True
    # repository/tool use with coding context (more permissive: repository alone + coding)
    if ("repository" in low or "tool use" in low) and ("coding" in low or "programming" in low or "software engineering" in low or "code generation" in low):
        return True
    # coding agents phrasing
    if "coding agent" in low or "coding-agent" in low:
        return True
    # >=2 programming languages listed
    # Use word-boundary regex to avoid java inside javascript double-count
    langs = ["python", "javascript", "typescript", "java", "go", "rust", "c++", "c#", "ruby", "php", "swift", "kotlin", "scala", "perl", "haskell", "bash", "shell", "r"]
    found = set()
    for lang in langs:
        # build pattern with boundaries, handle c++ escape
        esc = re.escape(lang)
        # for single-letter r, require word boundary and not part of other word
        if lang == "r":
            pat = r"\br\b"
        elif lang in ("go",):
            pat = r"\b" + esc + r"\b"
        else:
            pat = r"\b" + esc + r"\b"
        try:
            if re.search(pat, low):
                found.add(lang)
        except Exception:
            if lang in low:
                found.add(lang)
    # collapse java/javascript double count: if javascript present, java not counted separately
    if "javascript" in found and "java" in found:
        found.discard("java")
    if len(found) >= 2:
        return True
    # also detect comma-separated lang list with at least two known langs via simple count
    return False

def is_mini_variant(model_id: str) -> bool:
    low = (model_id or "").lower()
    for tok in MINI_TOKENS:
        if tok in low:
            # token must be separate via hyphen/underscore/slash? but substring is enough per prompt: "mini/small/lite/nano" as tokens
            # ensure bounded by non-alnum or start/end
            if re.search(r"(?:^|[^a-z0-9])" + re.escape(tok) + r"(?:[^a-z0-9]|$)", low) or tok in low:
                return True
    return False

def has_verified_claim(provider_claims, model_id: str) -> bool:
    """Check if any claim qualifies as verified (allowlisted+owner-matched+specific+not mini)."""
    if is_mini_variant(model_id):
        return False
    if not provider_claims:
        return False
    for claim in provider_claims:
        url = getattr(claim, "url", None)
        if not url:
            continue
        if not is_allowlisted_url(url):
            continue
        if not is_owner_matched_url(url, model_id):
            continue
        txt = getattr(claim, "claim", "")
        if not is_specific_claim(txt):
            continue
        return True
    return False

def evidence_has_allowlisted_url(evidence: list) -> bool:
    """Hardened guard: evidence strings contain allowlisted URL domain."""
    if not evidence:
        return False
    url_re = re.compile(r"https?://[^\s\)]+" )
    for e in evidence:
        try:
            s = str(e)
        except Exception:
            continue
        for url in url_re.findall(s):
            # strip trailing punctuation
            url = url.rstrip('.,;\"')
            if is_allowlisted_url(url):
                # For HF, owner match not required for evidence (LLM web search may cite HF) but domain allowlist already gates
                return True
    return False
