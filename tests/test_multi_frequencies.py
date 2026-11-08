"""Multi-Frequencies setups: the adaptive frequencies may be separated by ',' or ';' (the help said ';' but only
',' worked: "Invalid expression '2.45GHz;6.1GHz'"); the validation check names a bad entry."""
from hertzon.model import schema
from hertzon.model.design import Project, Setup
from hertzon.model.validate import validate
from hertzon.solver import bridge as B


def _setup(text: str):
    p = Project()
    d = p.designs[0]
    st = Setup("Setup1", props=schema.defaults(schema.SETUP))
    st.props.update(SolutionFrequencyMode="Multi-Frequencies", MultiFrequencies=text)
    d.setups.append(st)
    return p, d, st


def test_semicolon_and_comma_separate_the_frequencies():
    assert schema.frequency_list("2.45GHz;6.1GHz") == ["2.45GHz", "6.1GHz"]
    assert schema.frequency_list(" 1GHz, 2GHz ;3GHz ;") == ["1GHz", "2GHz", "3GHz"]
    for text in ("2.45GHz;6.1GHz", "2.45GHz, 6.1GHz", "6.1GHz; 2.45GHz"):
        p, d, st = _setup(text)
        assert B.adaptive_frequencies(st, d.evaluator(p)) == [2.45e9, 6.1e9]
        assert not [c for c in validate(p, d) if c.category == "Analysis Setup" and c.level == "error"]
    from hertzon.model.help import HELP
    assert "',' or ';'" in HELP["MultiFrequencies"]


def test_validation_names_a_bad_or_missing_frequency():
    p, d, _ = _setup("2.45GHz;6.1GHz+")
    errs = [c.message for c in validate(p, d) if c.category == "Analysis Setup" and c.level == "error"]
    assert len(errs) == 1 and "MultiFrequencies '6.1GHz+'" in errs[0]
    p, d, _ = _setup(" ; ")
    errs = [c.message for c in validate(p, d) if c.category == "Analysis Setup" and c.level == "error"]
    assert errs == ["Setup1.MultiFrequencies: no frequency given"]
