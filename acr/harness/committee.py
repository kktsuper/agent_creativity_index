"""Committee assignment with recusal and deterministic rotation.

chair_mode "rotating_judge": the committee is N judges from N distinct labs; one of them, rotating with the paper
sequence, also chairs (writes the summary, runs the discussion, decides). This is the two-lab configuration.
chair_mode "distinct": N reviewers plus a chair from an additional lab (the original 3+1 design).
If recusal leaves fewer labs than requested and committee.allow_short is true, the committee shrinks (minimum 1)
and the shortfall is recorded; otherwise assignment fails.
"""
from __future__ import annotations


class CommitteeError(RuntimeError):
    pass


def eligible_labs(config: dict, author_lab: str, extra_recused: set[str] | None = None) -> list[dict]:
    recused = {(author_lab or "").lower()} | {x.lower() for x in (extra_recused or set())}
    seen, out = set(), []
    for m in config["committee"]["labs"]:
        lab = m["lab"].lower()
        if lab in recused or lab in seen:
            continue
        seen.add(lab)
        out.append(m)
    return out


def assign_committee(config: dict, author_lab: str, rotation_index: int,
                     extra_recused: set[str] | None = None) -> dict:
    cc = config["committee"]
    n_rev = int(cc.get("reviewers_per_paper", 2))
    mode = cc.get("chair_mode", "rotating_judge")
    labs = eligible_labs(config, author_lab, extra_recused)
    recused = sorted({(author_lab or "").lower()} | {x.lower() for x in (extra_recused or set())})
    need = n_rev + (1 if mode == "distinct" else 0)
    note = ""
    if len(labs) < need:
        if not cc.get("allow_short", True) or not labs:
            raise CommitteeError(f"Need {need} distinct non-recused labs, have {len(labs)} "
                                 f"(recused {recused}). Add labs to the harness committee.")
        note = f"short committee: {len(labs)} of {need} labs available after recusal of {recused}"
        if mode == "distinct" and len(labs) < 2:
            mode = "rotating_judge"
        n_rev = len(labs) if mode == "rotating_judge" else len(labs) - 1
    k = rotation_index % len(labs)
    order = labs[k:] + labs[:k]
    reviewers = [dict(m, slot=i + 1) for i, m in enumerate(order[:n_rev])]
    if mode == "distinct":
        chair = dict(order[n_rev])
    else:
        chair = dict(reviewers[(rotation_index // max(1, len(labs))) % n_rev])   # rotates among the judges
        chair.pop("slot", None)
    return {"reviewers": reviewers, "chair": chair, "chair_mode": mode, "rotation_index": rotation_index,
            "recused": recused, "note": note}
