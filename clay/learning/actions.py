"""Action decider: choose per-part orientations without falling into a local optimum.

Why a separate decider
----------------------
The 16-candidate sweep in ``contact_primitives._generate`` enumerates two rows
times four quarter turns. That is small enough to exhaust, yet it already
produced a documented failure: with a budget of 4, a single-sample ranking spent
the budget on four *rotationally equivalent* constructions (n0_p0, n1_p1, n2_p2,
n3_p3 all materialise to the same area) and returned a worse result than a fixed
order. The bug was not the ranking; it was that equivalence was invisible to the
search.

Running the 16 candidates of the frozen GF180 experiment confirms the classes
directly - three distinct areas, with the four diagonal rotations sharing one:

    h = (p - n) mod 4        members            area_nm2
    0                        n0_p0 n1_p1 n2_p2 n3_p3   11 558 400
    2                        n0_p2 n1_p3 n2_p0 n3_p1   12 177 600
    1, 3                     the other 8            12 280 800

So this module does three things, in order of certainty:

1. **Canonical form.** Two plans that differ only by a global rotation of the
   whole design are the same construction and must be evaluated once. This is
   not a heuristic; it is a property of the action space.
2. **Lower-bound pruning.** Each surviving representative gets an arithmetic
   lower bound on its achievable area, computed from its own outer boxes. A
   representative whose bound cannot beat the best area already achieved is
   dropped *with the bound recorded*, which is what makes "we did not miss a
   better placement in this space" checkable rather than asserted.
3. **A certificate.** The decider returns what it explored, what it pruned and
   why, so a reviewer can see the difference between "optimal in this space"
   and "best we happened to try".

What it does not claim
----------------------
The bound is a bound for *two rectangles with one clearance*, which is the
placement model this experiment uses. It says nothing about constructions the
model does not contain (finger decomposition, non-rectangular active, a third
row). The certificate states that scope explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from clay.learning.freedom import Action, Freedom, FreedomViolation, ROTATE, enforce


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """One action per part: the candidate a solver will be asked to place."""

    actions: Mapping[str, Action]

    def key(self) -> Tuple[Tuple[str, int], ...]:
        return tuple(sorted((name, a.degrees) for name, a in self.actions.items()))

    def name(self) -> str:
        return "_".join(f"{n}{a.degrees // 90}" for n, a in sorted(self.actions.items()))

    def rotated_globally(self, degrees: int, parts: Iterable[str]) -> "Plan":
        """Rotate every named part by the same angle (a global rotation)."""
        rotated = dict(self.actions)
        for name in parts:
            current = rotated[name]
            if current.op != ROTATE:
                raise ValueError("global rotation requires every part to be a rotation")
            rotated[name] = Action(ROTATE, degrees=(current.degrees + degrees) % 360)
        return Plan(rotated)


def canonical(plan: Plan, parts: Sequence[str], step_deg: int) -> Tuple[int, ...]:
    """The plan's equivalence class under global rotation.

    Rotating the entire design leaves the achievable area unchanged, so plans
    in one class are one construction. A global rotation adds the same angle to
    every part, so what is invariant is each part's angle *relative to the
    first part*, **in part order**. The tuple must not be sorted: for three
    parts, (0, 90, 180) and (0, 180, 90) give the sorted multiset {90, 180}
    for both, yet no global rotation maps one onto the other (the second and
    third parts are different geometry). Sorting merged them and would have
    dropped a distinct construction from the search.
    """
    if len(parts) < 2:
        return (0,)
    angles = [plan.actions[name].degrees for name in parts]
    base = angles[0]
    return tuple((a - base) % 360 for a in angles[1:])


def representative_index(plans: Sequence[Plan], parts: Sequence[str], step_deg: int) -> List[int]:
    """Indices of one representative per global-rotation class, first seen wins."""
    seen: Dict[Tuple[int, ...], int] = {}
    for index, plan in enumerate(plans):
        cls = canonical(plan, parts, step_deg)
        seen.setdefault(cls, index)
    return sorted(seen.values())


# ---------------------------------------------------------------------------
# lower bound
# ---------------------------------------------------------------------------


def two_box_lower_bound(outer: Mapping[str, Tuple[int, int, int, int]],
                        rects: Mapping[str, Tuple[int, int]],
                        gap: int) -> Tuple[int, str]:
    """A provable lower bound on the envelope area of two placed rows.

    The bound must be **sound**: no legal placement may come in under it. It is
    therefore written in terms of exactly what the inner solver
    (:func:`clay.layout.synth.area_placement.solve_area`) enforces, and nothing
    else:

    * each part's *placement rectangle* (its active region, ``rects[n] = (w, h)``)
      sits with its lower-left corner at the part's origin;
    * the two rectangles are separated by ``gap`` along at least one axis, in
      one of four orders (a left of b, b left of a, a below b, b below a);
    * the envelope must contain both *outer* boxes, given relative to the same
      origin.

    For "a left of b" (``x_b >= x_a + w_a + gap``) the envelope width is at
    least ``(x_b + ob.right) - (x_a + oa.left) >= w_a + gap + ob.right - oa.left``,
    and never less than either outer width; its height is never less than
    either outer height. The other three orders are symmetric. A placement uses
    one of the four, so the minimum over them is a valid bound.

    Two earlier versions were wrong, both in the same direction (too strong):

    1. separating the *outer* boxes by the gap - 15 498 000 nm2 against an
       achieved 11 558 400 on GF180, because gate tails interleave;
    2. separating the *core* boxes (active + poly + contact + metal1) by the
       pack's clearance. The solver separates *active* rectangles, and the core
       box sticks out past the active, so two cores can legally sit closer than
       the clearance. GF180's 1200 nm clearance hid this; on SKY130 (520 nm) the
       "bound" exceeded the achieved area by 2.5-5.8 % and, under a small budget,
       pruned every class including the optimum.

    ``gap`` must be the gap the solver is actually given for this plan (the
    per-plan construction-aware clearance), not a global constant: a smaller
    value would only weaken the bound, a larger one would make it unsound.
    """
    if set(outer) != set(rects) or len(outer) != 2:
        raise ValueError(f"two-box bound needs two boxes in both outer and rects, "
                         f"got outer={sorted(outer)} rects={sorted(rects)}")
    a, b = sorted(outer)
    oa, ob = outer[a], outer[b]
    wa, ha = rects[a]
    wb, hb = rects[b]
    ow = max(oa[2] - oa[0], ob[2] - ob[0])     # envelope width floor
    oh = max(oa[3] - oa[1], ob[3] - ob[1])     # envelope height floor
    options = {
        "a_left_of_b":  max(wa + gap + ob[2] - oa[0], ow) * oh,
        "b_left_of_a":  max(wb + gap + oa[2] - ob[0], ow) * oh,
        "a_below_b":    ow * max(ha + gap + ob[3] - oa[1], oh),
        "b_below_a":    ow * max(hb + gap + oa[3] - ob[1], oh),
    }
    form = min(options, key=lambda k: (options[k], k))
    axis = "separated_in_x" if "left" in form else "separated_in_y"
    return options[form], axis


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    plan: Plan
    class_key: Tuple[int, ...]
    lower_bound_nm2: Optional[int]
    bound_form: str
    evaluated: bool
    pruned_reason: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "plan": self.plan.name(),
            "class": list(self.class_key),
            "lower_bound_nm2": self.lower_bound_nm2,
            "bound_form": self.bound_form,
            "evaluated": self.evaluated,
            "pruned": self.pruned_reason,
        }


@dataclass(frozen=True)
class Decision:
    """The decider's answer plus the evidence for it."""

    order: Tuple[Candidate, ...]
    best_area_nm2: Optional[int]
    certificate: Dict[str, object]

    def evaluated(self) -> Tuple[Candidate, ...]:
        return tuple(c for c in self.order if c.evaluated)


