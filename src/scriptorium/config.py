"""Project configuration, glossary, and do-not-translate list."""

import json
import os

STATE = ".lx"

DEFAULT_CONFIG = {
    "source_lang": "en",
    "targets": ["zh-TW"],
    "tone": "technical",
    "glossary": "config/glossary.csv",
    "dnt": "config/dnt.txt",
    "sources": ["docs/**/*.md"],
    "output_pattern": "i18n/{lang}/{path}",
    "length_ratio": {"zh-TW": [0.25, 1.20]},
    "normalize": {"zh-TW": ["punct", "pangu", "collapse_space"]},
    "lexicon_extra": {},
    "checks_disabled": [],
    "providers": {
        "local": {
            "kind": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b-instruct",
            "api_key_env": "",
            "timeout": 300,
            "temperature": 0.2,
        },
        "lmstudio": {
            "kind": "openai",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key_env": "",
            "timeout": 300,
            "temperature": 0.2,
        },
        "openai": {
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "timeout": 120,
            "temperature": 0.2,
        },
        "claude": {
            "kind": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-sonnet-4-6",
            "api_key_env": "ANTHROPIC_API_KEY",
            "timeout": 120,
            "temperature": 0.2,
        },
    },
    "routing": {"draft": "local", "polish": "local", "repair": "local"},
    "batch": {"size": 25, "concurrency": 2, "max_repair_rounds": 3},
}


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is None:
            raise FileNotFoundError(path)
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _merge(base, override):
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path="lx.config.json"):
    """User config layered over defaults, so new keys never break old projects."""
    return _merge(DEFAULT_CONFIG, load_json(path, {}))


def load_glossary(cfg):
    path = cfg.get("glossary", "config/glossary.csv")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if i == 0 and parts[0].lower() == "source":
                continue
            if len(parts) < 2:
                continue
            rows.append({
                "source": parts[0],
                "target": parts[1],
                "forbidden": [x for x in (parts[2].split(";") if len(parts) > 2 and parts[2] else []) if x],
                "severity": parts[3] if len(parts) > 3 and parts[3] else "error",
            })
    return rows


def load_dnt(cfg):
    path = cfg.get("dnt", "config/dnt.txt")
    if not os.path.exists(path):
        return []
    terms = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return sorted(set(terms), key=len, reverse=True)


def write_templates():
    """Idempotent scaffolding for a fresh project."""
    created = []
    if not os.path.exists("lx.config.json"):
        dump_json("lx.config.json", DEFAULT_CONFIG)
        created.append("lx.config.json")
    for d in (os.path.join(STATE, "docs"), os.path.join(STATE, "reports"), "config"):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists("config/glossary.csv"):
        with open("config/glossary.csv", "w", encoding="utf-8") as f:
            f.write("source,target,forbidden,severity\n"
                    "# forbidden is ;-separated; severity is error or warn\n")
        created.append("config/glossary.csv")
    if not os.path.exists("config/dnt.txt"):
        with open("config/dnt.txt", "w", encoding="utf-8") as f:
            f.write("# verbatim terms, one per line; longest match wins\n")
        created.append("config/dnt.txt")
    return created
