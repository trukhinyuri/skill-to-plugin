#!/usr/bin/env python3
import sys


def normalize(text: str) -> str:
    return " ".join(text.split())


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    print(normalize(text))