def decide_actions(freedom: Freedom,
                   grid: Mapping[str, Sequence[int]],
                   outs: Mapping[Tuple[Tuple[str, int], ...], Mapping[str, Tuple[int, int, int, int]]],
                   clearance: int,
                   budget: Optional[int] = None,
                   parts: Sequence[str] = (),
                   cores: Optional[Mapping[Tuple[Tuple[str, int], ...], Mapping[str, Tuple[int, int, int, int]]]] = None,
                   *,
                   rects: Mapping[Tuple[Tuple[str, int], ...], Mapping[str, Tuple[int, int]]],
                   gaps: Optional[Mapping[Tuple[Tuple[str, int], ...], int]] = None,
                   ) -> Decision:
    """Order the legal plans, dedupe equivalence classes, and bound each one.

    ``grid`` maps a part to the rotation angles it may take (as resolved by the
    freedom model). Per plan key: ``outs`` gives each part's outer box and
    ``rects`` its placement rectangle (``(w, h)`` of the active, at the origin);
    ``gaps`` gives the separation the inner solver will actually apply to that
    plan (defaults to ``clearance``). ``cores`` is passed through for the inner
    solve only - it does not enter the bound, because the solver does not
    separate cores (see :func:`two_box_lower_bound`). All of these are computed
    by the caller from freshly built geometry, never from a reference layout.
    ``budget`` caps how many representatives are actually evaluated.

    Candidates are returned **ordered by their lower bound**, cheapest first, so
    a small budget is spent on the most promising distinct constructions rather
    than on rotations of one. Pruning against an achieved area is a separate
    step (:func:`prune`), because a bound only prunes once something has been
    measured; pretending otherwise would make the certificate dishonest.
    """
    part_names = tuple(parts) or tuple(sorted(grid))
    from itertools import product
    plans: List[Plan] = []
    for combo in product(*(grid[name] for name in part_names)):
        plans.append(Plan({name: Action(ROTATE, degrees=d)
                           for name, d in zip(part_names, combo)}))
    # Every plan must be legal under the freedom model, or the decider is
    # proposing something the design forbids.
    for plan in plans:
        enforce(freedom, plan.actions)

    reps = representative_index(plans, part_names, freedom.pack.grid_nm)
    scored: List[Candidate] = []
    for index in reps:
        plan = plans[index]
        box = outs.get(plan.key())
        if box is None:
            raise KeyError(f"no outer boxes supplied for plan {plan.name()}")
        rect = rects.get(plan.key())
        if rect is None:
            raise KeyError(f"no placement rectangles supplied for plan {plan.name()}")
        gap = clearance if gaps is None else gaps.get(plan.key(), clearance)
        lb, form = two_box_lower_bound(box, rect, gap)
        scored.append(Candidate(plan, canonical(plan, part_names, freedom.pack.grid_nm),
                                lb, form, True))
    # A lower bound that is not monotone in the bound value would make this
    # ordering meaningless, so assert the ordering is total.
    scored.sort(key=lambda c: (c.lower_bound_nm2 or 0, c.plan.name()))
    out: List[Candidate] = []
    evaluated = 0
    for candidate in scored:
        if budget is not None and evaluated >= budget:
            out.append(Candidate(candidate.plan, candidate.class_key,
                                 candidate.lower_bound_nm2, candidate.bound_form, False,
                                 f"budget {budget} exhausted"))
            continue
        out.append(candidate)
        evaluated += 1
    return Decision(tuple(out), None, {
        "scope": "two-rectangle placement model with one clearance; the bound and the "
                 "class dedup say nothing about constructions outside that model",
        "plans_total": len(plans),
        "representatives": len(reps),
        "evaluated": evaluated,
        "parts": list(part_names),
        "clearance_nm": clearance,
        "budget": budget,
    })


