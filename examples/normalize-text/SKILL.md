---
name: normalize-text
description: Normalize pasted text by trimming and collapsing whitespace while preserving Unicode characters.
---

For supplied text, use [the normalizer](scripts/normalize.py). Preserve letters,
punctuation and character case. Collapse consecutive whitespace into one space,
remove leading/trailing whitespace, and return the normalized text. Empty or
whitespace-only input returns an empty string. Do not publish or send the result.
