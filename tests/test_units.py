"""Мелкие чистые функции: тексты, транслит, длительности, конфиг."""

import config
from src.core.texts import fmt_track, plural, spoken_duration
from src.services.weather import spoken_temp
from src.services.youtube import duration_ms
from src.skills.music import playlist_names_match


def test_plural():
    assert plural(1, "час", "часа", "часов") == "час"
    assert plural(2, "час", "часа", "часов") == "часа"
    assert plural(11, "час", "часа", "часов") == "часов"
    assert plural(21, "минута", "минуты", "минут") == "минута"


def test_spoken_duration():
    assert spoken_duration(30) == "30 секунд"
    assert spoken_duration(60) == "минуту"
    assert spoken_duration(120) == "2 минуты"


def test_spoken_temp():
    assert spoken_temp("7.4") == "плюс 7"
    assert spoken_temp(-3.6) == "минус 4"
    assert spoken_temp(0, genitive=True) == "нуля"


def test_fmt_track():
    assert fmt_track({"title": "T", "artists": [{"name": "A"}]}) == "A — T"
    assert fmt_track({"title": "T", "artists": []}) == "T"


def test_playlist_translit():
    assert playlist_names_match("жанулька", "Zhanulka")
    assert playlist_names_match("жанульку", "Zhanulka")  # падеж
    assert playlist_names_match("рок", "Rock Classics")
    assert playlist_names_match("чил", "Chill Mix")
    assert not playlist_names_match("жанулька", "Мой плейлист")
    assert not playlist_names_match("", "Zhanulka")


def test_duration_ms():
    assert duration_ms("3:45") == 225000
    assert duration_ms("1:02:03") == 3723000
    assert duration_ms(None) is None  # лайв-стрим


def test_config_word_lists():
    assert config.BARE_COMMANDS.match("стоп")
    assert config.BARE_COMMANDS.match("следующий")  # «следующ*»
    assert not config.BARE_COMMANDS.match("да стоп же")
    assert config.SHUTUP_COMMANDS.match("заткнись")
    assert config.ASSISTANT_NAMES  # имя задано
