# Contributing

Useful contributions are small, concrete, and easy to verify.

Good first improvements:

- Better search query patterns for Claude skills.
- More robust parsing of unusual GitHub `SKILL.md` URLs.
- Clearer false-positive filters for non-Claude `SKILL.md` files.
- Example outputs from real searches.
- Install support for more agent skill directories.

Before opening a pull request:

```bash
python3 -m py_compile scripts/find_skills.py scripts/install_skill.py
scripts/find_skills.py --keywords "invoice,receipt" --phrases "expense tracking" --no-enrich --limit 2 --repo-limit 2
scripts/install_skill.py not-a-url
```

Expected behavior:

- The Python compile step exits cleanly.
- The finder returns JSON.
- The installer rejects the invalid URL with a clear error.

Do not include private GitHub tokens, personal search results from private repositories, local machine paths, or real customer/user data in examples.
