"""Extract required experience separately from education and other durations."""
import re


_DURATION = re.compile(r"\b(?P<minimum>\d+(?:\.\d+)?)\s*(?:(?:-|to)\s*(?P<maximum>\d+(?:\.\d+)?)\s*|(?P<plus>\+)\s*)?(?:years?|yrs?)\b")


def extract_requirements(text: str):
    low = text.lower().replace("\u2013", "-").replace("\u2014", "-")
    requirements = []
    for match in _DURATION.finditer(low):
        before = low[max(0, match.start() - 70):match.start()]
        after = low[match.end():match.end() + 100]
        # A nearby duration is not itself an experience requirement.
        if re.match(r"\s*(?:[- ]?(?:degree|diploma|course|program|contract|tenure)|ago|old|warranty)\b", after):
            continue
        clause_end = re.split(r"[.;\n,]", after, maxsplit=1)[0]
        if re.search(r"\b(?:preferred|desirable|optional|nice to have|not required)\b", clause_end):
            continue
        required_prefix = re.search(r"(?:minimum(?: of)?|at least|requires?|required|experience\s*[:=]?)\s*$", before)
        experience_suffix = re.match(r"\s*(?:of\s+)?(?:(?:relevant|professional|work|working|industry|overall|hands.on)\s+)*(?:experience|working)\b", after)
        compact = not before.strip() and not after.strip(" .;\n")
        experience_range = match.group("maximum") is not None
        if not (required_prefix or experience_suffix or compact or experience_range or match.group("plus")):
            continue
        minimum = float(match.group("minimum"))
        maximum = float(match.group("maximum")) if match.group("maximum") else None
        if minimum > 70 or maximum is not None and (maximum > 70 or maximum < minimum):
            continue
        requirements.append((minimum, maximum))
    minimum, maximum = max(requirements, key=lambda pair: (pair[0], pair[1] if pair[1] is not None else float('inf')), default=(0, None))
    years = set()
    for sentence in re.split(r'[.!;\n]', text):
        if re.search(r'graduat|batch|cohort|pass(?:ed)?[ -]?out', sentence, re.I):
            years.update(int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', sentence))
    return minimum, maximum, sorted(years)