def prune(decision: Decision, achieved_nm2: int) -> Decision:
    """Drop candidates whose bound is strictly worse than an achieved area.

    Strict (``>``), not ``>=``: the objective is lexicographic (area, then the
    later priority levels), so a candidate whose bound *equals* the best area
    may still win on a later level and must be solved, not discarded.

    Kept separate from :func:`decide_actions` on purpose: a lower bound only
    prunes once some area has actually been measured. Returns a new decision
    with the pruned candidates marked and the reason recorded.
    """
    out: List[Candidate] = []
    pruned = 0
    for candidate in decision.order:
        if candidate.evaluated and candidate.lower_bound_nm2 is not None \
                and candidate.lower_bound_nm2 > achieved_nm2:
            out.append(Candidate(candidate.plan, candidate.class_key,
                                 candidate.lower_bound_nm2, candidate.bound_form, False,
                                 f"lower bound {candidate.lower_bound_nm2} > achieved {achieved_nm2}"))
            pruned += 1
        else:
            out.append(candidate)
    certificate = dict(decision.certificate)
    certificate["pruned_by_bound"] = pruned
    certificate["achieved_nm2"] = achieved_nm2
    return Decision(tuple(out),
                    achieved_nm2 if decision.best_area_nm2 is None
                    else min(decision.best_area_nm2, achieved_nm2),
                    certificate)


def record_area(decision: Decision, area_nm2: int) -> Decision:
    """Record a measured area without changing which candidates were chosen."""
    return Decision(decision.order,
                    area_nm2 if decision.best_area_nm2 is None
                    else min(decision.best_area_nm2, area_nm2),
                    decision.certificate)


