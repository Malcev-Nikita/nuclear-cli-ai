from src.core.wake import WakeMatcher, looks_like_junk, normalize_words

wake = WakeMatcher(names=["мага"])


def test_name_with_command():
    assert wake.extract_command("Мага, включи Нирвану") == "включи нирвану"
    assert wake.extract_command("мага дальше") == "дальше"


def test_fuzzy_name():
    assert wake.extract_command("Мака, пауза") == "пауза"  # ±1 буква


def test_name_anywhere():
    assert wake.extract_command("Играй. Он делает. Мага, стоп.") == "стоп"
    assert wake.extract_command("Что ты включил? Мага, включи рок.") == "включи рок"


def test_name_only_and_absent():
    assert wake.extract_command("Мага!") == ""
    assert wake.extract_command("включи нирвану") is None
    assert wake.extract_command("") is None


def test_control_in_context():
    words = normalize_words("Он может и управлять этим приложением Продолжай")
    assert wake.control_in_context(words) == "продолжай"
    words = normalize_words("Заткнись, сука")
    assert wake.control_in_context(words) == "заткнись"
    # длинный монолог со словом «дальше» на конце — не команда
    long = normalize_words("ну и вот мы значит пошли туда а потом ещё дальше")
    assert wake.control_in_context(long) is None


def test_junk_filter():
    assert looks_like_junk("Субтитры делал DimaTorzok")
    assert looks_like_junk("...")
    assert not looks_like_junk("Мага, включи нирвану")


def test_barge_words():
    assert wake.has_shutup_word(normalize_words("да заткнись ты"))
    assert wake.has_name(normalize_words("и тут мага стоп"))
    assert not wake.has_name(normalize_words("мы просто разговариваем"))
