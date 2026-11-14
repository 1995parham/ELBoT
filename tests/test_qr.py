"""QR login: the picture a phone has to read, and the flags that ask for it."""

import topoli_user as t


def test_matrix_has_quiet_zone():
    m = t.qr_matrix("tg://login?token=abc")
    assert len(m) == len(m[0])
    assert not any(m[0]) and not any(m[-1])  # four light modules all round
    assert not any(row[0] for row in m)


def test_text_is_two_rows_per_line_and_inverted():
    m = [[True, False], [False, False], [True, True]]
    lines = t.qr_text(m).split("\n")
    assert len(lines) == 2  # three rows, padded to four
    # dark modules are blank, light ones drawn: dark-on-light on a dark terminal
    assert lines[0] == "▄█"
    assert lines[1] == "▄▄"  # the padding row is light


def test_text_glyphs():
    assert t.qr_text([[False], [False]]) == "█"
    assert t.qr_text([[True], [True]]) == " "
    assert t.qr_text([[False], [True]]) == "▀"
    assert t.qr_text([[True], [False]]) == "▄"


def test_svg_draws_one_square_per_dark_module():
    svg = t.qr_svg([[True, False], [False, True]], scale=4)
    assert 'width="8"' in svg
    assert svg.count("h4v4h-4z") == 2


def test_page_refreshes_until_done():
    assert 'http-equiv="refresh"' in t.qr_page("x")
    assert "refresh" not in t.qr_page("done", refresh=False)


def test_login_flags():
    args = t.build_parser().parse_args(["login", "--qr", "--open", "--qr-timeout", "60"])
    assert args.qr and args.open and args.qr_timeout == 60
    plain = t.build_parser().parse_args(["login"])
    assert not plain.qr and plain.qr_timeout == 300
