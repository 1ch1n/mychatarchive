# Agents

## Cursor Cloud specific instructions

This is a pure Python CLI tool with no external services or build steps. Setup is simply `pip install -r requirements.txt`.

### Running tests

There is no formal test framework (e.g. pytest). Tests are run by executing the CLI against example JSON files, matching the CI workflow in `.github/workflows/test.yml`:

```bash
# Dry-run each parser
python3 src/ingest.py --in examples/example_chatgpt.json --format chatgpt --test
python3 src/ingest.py --in examples/example_anthropic.json --format anthropic --test
python3 src/ingest.py --in examples/example_grok.json --format grok --test

# Full DB creation + verification (expects 12 messages total)
python3 src/ingest.py --in examples/example_chatgpt.json --db test_output.sqlite --format chatgpt
python3 src/ingest.py --in examples/example_anthropic.json --db test_output.sqlite --format anthropic
python3 src/ingest.py --in examples/example_grok.json --db test_output.sqlite --format grok
python3 -c "import sqlite3; c=sqlite3.connect('test_output.sqlite'); assert c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==12; print('PASSED')"
```

### Notes

- No linter or formatter is configured in the repository. There are no lint commands to run.
- SQLite is embedded in Python's stdlib — no external database server is needed.
- The `--test` flag provides a dry-run mode that parses without writing to disk.
- Clean up any `.sqlite` files created during testing (they are gitignored).
