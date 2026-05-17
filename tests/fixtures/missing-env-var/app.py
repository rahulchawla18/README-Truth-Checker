import os
import sys


def main() -> None:
    db_url = os.environ["DATABASE_URL"]
    print(f"connecting to {db_url}")
    sys.exit(0)


if __name__ == "__main__":
    main()