# ---------------------------------------------------------------------------
# CEGIS: close the loop between the decider and the exact inner solver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What the inner solver reported for one evaluated plan.

    ``area_nm2`` is the material area it achieved; ``inner_bound_nm2`` is the
    solver's own best objective bound, which is what makes an optimality claim
    checkable rather than asserted; ``status`` is the solver's word
    (``OPTIMAL`` / ``FEASIBLE`` / ...). ``feasible`` is False when the plan could
    not be placed at all under the clearance - a real possibility for a rotated
    construction, and the reason the loop exists.
    """

    plan: Plan
    area_nm2: Optional[int]
    inner_bound_nm2: Optional[int]
    status: str
    feasible: bool

    @property
    def proven_optimal(self) -> bool:
        return self.status == "OPTIMAL" and self.feasible


def search(freedom: Freedom,
           grid: Mapping[str, Sequence[int]],
           outs: Mapping[Tuple[Tuple[str, int], ...], Mapping[str, Tuple[int, int, int, int]]],
           cores: Mapping[Tuple[Tuple[str, int], ...], Mapping[str, Tuple[int, int, int, int]]],
           clearance: int,
           solve,
           budget: Optional[int] = None,
           parts: Sequence[str] = (),
           max_rounds: Optional[int] = None,
           *,
           rects: Mapping[Tuple[Tuple[str, int], ...], Mapping[str, Tuple[int, int]]],
           gaps: Optional[Mapping[Tuple[Tuple[str, int], ...], int]] = None) -> Decision:
    """Iterate decide -> solve -> prune until nothing changes.

    ``solve(plan, outer, core) -> Outcome`` is the exact inner solver; in this
    project it wraps :func:`clay.layout.synth.area_placement.solve_area`.

    Why iterate rather than evaluate once:

    * a lower bound computed from the parts' own boxes prunes as soon as a real
      area is known - so the *first* solve makes the remaining candidates
      cheaper to judge, and re-running the bound pass with the achieved area can
      retire candidates the initial ordering kept;
    * a plan can fail to place (the inner solver returns infeasible). That
      outcome is information the outer layer did not have, and it must remove
      the plan from contention rather than silently leaving a candidate that was
      never measured in the ordering;
    * when the best plan's inner solve reports OPTIMAL *and* every remaining
      candidate's bound is at least that area, the loop stops with a proof:
      nothing left can beat it. When the budget or the round cap stops it first,
      the certificate says so instead.

    The result is always the same shape as :func:`decide_actions`, plus
    ``certificate['converged']`` and the per-round trace, so a caller does not
    have to branch on which one ran.
    """
    decision = decide_actions(freedom, grid, outs, clearance,
                              budget=None, parts=parts, cores=cores,
                              rects=rects, gaps=gaps)
    part_names = tuple(parts) or tuple(sorted(grid))
    # Best-first branch and bound: each round solves the single live candidate
    # with the smallest bound, then re-prunes. Solving a whole batch per round
    # (as before) spent the budget before any bound could prune, so a sound
    # bound saved nothing. One solve per round needs at most one round per
    # representative, plus the round that finds nothing left.
    if max_rounds is None:
        max_rounds = len(decision.order) + 1
    trace: List[Dict[str, object]] = []
    best: Optional[Tuple[int, Plan]] = None
    solved: Dict[Tuple[Tuple[str, int], ...], Outcome] = {}
    evaluated_count = 0

    for round_index in range(max_rounds):
        # Re-prune with what is known now; the first round has no best yet.
        if best is not None:
            decision = prune(decision, best[0])
        pending = [c for c in decision.order if c.evaluated
                   and c.plan.key() not in solved]
        pending = pending[:1]
        if budget is not None and budget - evaluated_count <= 0:
            pending = []
        if not pending:
            trace.append({'round': round_index, 'solved': 0,
                          'reason': 'no pending candidate'})
            break
        progressed = False
        for candidate in pending:
            key = candidate.plan.key()
            outcome = solve(candidate.plan,
                            outs.get(key, {}), cores.get(key, {}))
            solved[key] = outcome
            evaluated_count += 1
            progressed = True
            if outcome.feasible and outcome.area_nm2 is not None:
                if best is None or outcome.area_nm2 < best[0]:
                    best = (outcome.area_nm2, candidate.plan)
        trace.append({'round': round_index, 'solved': len(pending)})
        if not progressed:
            break
        # Stop when nothing the decider still regards as a live candidate is
        # unsolved. Optimality itself is judged after the loop, from the
        # outcomes, so this test only decides when to stop asking for solves.
        unsolved = [c for c in decision.order if c.evaluated and c.plan.key() not in solved]
        if not unsolved:
            break

    out: List[Candidate] = []
    for candidate in decision.order:
        outcome = solved.get(candidate.plan.key())
        if outcome is None:
            out.append(Candidate(candidate.plan, candidate.class_key,
                                 candidate.lower_bound_nm2, candidate.bound_form, False,
                                 candidate.pruned_reason or 'not solved within round/budget limit'))
            continue
        if not outcome.feasible:
            out.append(Candidate(candidate.plan, candidate.class_key,
                                 candidate.lower_bound_nm2, candidate.bound_form, False,
                                 f'inner solver could not place this plan: {outcome.status}'))
            continue
        out.append(Candidate(candidate.plan, candidate.class_key,
                             candidate.lower_bound_nm2, candidate.bound_form,
                             # A plan with a real, feasible solve is a measured
                             # result. A bound computed afterwards cannot unmeasure
                             # it: dropping it here is how the SKY130 winner was
                             # lost under budgets 1 and 2 (its own bound was
                             # compared with its own area and "pruned" it).
                             True, ''))

    # A candidate is *retired* when it can no longer win: it was pruned by a
    # bound at or above the best area, or the exact inner solve placed it and
    # came out no better. Merely having been evaluated is not retirement, so the
    # two cases have to be told apart from the outcome, not from the flag.
    retired = 0
    unexplained: List[str] = []
    if best is not None:
        for candidate in out:
            key = candidate.plan.key()
            outcome = solved.get(key)
            if key == best[1].key():
                continue
            if outcome is None:
                # Never solved. That is only accounted for if a *sound* bound
                # shows it cannot beat the best measured area; anything else
                # (budget, round cap) leaves it genuinely unexplained.
                if candidate.lower_bound_nm2 is not None \
                        and candidate.lower_bound_nm2 > best[0]:
                    retired += 1
                else:
                    unexplained.append(f'{candidate.plan.name()}: never solved')
            elif not outcome.feasible:
                retired += 1
            elif outcome.area_nm2 is not None and outcome.area_nm2 < best[0]:
                # Cannot happen: best is the minimum over feasible outcomes.
                unexplained.append(f'{candidate.plan.name()}: better than the recorded best')
            else:
                retired += 1

    # Every representative, with its bound and how it was settled, so a
    # reviewer can check "bound >= winner" from result.json alone.
    classes = []
    retired_by_bound = 0
    for candidate in out:
        key = candidate.plan.key()
        outcome = solved.get(key)
        row = {"plan": candidate.plan.name(), "class": list(candidate.class_key),
               "lower_bound_nm2": candidate.lower_bound_nm2,
               "bound_form": candidate.bound_form}
        if outcome is not None:
            row.update({"settled": "solved" if outcome.feasible else "infeasible",
                        "area_nm2": outcome.area_nm2, "inner_status": outcome.status,
                        "inner_bound_nm2": outcome.inner_bound_nm2})
        elif best is not None and candidate.lower_bound_nm2 is not None \
                and candidate.lower_bound_nm2 > best[0]:
            row.update({"settled": "bound",
                        "reason": f"lower bound {candidate.lower_bound_nm2} > best {best[0]}"})
            retired_by_bound += 1
        else:
            row.update({"settled": "open", "reason": candidate.pruned_reason or "never solved"})
        classes.append(row)

    certificate = dict(decision.certificate)
    certificate.update({
        'mode': 'cegis',
        'search': 'best-first branch and bound: one exact solve per round, then re-prune',
        'rounds': len(trace),
        'trace': trace,
        'solved': evaluated_count,
        # Converged: every plan the decider ever offered is resolved one way or
        # another (solved, or pruned before solving).
        'converged': bool(out) and not unexplained,
        'unexplained': unexplained,
        'retired': retired,
        # Never-solved representatives excluded by their bound. Unlike the
        # prune() counter this cannot include the winner itself.
        'retired_by_bound': retired_by_bound,
        'classes': classes,
        'best_area_nm2': None if best is None else best[0],
        'winner': None if best is None else best[1].name(),
        'inner_status': {o.plan.name(): o.status for o in solved.values()},
    })
    # Optimality inside this model needs all three: the winner's own inner solve
    # returned OPTIMAL, every other plan is accounted for, and none of them came
    # out better. Otherwise the certificate says only what was actually shown.
    if best is not None:
        winner_outcome = solved.get(best[1].key())
        certificate['proven_optimal_in_model'] = bool(
            winner_outcome is not None
            and winner_outcome.proven_optimal
            and not unexplained)
    else:
        certificate['proven_optimal_in_model'] = False
    return Decision(tuple(out), None if best is None else best[0], certificate)


__all__ = [
    "Plan", "Candidate", "Decision", "Outcome",
    "canonical", "representative_index", "two_box_lower_bound",
    "decide_actions", "prune", "record_area", "search",
]
