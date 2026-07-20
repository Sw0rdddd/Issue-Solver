from search_demo.cli import main


def test_cli_prints_matching_items(capsys) -> None:
    exit_code = main(["Alpha"])

    assert exit_code == 0
    assert capsys.readouterr().out == "Alpha Keyboard\n"
