from pagination import paginate


def test_first_page() -> None:
    assert paginate([1, 2, 3, 4], page=1, page_size=2) == [1, 2]


def test_second_page() -> None:
    assert paginate([1, 2, 3, 4], page=2, page_size=2) == [3, 4]
