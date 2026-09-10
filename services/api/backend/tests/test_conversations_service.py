"""Unit tests for app/services/conversations.py's title-from-first-message
helper -- the part of append_message() covered by test_chat_api.py's own
end-to-end assertions only for the plain, no-truncation case."""

from app.services.conversations import _title_from_first_message


def test_short_message_becomes_the_title_verbatim():
    assert _title_from_first_message('Wie setze ich mein VPN-Passwort zurueck?') == 'Wie setze ich mein VPN-Passwort zurueck?'


def test_long_message_is_truncated_with_an_ellipsis():
    content = 'a' * 100
    title = _title_from_first_message(content)
    assert len(title) == 80
    assert title.endswith('…')
    assert title[:-1] == 'a' * 79


def test_only_the_first_line_of_a_multiline_message_is_used():
    assert _title_from_first_message('Erste Zeile\nZweite Zeile\nDritte Zeile') == 'Erste Zeile'


def test_blank_message_falls_back_to_a_generic_title():
    assert _title_from_first_message('   \n   ') == 'Neue Konversation'
