def refine(generate, check, max_tries: int, score=None):
    """generate(errors) -> candidate; check(candidate) -> list of error strings.
    Returns (best_candidate, its_errors); best = highest score (default: fewest errors)."""
    score = score or (lambda cand, errs: -len(errs))
    best = None
    errors: list[str] = []
    for _ in range(max_tries):
        cand = generate(errors)
        errors = check(cand)
        if not errors:
            return cand, []
        if best is None or score(cand, errors) > best[0]:
            best = (score(cand, errors), cand, errors)
    return best[1], best[2]
