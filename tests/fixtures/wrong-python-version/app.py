def classify(x: int) -> str:
    # match-case syntax requires Python 3.10+
    match x:
        case 0:
            return "zero"
        case n if n > 0:
            return "positive"
        case _:
            return "negative"


if __name__ == "__main__":
    print(classify(42))
